"""Reads the data strip the Bridge addon draws at the top of the game window.

An addon cannot write files while the game runs, so market data reaches this app as
coloured pixels. This only reads the screen: it does not touch the game's process.

wow_addon/ForeverTraderBridge/Link.lua is the matching encoder; change them together.
"""
from __future__ import annotations
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

BLOCK_PX = 3
BLOCKS = 256
RAMP = 8
DATA_BLOCKS = BLOCKS - RAMP
STRIP_WIDTH = BLOCK_PX * BLOCKS
STRIP_HEIGHT = BLOCK_PX
MAX_BYTES = DATA_BLOCKS * 9 // 8
OVERHEAD = 7
MAX_PAYLOAD = MAX_BYTES - OVERHEAD
VERSION = 1

TYPE_STATE, TYPE_BARS, TYPE_L3, TYPE_LOG, TYPE_L3_END, TYPE_TEST = range(6)

PHASE_NAMES = ('IDLE', 'PREPARE', 'READY', 'QUOTING', 'CONFIRMING', 'BUYING', 'DONE', 'LOST')
REASON_NAMES = {1: 'vendor', 2: 'usual', 3: 'plan'}

_MIN_RAMP_STEP = 12


@dataclass
class Frame:
    type: int
    seq: int
    payload: bytes


def checksum(data: bytes) -> tuple[int, int]:
    s1 = s2 = 0
    for b in data:
        s1 = (s1 + b) % 251
        s2 = (s2 + s1) % 251
    return s1, s2


def parse_frame_bytes(raw: bytes) -> Optional[Frame]:
    """Validate one frame's bytes. A torn or misread frame returns None."""
    if len(raw) < OVERHEAD or raw[0] >> 4 != VERSION:
        return None
    length = raw[2] | raw[3] << 8
    if length > MAX_PAYLOAD or 7 + length > len(raw):
        return None
    s1, s2 = checksum(raw[:4 + length])
    if raw[4 + length] != s1 or raw[5 + length] != s2 or raw[6 + length] != raw[1]:
        return None
    return Frame(raw[0] & 15, raw[1], bytes(raw[4:4 + length]))


def _channel_lut(samples: list[int]) -> Optional[list[int]]:
    """Map 0-255 to a level 0-7 using the ramp, or None if there is no ramp here."""
    if samples[0] > 60 or samples[7] < 195:
        return None
    for a, b in zip(samples, samples[1:]):
        if b - a < _MIN_RAMP_STEP:
            return None
    lut, level = [0] * 256, 0
    for value in range(256):
        while level < 7 and value > (samples[level] + samples[level + 1]) / 2:
            level += 1
        lut[value] = level
    return lut


class StripDecoder:
    """Decodes a BGRA capture of the strip. The ramp LUTs are cached between frames."""

    def __init__(self):
        self._ramp_key = None
        self._luts = None

    def decode(self, bgra: bytes, width: int = STRIP_WIDTH, row: int = 1) -> tuple[Optional[Frame], bool]:
        """Returns (frame or None, whether a calibration ramp was visible)."""
        base = row * width * 4
        if len(bgra) < base + width * 4:
            return None, False
        ramp_key = bytes(bgra[base + (BLOCK_PX * i + 1) * 4 + c] for i in range(RAMP) for c in range(3))
        if ramp_key != self._ramp_key:
            luts = []
            for c in range(3):
                lut = _channel_lut([ramp_key[i * 3 + c] for i in range(RAMP)])
                if lut is None:
                    self._ramp_key, self._luts = None, None
                    return None, False
                luts.append(lut)
            self._ramp_key, self._luts = ramp_key, luts
        lut_b, lut_g, lut_r = self._luts
        value = 0
        offset = base + (BLOCK_PX * RAMP + 1) * 4
        step = BLOCK_PX * 4
        for _ in range(DATA_BLOCKS):
            value = (value << 9) | lut_r[bgra[offset + 2]] << 6 | lut_g[bgra[offset + 1]] << 3 | lut_b[bgra[offset]]
            offset += step
        return parse_frame_bytes(value.to_bytes(MAX_BYTES, 'big')), True


def read_varint(data: bytes, pos: int) -> tuple[int, int]:
    value, shift = 0, 0
    while True:
        if pos >= len(data):
            raise ValueError('truncated varint')
        b = data[pos]
        pos += 1
        value |= (b & 127) << shift
        if b < 128:
            return value, pos
        shift += 7
        if shift > 70:
            raise ValueError('varint too long')


def parse_state(payload: bytes) -> dict:
    """Field order matches NS.BuildState in Main.lua."""
    values, pos = [], 0
    while pos < len(payload):
        v, pos = read_varint(payload, pos)
        values.append(v)
    if len(values) < 12:
        raise ValueError('short state frame')
    flags = values[1]
    phase = values[3]
    return {
        'server_time': values[0], 'ah_open': bool(flags & 1), 'aux_attached': bool(flags & 2),
        'sniper_running': bool(flags & 4), 'auto_confirm': bool(flags & 8), 'plan_loaded': bool(flags & 16),
        'commodity': bool(flags & 32), 'sniper_tab': bool(flags & 64), 'money': values[2], 'phase': phase,
        'phase_name': PHASE_NAMES[phase] if phase < len(PHASE_NAMES) else str(phase), 'item_id': values[4],
        'units': values[5], 'cost': values[6], 'profit': values[7], 'plan_rev': values[8], 'rounds': values[9],
        'last_listing': values[10], 'queue': values[11],
        # Added in 0.4.0's first live session; older addons send 12 fields.
        'reason': REASON_NAMES.get(values[12], '') if len(values) > 12 else '',
        'percent': values[13] if len(values) > 13 else 0,
    }


def parse_bars(payload: bytes) -> tuple[int, list[tuple]]:
    bucket, pos = read_varint(payload, 0)
    rows = []
    while pos < len(payload):
        row = []
        for _ in range(8):
            v, pos = read_varint(payload, pos)
            row.append(v)
        rows.append(tuple(row))
    return bucket, rows


def parse_l3(payload: bytes) -> tuple[int, int, dict[int, list[tuple[int, int, int]]]]:
    """Returns (snapshot ts, chunk index, {item: [(count, buyout, band), ...]})."""
    ts, pos = read_varint(payload, 0)
    index, pos = read_varint(payload, pos)
    items: dict[int, list[tuple[int, int, int]]] = {}
    while pos < len(payload):
        item, pos = read_varint(payload, pos)
        rows = items.setdefault(item, [])
        while True:
            packed, pos = read_varint(payload, pos)
            if packed == 0:
                break
            buyout, pos = read_varint(payload, pos)
            rows.append((packed >> 3, buyout, packed & 7))
    return ts, index, items


def parse_l3_end(payload: bytes) -> tuple[int, int, int]:
    ts, pos = read_varint(payload, 0)
    chunks, pos = read_varint(payload, pos)
    rows, pos = read_varint(payload, pos)
    return ts, chunks, rows


def expected_test_payload(index: int, count: int) -> bytes:
    """What Link.TestBurst sends for frame number `index` of `count`."""
    head = bytearray()
    for value in (index, count):
        while value >= 128:
            head.append(value % 128 + 128)
            value //= 128
        head.append(value)
    k = len(head) + 1
    body = bytearray(head)
    while k <= MAX_PAYLOAD:
        body.append((index * 7 + k * 13) % 256)
        k += 1
    return bytes(body)


@dataclass
class _Pending:
    ts: int
    chunks: dict = field(default_factory=dict)
    first_seen: float = 0.0
    expected: Optional[int] = None
    expected_rows: Optional[int] = None


class SnapshotAssembler:
    """Collects full-listing chunks. A lost chunk leaves the snapshot partial: those
    items are unknown, which must never be read as every one of them having sold."""

    STALE_SECONDS = 600

    def __init__(self):
        self.pending: dict[int, _Pending] = {}

    def add_chunk(self, payload: bytes, now: float) -> list[dict]:
        ts, index, items = parse_l3(payload)
        entry = self.pending.setdefault(ts, _Pending(ts, first_seen=now))
        entry.chunks[index] = items
        return self._finish_stale(now, exclude=ts)

    def add_end(self, payload: bytes, now: float) -> list[dict]:
        ts, chunks, rows = parse_l3_end(payload)
        entry = self.pending.setdefault(ts, _Pending(ts, first_seen=now))
        entry.expected, entry.expected_rows = chunks, rows
        done = [self._build(self.pending.pop(ts))]
        return done + self._finish_stale(now)

    def _finish_stale(self, now: float, exclude: Optional[int] = None) -> list[dict]:
        out = []
        for ts in list(self.pending):
            if ts != exclude and now - self.pending[ts].first_seen > self.STALE_SECONDS:
                out.append(self._build(self.pending.pop(ts)))
        return out

    @staticmethod
    def _build(entry: _Pending) -> dict:
        items: dict[int, list] = {}
        for chunk in entry.chunks.values():
            for item, rows in chunk.items():
                items.setdefault(item, []).extend(rows)
        complete = entry.expected is not None and len(entry.chunks) == entry.expected
        if not complete and entry.chunks:
            # A big item's auctions run on across chunks, so the items either side of a lost
            # chunk may have lost some of their rows. Partial rows would read as units sold,
            # so those items are dropped: unknown, like everything in the lost chunk.
            last = (entry.expected - 1) if entry.expected else max(entry.chunks) + 1
            for index in range(last + 1):
                if index in entry.chunks:
                    continue
                before, after = entry.chunks.get(index - 1), entry.chunks.get(index + 1)
                if before:
                    items.pop(next(reversed(before)), None)
                if after:
                    items.pop(next(iter(after)), None)
        return {'ts': entry.ts, 'items': items, 'complete': complete,
                'chunks_seen': len(entry.chunks), 'chunks_expected': entry.expected}


class LinkStats:
    def __init__(self):
        self.frames = self.bad = self.no_ramp = self.gaps = self.test_ok = self.test_bad = 0
        self.last_frame_at = 0.0
        self.window_found = False
        self.mode = ''
        self.last_seq: Optional[int] = None

    def as_dict(self) -> dict:
        return {'frames': self.frames, 'bad': self.bad, 'no_ramp': self.no_ramp, 'gaps': self.gaps,
                'test_ok': self.test_ok, 'test_bad': self.test_bad, 'last_frame_at': self.last_frame_at,
                'window_found': self.window_found, 'mode': self.mode}


class Win32Capture:
    """BitBlt of the strip, preferring the screen and falling back to the window DC."""

    MODES = ('screen', 'window')

    def __init__(self):
        import ctypes
        from ctypes import wintypes as w
        self.ctypes, self.w = ctypes, w
        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32
        self.kernel32 = ctypes.windll.kernel32
        for fn, res, args in (
            ('GetDC', w.HDC, [w.HWND]), ('ReleaseDC', ctypes.c_int, [w.HWND, w.HDC]),
            ('GetForegroundWindow', w.HWND, []), ('IsWindow', w.BOOL, [w.HWND]), ('IsIconic', w.BOOL, [w.HWND]),
            ('GetClientRect', w.BOOL, [w.HWND, ctypes.POINTER(w.RECT)]),
            ('ClientToScreen', w.BOOL, [w.HWND, ctypes.POINTER(w.POINT)]),
        ):
            f = getattr(self.user32, fn)
            f.restype, f.argtypes = res, args
        self.gdi32.CreateCompatibleDC.restype = w.HDC
        self.gdi32.CreateCompatibleDC.argtypes = [w.HDC]
        self.gdi32.SelectObject.restype = w.HGDIOBJ
        self.gdi32.SelectObject.argtypes = [w.HDC, w.HGDIOBJ]
        self.gdi32.BitBlt.restype = w.BOOL
        self.gdi32.BitBlt.argtypes = [w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HDC,
                                      ctypes.c_int, ctypes.c_int, w.DWORD]
        self.gdi32.CreateDIBSection.restype = w.HBITMAP
        self.gdi32.CreateDIBSection.argtypes = [w.HDC, ctypes.c_void_p, w.UINT, ctypes.POINTER(ctypes.c_void_p),
                                                w.HANDLE, w.DWORD]
        self.hwnd = None
        self.mode = 'screen'
        self._mem_dc = None
        self._bits = ctypes.c_void_p()
        self._screen_dc = self.user32.GetDC(None)
        self._make_buffer()

    def _make_buffer(self):
        ctypes, w = self.ctypes, self.w

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [('biSize', w.DWORD), ('biWidth', w.LONG), ('biHeight', w.LONG), ('biPlanes', w.WORD),
                        ('biBitCount', w.WORD), ('biCompression', w.DWORD), ('biSizeImage', w.DWORD),
                        ('biXPelsPerMeter', w.LONG), ('biYPelsPerMeter', w.LONG), ('biClrUsed', w.DWORD),
                        ('biClrImportant', w.DWORD)]
        header = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), STRIP_WIDTH, -STRIP_HEIGHT, 1, 32, 0, 0, 0, 0, 0, 0)
        self._mem_dc = self.gdi32.CreateCompatibleDC(self._screen_dc)
        bitmap = self.gdi32.CreateDIBSection(self._mem_dc, ctypes.byref(header), 0, ctypes.byref(self._bits), None, 0)
        self.gdi32.SelectObject(self._mem_dc, bitmap)

    def find_window(self) -> bool:
        if self.hwnd and self.user32.IsWindow(self.hwnd):
            return True
        self.hwnd = find_wow_window()
        return self.hwnd is not None

    def next_mode(self):
        self.mode = self.MODES[(self.MODES.index(self.mode) + 1) % len(self.MODES)]

    def grab(self) -> Optional[bytes]:
        if not self.hwnd or self.user32.IsIconic(self.hwnd):
            return None
        SRCCOPY = 0x00CC0020
        if self.mode == 'screen':
            point = self.w.POINT(0, 0)
            if not self.user32.ClientToScreen(self.hwnd, self.ctypes.byref(point)):
                return None
            ok = self.gdi32.BitBlt(self._mem_dc, 0, 0, STRIP_WIDTH, STRIP_HEIGHT, self._screen_dc, point.x, point.y, SRCCOPY)
        else:
            dc = self.user32.GetDC(self.hwnd)
            if not dc:
                return None
            ok = self.gdi32.BitBlt(self._mem_dc, 0, 0, STRIP_WIDTH, STRIP_HEIGHT, dc, 0, 0, SRCCOPY)
            self.user32.ReleaseDC(self.hwnd, dc)
        if not ok:
            return None
        return self.ctypes.string_at(self._bits, STRIP_WIDTH * STRIP_HEIGHT * 4)


def set_dpi_aware():
    """Coordinates must be physical pixels whatever the Windows display scale is."""
    if sys.platform != 'win32':
        return
    import ctypes
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass


def find_wow_window() -> Optional[int]:
    """The game's top-level window, recognised by Blizzard's window class."""
    if sys.platform != 'win32':
        return None
    import ctypes
    from ctypes import wintypes as w
    user32 = ctypes.windll.user32
    user32.FindWindowW.restype = w.HWND
    user32.FindWindowW.argtypes = [w.LPCWSTR, w.LPCWSTR]
    return user32.FindWindowW('waApplication Window', None) or None


class LinkReceiver(threading.Thread):
    """Background reader. Decoded frames go on `frames`; only this thread does capture."""

    POLL_SECONDS = 1 / 45
    MODE_SWITCH_SECONDS = 2.0
    FIND_WINDOW_SECONDS = 2.0

    def __init__(self):
        super().__init__(name='ft-link', daemon=True)
        self.frames: queue.Queue[Frame] = queue.Queue(maxsize=20000)
        self.states: queue.Queue[dict] = queue.Queue(maxsize=200)
        self.stats = LinkStats()
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def _route(self, frame: Frame):
        try:
            if frame.type == TYPE_STATE:
                self.states.put_nowait(parse_state(frame.payload))
            else:
                self.frames.put_nowait(frame)
        except (queue.Full, ValueError):
            pass

    def run(self):
        if sys.platform != 'win32':
            return
        set_dpi_aware()
        capture = Win32Capture()
        decoder = StripDecoder()
        stats = self.stats
        last_raw = None
        next_find = 0.0
        ramp_seen_at = time.monotonic()
        while not self._stop_event.is_set():
            now = time.monotonic()
            if now >= next_find:
                next_find = now + self.FIND_WINDOW_SECONDS
                stats.window_found = capture.find_window()
            raw = capture.grab() if stats.window_found else None
            if raw is not None and raw != last_raw:
                last_raw = raw
                frame, ramp = decoder.decode(raw)
                if ramp:
                    ramp_seen_at = now
                else:
                    stats.no_ramp += 1
                if frame is None:
                    if ramp:
                        stats.bad += 1
                elif frame.seq != stats.last_seq:
                    if stats.last_seq is not None and frame.seq != (stats.last_seq + 1) % 256:
                        stats.gaps += 1
                    stats.last_seq = frame.seq
                    stats.frames += 1
                    stats.last_frame_at = time.time()
                    stats.mode = capture.mode
                    self._route(frame)
            if now - ramp_seen_at > self.MODE_SWITCH_SECONDS:
                ramp_seen_at = now
                capture.next_mode()
            self._stop_event.wait(self.POLL_SECONDS)
