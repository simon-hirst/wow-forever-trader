"""The Windows Confirm button: for when your cursor is outside the game.

It relays clicks one for one. Each click on this button sends one key press to WoW,
which the addon treats like a click on its in-game Confirm button. There is no repeat,
timer or queue here: no click, no key press.
"""
from __future__ import annotations
import sys
import tkinter as tk

from .link import find_wow_window

# VK_DIVIDE, the numpad's divide key; matches RELAY_KEY in the addon's Main.lua. The game
# ignores F13-F24 (measured live), but takes a posted numpad divide.
RELAY_VK = 0x6F
EXTENDED_KEY = 1 << 24  # numpad divide is an extended key
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE = 0x00000008, 0x00000080, 0x08000000
RESULT_MS = 1200

GREEN, AMBER, BLUE, RED = '#0c8a1f', '#7a6a0d', '#1a668c', '#8c1f1a'


def send_relay_press(hwnd: int) -> bool:
    """Exactly one key press to one window. Posted, so WoW does not need focus."""
    if sys.platform != 'win32' or not hwnd:
        return False
    import ctypes
    from ctypes import wintypes as w
    user32 = ctypes.windll.user32
    user32.PostMessageW.restype = w.BOOL
    user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user32.MapVirtualKeyW.restype = w.UINT
    user32.MapVirtualKeyW.argtypes = [w.UINT, w.UINT]
    scan = user32.MapVirtualKeyW(RELAY_VK, 0)
    down = user32.PostMessageW(hwnd, WM_KEYDOWN, RELAY_VK, 1 | scan << 16 | EXTENDED_KEY)
    up = user32.PostMessageW(hwnd, WM_KEYUP, RELAY_VK, 1 | scan << 16 | EXTENDED_KEY | 1 << 30 | 1 << 31)
    return bool(down and up)


def reason_text(state: dict) -> str:
    """Why the addon offers the deal, as the in-game button says it."""
    reason, pct = state.get('reason'), state.get('percent') or 0
    if reason == 'vendor':
        return 'below vendor'
    if reason == 'usual':
        return f'{pct}% of usual'
    if reason == 'plan':
        return f'resale bet, {pct}% of fair' if pct else 'resale bet'
    return ''


def cursor_position() -> tuple[int, int]:
    import ctypes
    from ctypes import wintypes as w
    point = w.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def cursor_in_foreground_game(hwnd: int) -> bool:
    """True when the addon's own button is already under the cursor and clickable."""
    if sys.platform != 'win32' or not hwnd:
        return False
    import ctypes
    from ctypes import wintypes as w
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = w.HWND
    if user32.GetForegroundWindow() != hwnd:
        return False
    user32.GetWindowRect.argtypes = [w.HWND, ctypes.POINTER(w.RECT)]
    rect = w.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    x, y = cursor_position()
    return rect.left <= x < rect.right and rect.top <= y < rect.bottom


class ConfirmOverlay:
    WIDTH, HEIGHT = 420, 58

    def __init__(self, root: tk.Tk, status=None):
        self.status = status or (lambda _text: None)
        self.enabled = True
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes('-topmost', True)
        self.win.configure(bg='white', padx=2, pady=2)
        self.body = tk.Frame(self.win, bg=GREEN, cursor='hand2')
        self.body.pack(fill='both', expand=True)
        self.title = tk.Label(self.body, text='CONFIRM', font=('Segoe UI Semibold', 13), fg='white', bg=GREEN)
        self.title.pack(pady=(4, 0))
        self.detail = tk.Label(self.body, text='', font=('Segoe UI', 10), fg='white', bg=GREEN)
        self.detail.pack(pady=(0, 4))
        for widget in (self.win, self.body, self.title, self.detail):
            widget.bind('<ButtonRelease-1>', self._on_click)
            widget.bind('<ButtonRelease-3>', self._on_dismiss)
        self.deal_key = None
        self.dismissed_key = None
        self.pressable = False
        self.sent = False
        self._hide_job = None
        self._styled = False

    def _style_window(self):
        """Clicking must not take focus away from whatever you are doing."""
        if self._styled or sys.platform != 'win32':
            return
        self._styled = True
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
        user32.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        hwnds = {self.win.winfo_id()}
        try:
            hwnds.add(int(self.win.frame(), 16))
        except (tk.TclError, ValueError):
            pass
        for hwnd in hwnds:
            style = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
            user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST)

    def _paint(self, color: str, title: str, detail: str):
        for widget in (self.body, self.title, self.detail):
            widget.configure(bg=color)
        self.title.configure(text=title)
        self.detail.configure(text=detail)

    def _show_at_cursor(self):
        x, y = cursor_position()
        left = max(0, x - self.WIDTH // 2)
        top = max(0, y - self.HEIGHT // 2)
        self.win.geometry(f'{self.WIDTH}x{self.HEIGHT}+{left}+{top}')
        self.win.deiconify()
        self.win.update_idletasks()
        self._style_window()
        self.win.attributes('-topmost', True)

    def _hide(self):
        self.win.withdraw()
        self.deal_key = None
        self.pressable = False
        self.sent = False

    def _cancel_hide(self):
        if self._hide_job is not None:
            self.win.after_cancel(self._hide_job)
            self._hide_job = None

    def _on_click(self, _event=None):
        if not self.pressable:
            return
        # One click, one key press. Further clicks do nothing until the game moves on.
        hwnd = find_wow_window()
        if send_relay_press(hwnd):
            self.pressable = False
            self.sent = True
            self._paint(AMBER, 'Sent to WoW...', self.detail.cget('text'))
        else:
            self.status('Could not reach the WoW window')

    def _on_dismiss(self, _event=None):
        self.dismissed_key = self.deal_key
        self._hide()

    def update(self, state: dict, name: str, money_text):
        """Called for each state frame from the addon."""
        if not self.enabled or not state:
            self._cancel_hide()
            self._hide()
            return
        phase = state.get('phase_name')
        key = (state.get('item_id'), state.get('units'), state.get('cost'))
        if phase == 'READY' or (phase == 'CONFIRMING' and not state.get('auto_confirm')):
            if key == self.dismissed_key or cursor_in_foreground_game(find_wow_window()):
                # The addon's own button is right there; two would just get in the way.
                self._cancel_hide()
                self._hide()
                return
            self._cancel_hide()
            detail = f'{state["units"]} x {name}  {money_text(state["cost"])}  (+{money_text(state["profit"])})'
            why = reason_text(state)
            if why:
                detail = f'{why}  |  {detail}'
            if key != self.deal_key or not self.win.winfo_viewable():
                self.deal_key = key
                # A below-vendor deal is a sure profit; a bet must not look the same.
                vendor = state.get('reason') in ('vendor', '')
                self._paint(GREEN if vendor else BLUE, 'CONFIRM' if vendor else 'CONFIRM BET', detail)
                self.pressable = True
                self.sent = False
                self._show_at_cursor()
            elif not self.sent:
                self.pressable = True
        elif phase in ('QUOTING', 'BUYING'):
            if self.deal_key is not None:
                self._cancel_hide()
                self.pressable = False
                self._paint(AMBER, 'Buying...', self.detail.cget('text'))
        elif phase in ('DONE', 'LOST'):
            if self.deal_key is not None and self._hide_job is None:
                bought = phase == 'DONE'
                self.pressable = False
                self._paint(BLUE if bought else RED, 'BOUGHT' if bought else 'LOST', self.detail.cget('text'))
                self._hide_job = self.win.after(RESULT_MS, self._finish_result)
        elif self._hide_job is None:
            self._hide()

    def _finish_result(self):
        self._hide_job = None
        self._hide()
