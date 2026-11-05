"""Background worker: stores what the link receives, reruns the analysis and writes export
ZIPs, so none of it waits on a click or holds up the window."""
from __future__ import annotations
import json
import os
import queue
import threading
import time
from pathlib import Path

from . import link as link_protocol
from . import marketdata as md
from . import strategy, performance
from .market import MarketStore

ANALYSE_EVERY_SECONDS = 180
EXPORT_EVERY_SECONDS = 15 * 60
KEEP_EXPORTS = 40
MIRROR_SCAN_EVERY_SECONDS = 30 * 60
LOG_MAX_BYTES = 2 * 1024 * 1024


def exports_dir(data_dir: Path) -> Path:
    return Path(data_dir) / 'exports'


def write_export(store: MarketStore, data_dir: Path, link_status: dict | None = None, now: float | None = None) -> Path:
    """A complete ZIP appears under its final name in one step, so a watcher never sees half of one."""
    folder = exports_dir(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(now if now is not None else time.time()))
    final = folder / f'forever-market-{stamp}.zip'
    temp = folder / f'.partial-{stamp}.tmp'
    store.export_bundle(temp, analysis_dir=Path(data_dir) / 'analysis', link_status=link_status)
    os.replace(temp, final)
    zips = sorted(folder.glob('forever-market-*.zip'))
    for old in zips[:-KEEP_EXPORTS]:
        old.unlink(missing_ok=True)
    (folder / 'latest.json').write_text(json.dumps({'file': final.name, 'written_at': int(time.time())}), encoding='utf-8')
    return final


class Autopilot(threading.Thread):
    def __init__(self, data_dir: Path, frames: queue.Queue | None, context):
        super().__init__(name='ft-autopilot', daemon=True)
        self.data_dir = Path(data_dir)
        self.frames = frames
        self.context = context  # called on this thread: settings and wallet as a dict
        self.results: queue.Queue = queue.Queue()
        self.messages: queue.Queue = queue.Queue()
        self.assembler = link_protocol.SnapshotAssembler()
        self._stop_event = threading.Event()
        self._analyse_now = threading.Event()
        self._export_now = threading.Event()
        self.test_ok = self.test_bad = 0

    def stop(self):
        self._stop_event.set()

    def request_analysis(self):
        self._analyse_now.set()

    def request_export(self):
        self._export_now.set()

    def _log(self, text: str):
        path = self.data_dir / 'link.log'
        try:
            if path.exists() and path.stat().st_size > LOG_MAX_BYTES:
                os.replace(path, path.with_suffix('.old.log'))
            with path.open('a', encoding='utf-8', errors='replace') as f:
                f.write(time.strftime('%Y-%m-%d %H:%M:%S ') + text + '\n')
        except OSError:
            pass

    def _mirror_scan(self, store: MarketStore, ts: int, items: dict):
        """Keeps the MARKET tab current without a /reload."""
        row = store.db.execute('SELECT MAX(observed_at) FROM scans').fetchone()
        if row and row[0] and ts - row[0] < MIRROR_SCAN_EVERY_SECONDS:
            return
        overview = store.overview()
        records = {}
        for item, rows in items.items():
            levels, _ = md.levels_of(rows)
            if levels:
                records[str(item)] = {'id': str(item), 'price': min(levels), 'quantity': sum(levels.values())}
        if records:
            store.ingest({'market': overview[2] if overview else 'PvP', 'scan_time': ts, 'items': records, 'count': len(records)}, str(ts))

    def _store_snapshot(self, store: MarketStore, snapshot: dict):
        added = md.ingest_snapshot(store.db, snapshot['ts'], snapshot['items'], snapshot['complete'], 'link')
        self._log(f'listing ts={snapshot["ts"]} items={len(snapshot["items"])} complete={snapshot["complete"]} '
                  f'chunks={snapshot["chunks_seen"]}/{snapshot["chunks_expected"]} stored={added}')
        if added:
            if snapshot['complete']:
                self._mirror_scan(store, snapshot['ts'], snapshot['items'])
            self._analyse_now.set()
            self.messages.put(f'New full listing: {len(snapshot["items"])} items')

    def _handle_frame(self, store: MarketStore, frame: link_protocol.Frame):
        now = time.time()
        try:
            if frame.type == link_protocol.TYPE_BARS:
                bucket, rows = link_protocol.parse_bars(frame.payload)
                md.ingest_bars(store.db, bucket, rows)
            elif frame.type == link_protocol.TYPE_L3:
                for snapshot in self.assembler.add_chunk(frame.payload, now):
                    self._store_snapshot(store, snapshot)
            elif frame.type == link_protocol.TYPE_L3_END:
                for snapshot in self.assembler.add_end(frame.payload, now):
                    self._store_snapshot(store, snapshot)
            elif frame.type == link_protocol.TYPE_LOG:
                message=frame.payload.decode('utf-8','replace')
                if message.startswith(performance.PREFIX):
                    try:
                        row=performance.parse_log_line(message)
                        store.ingest_snipe_metrics({'snipe_metrics':[row]})
                    except (ValueError,TypeError,OverflowError) as exc:
                        self._log('invalid sniper telemetry: '+str(exc))
                else:
                    self._log('addon: '+message)
            elif frame.type == link_protocol.TYPE_TEST:
                index, pos = link_protocol.read_varint(frame.payload, 0)
                count, _ = link_protocol.read_varint(frame.payload, pos)
                if frame.payload == link_protocol.expected_test_payload(index, count):
                    self.test_ok += 1
                else:
                    self.test_bad += 1
                if index == count:
                    self._log(f'link test: received ok={self.test_ok} bad={self.test_bad} of {count}')
        except (ValueError, IndexError, KeyError) as exc:
            self._log(f'bad frame type={frame.type}: {exc}')

    def _data_marker(self, store: MarketStore) -> tuple:
        db = store.db
        return (db.execute('SELECT MAX(ts) FROM snapshots').fetchone()[0], db.execute('SELECT MAX(ts) FROM bars').fetchone()[0],
                db.execute('SELECT COUNT(*) FROM automatic_events').fetchone()[0], db.execute('SELECT MAX(rev) FROM plan_runs').fetchone()[0],
                db.execute('SELECT MAX(rowid) FROM snipe_attempts').fetchone()[0])

    def run(self):
        store = MarketStore(self.data_dir)
        strategy.ensure_schema(store.db)
        next_analysis = time.monotonic() + 3
        next_export = time.monotonic() + EXPORT_EVERY_SECONDS
        last_marker = None
        while not self._stop_event.is_set():
            handled = False
            if self.frames is not None:
                try:
                    self._handle_frame(store, self.frames.get(timeout=0.25))
                    handled = True
                except queue.Empty:
                    pass
            else:
                self._stop_event.wait(0.25)
            if handled:
                continue
            now = time.monotonic()
            try:
                context = None
                if self._analyse_now.is_set() or now >= next_analysis:
                    self._analyse_now.clear()
                    next_analysis = now + ANALYSE_EVERY_SECONDS
                    context = self.context()
                    result = strategy.run_analysis(store.db, self.data_dir, context['cash'], context['reserve'], context['spec'],
                                                   context.get('addons_root'), link=context.get('link'))
                    if result:
                        self.results.put(result)
                forced = self._export_now.is_set()
                if forced or now >= next_export:
                    self._export_now.clear()
                    next_export = now + EXPORT_EVERY_SECONDS
                    context = context or self.context()
                    marker = self._data_marker(store)
                    if forced or (context.get('auto_export') and marker != last_marker):
                        last_marker = marker
                        path = write_export(store, self.data_dir, context.get('link'))
                        self._log(f'export {path.name}')
                        self.messages.put(f'Export written: {path.name}')
            except Exception as exc:  # the worker must outlive one bad pass
                self._log(f'worker error: {exc!r}')
                self.messages.put(f'Analysis error: {exc}')
        store.close()
