"""Size the existing WSA Hongguo window without stretching the video itself."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from dataclasses import dataclass
from pathlib import Path
import re
import time


@dataclass(frozen=True)
class PlayerLayout:
    landscape: bool
    width: int
    height: int


def player_layout(output: str) -> PlayerLayout | None:
    for part in re.split(r"(?m)^  Window #", output):
        header = part.partition("\n")[0]
        if " u0 com.phoenix.read/" not in header or "isVisible=true" not in part:
            continue
        size = re.search(r"Requested w=(\d+) h=(\d+)", part)
        if size and "mDrawState=HAS_DRAWN" in part:
            # The normal feed supports freeform sizing too. Its current width
            # cannot tell us whether the user entered the landscape player.
            return PlayerLayout(".fullscreen.ShortSeriesLandActivity" in header,
                                int(size[1]), int(size[2]))
    return None


def fitted_rect(work: tuple[int, int, int, int], current: tuple[int, int, int, int],
                landscape: bool, dpi: int, chrome: tuple[int, int],
                height_dip: int | None = None) -> tuple[int, int, int, int]:
    left, top, right, bottom = work
    margin = max(8, round(16 * dpi / 96))
    available_w = right - left - 2 * margin - chrome[0]
    available_h = bottom - top - 2 * margin - chrome[1]
    ratio = 16 / 9 if landscape else 9 / 16
    target_h = round((height_dip if height_dip is not None else 600 if landscape else 820) * dpi / 96)
    height = max(1, min(target_h, available_h, int(available_w / ratio)))
    width = round(height * ratio) + chrome[0]
    height += chrome[1]
    x = round((current[0] + current[2] - width) / 2)
    y = round((current[1] + current[3] - height) / 2)
    x = max(left + margin, min(x, right - width - margin))
    y = max(top + margin, min(y, bottom - height - margin))
    return x, y, width, height


class WsaPlayerWindow:
    def __init__(self):
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.callback_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        self.user.EnumWindows.argtypes = [self.callback_type, wt.LPARAM]
        self.user.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        self.user.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        self.user.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        self.user.IsWindowVisible.argtypes = [wt.HWND]
        self.user.IsIconic.argtypes = [wt.HWND]
        self.user.IsZoomed.argtypes = [wt.HWND]
        self.user.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        self.user.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
        self.user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user.GetDpiForWindow.argtypes = [wt.HWND]
        self.user.GetPropW.argtypes = [wt.HWND, wt.LPCWSTR]
        self.user.GetPropW.restype = ctypes.c_size_t
        self.user.MonitorFromWindow.argtypes = [wt.HWND, wt.DWORD]
        self.user.MonitorFromWindow.restype = wt.HANDLE
        self.user.GetMonitorInfoW.argtypes = [wt.HANDLE, ctypes.c_void_p]
        self.user.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, wt.UINT]
        self.user.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        self.user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        self.user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        self.kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self.kernel.OpenProcess.restype = wt.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR,
                                                          ctypes.POINTER(wt.DWORD)]
        self.kernel.CloseHandle.argtypes = [wt.HANDLE]

    def finish_tap_control(self):
        """Restore the pointer after the app acknowledged or canceled its click."""
        pending = getattr(self, "_pending_tap_pointer", None)
        self._pending_tap_pointer = None
        if not pending:
            return
        hwnd, old_x, old_y, tap_x, tap_y = pending
        u = self.user
        previous = u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        try:
            cursor = wt.POINT()
            if (u.GetCursorPos(ctypes.byref(cursor)) and (cursor.x,cursor.y) == (tap_x,tap_y)
                    and u.GetAncestor(u.GetForegroundWindow(),2) == hwnd):
                u.SetCursorPos(old_x,old_y)
        finally:
            if previous:
                u.SetThreadDpiAwarenessContext(previous)

    def tap_control(self, hwnd, root, bounds, *, defer_restore=False):
        """Activate one verified button through WSA's own mouse mapping."""
        if not hwnd or len(root) != 4 or len(bounds) != 4:
            return False
        left, top, right, bottom = root
        x1, y1, x2, y2 = bounds
        x, y = (x1+x2)/2, (y1+y2)/2
        if (right <= left or bottom <= top or x2 <= x1 or y2 <= y1
                or not (left <= x < right and top <= y < bottom)
                or (x2-x1)*(y2-y1) > (right-left)*(bottom-top)*.2):
            return False
        u = self.user
        u.GetForegroundWindow.restype = wt.HWND
        u.GetAncestor.argtypes = [wt.HWND, wt.UINT]
        u.GetAncestor.restype = wt.HWND
        u.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        u.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
        u.WindowFromPoint.argtypes = [wt.POINT]
        u.WindowFromPoint.restype = wt.HWND
        u.GetCursorPos.argtypes = [ctypes.POINTER(wt.POINT)]
        u.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        u.mouse_event.argtypes = [wt.DWORD, wt.DWORD, wt.DWORD, wt.DWORD, ctypes.c_size_t]
        if (u.GetAncestor(u.GetForegroundWindow(), 2) != hwnd or u.IsIconic(hwnd)
                or any(u.GetAsyncKeyState(key)&0x8000 for key in (0x10, 0x11, 0x12))):
            return False
        previous_dpi = u.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        original = wt.POINT()
        point = wt.POINT()
        moved = False
        try:
            rect = wt.RECT()
            if not u.GetClientRect(hwnd, ctypes.byref(rect)) or rect.right <= 0 or rect.bottom <= 0:
                return False
            point.x, point.y = round((x-left)/(right-left)*rect.right), round((y-top)/(bottom-top)*rect.bottom)
            if not u.ClientToScreen(hwnd, ctypes.byref(point)) or not u.GetCursorPos(ctypes.byref(original)):
                return False
            # Never activate another app, an occluding window, or a stale
            # fullscreen gesture after the user has changed focus.
            if (u.GetAncestor(u.WindowFromPoint(point), 2) != hwnd
                    or u.GetAncestor(u.GetForegroundWindow(), 2) != hwnd):
                return False
            if not u.SetCursorPos(point.x, point.y):
                return False
            moved = True
            # WSA needs a real move packet before the button packet. Restoring
            # the pointer before its queued release is handled cancels clicks.
            vx, vy = u.GetSystemMetrics(76), u.GetSystemMetrics(77)
            vw, vh = u.GetSystemMetrics(78), u.GetSystemMetrics(79)
            if vw <= 1 or vh <= 1:
                return False
            nx, ny = round((point.x-vx)*65535/(vw-1)), round((point.y-vy)*65535/(vh-1))
            flags = 0x8000 | 0x4000
            u.mouse_event(1 | flags, nx, ny, 0, 0)
            time.sleep(.05)
            cursor = wt.POINT()
            if (u.GetAncestor(u.GetForegroundWindow(), 2) != hwnd
                    or not u.GetCursorPos(ctypes.byref(cursor))
                    or (cursor.x, cursor.y) != (point.x, point.y)
                    or u.GetAncestor(u.WindowFromPoint(cursor), 2) != hwnd):
                return False
            u.mouse_event(2 | flags, nx, ny, 0, 0)
            try:
                time.sleep(.04)
            finally:
                u.mouse_event(4 | flags, nx, ny, 0, 0)
            time.sleep(.1)
            if defer_restore:
                # WSA processes input asynchronously. Moving away after a
                # fixed delay can cancel a queued release on a busy frame.
                # The app observer completes restoration after its outcome.
                self._pending_tap_pointer = (hwnd,original.x,original.y,point.x,point.y)
                moved = False
            return True
        finally:
            cursor = wt.POINT()
            if moved and u.GetCursorPos(ctypes.byref(cursor)) and (cursor.x, cursor.y) == (point.x, point.y):
                u.SetCursorPos(original.x, original.y)
            if previous_dpi:
                u.SetThreadDpiAwarenessContext(previous_dpi)

    def find(self) -> int | None:
        found = []

        @self.callback_type
        def visit(hwnd, _):
            if not self.user.IsWindowVisible(hwnd):
                return True
            title = ctypes.create_unicode_buffer(512)
            self.user.GetWindowTextW(hwnd, title, len(title))
            if title.value not in {"红果免费短剧", "红果短剧"}:
                return True
            window_class = ctypes.create_unicode_buffer(256)
            self.user.GetClassNameW(hwnd, window_class, len(window_class))
            if window_class.value != "com.phoenix.read":
                # WSA can keep a same-title '(splash)' window after the app has
                # drawn. Never select that loading window as the player.
                return True
            pid = wt.DWORD()
            self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            process = self.kernel.OpenProcess(0x1000, False, pid.value)
            if process:
                try:
                    path = ctypes.create_unicode_buffer(32768)
                    length = wt.DWORD(len(path))
                    if self.kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(length)):
                        if Path(path.value).name.lower() == "wsaclient.exe":
                            found.append(hwnd)
                finally:
                    self.kernel.CloseHandle(process)
            return True

        self.user.EnumWindows(visit, 0)
        return found[0] if found else None

    def hide_loaded_splashes(self, real=None):
        """Hide only this app's stale WSA loader after its UI is confirmed."""
        real = real or self.find()
        if not real:
            return
        owner = wt.DWORD()
        self.user.GetWindowThreadProcessId(real, ctypes.byref(owner))

        @self.callback_type
        def visit(hwnd, _):
            if not self.user.IsWindowVisible(hwnd):
                return True
            window_class = ctypes.create_unicode_buffer(256)
            self.user.GetClassNameW(hwnd, window_class, len(window_class))
            if window_class.value == "com.phoenix.read(splash)":
                pid = wt.DWORD()
                self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value == owner.value:
                    # Closing the loader could cancel a WSA activation. Hide
                    # it without stopping or sending messages to the app.
                    self.user.ShowWindow(hwnd, 0)
            return True

        self.user.EnumWindows(visit, 0)

    def resize(self, hwnd: int, layout: PlayerLayout, landscape: bool, manual: bool = False) -> bool:
        # Background adaptation must not restore a minimized window or undo a
        # user's maximization. Explicit buttons are allowed to restore it.
        if not manual and (self.user.IsIconic(hwnd) or self.user.IsZoomed(hwnd)):
            return False
        previous = self.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        try:
            if manual:
                self.user.ShowWindow(hwnd, 9)
            rect = wt.RECT()
            if not self.user.GetWindowRect(hwnd, ctypes.byref(rect)):
                raise OSError("读取红果窗口失败")
            current = (rect.left, rect.top, rect.right, rect.bottom)
            dpi = self.user.GetDpiForWindow(hwnd) or 96
            chrome = (max(0, min(round(40 * dpi / 96), rect.right - rect.left - layout.width)),
                      max(0, min(round(80 * dpi / 96), rect.bottom - rect.top - layout.height)))
            if not self.user.GetWindowLongPtrW(hwnd, -16) & 0xC00000:  # WS_CAPTION
                # WSA's borderless presentation fills the whole native window.
                # Android can still report the previous size during a manual
                # orientation switch; that difference is not a title/frame.
                chrome = (0, 0)

            class MonitorInfo(ctypes.Structure):
                _fields_ = [("cbSize", wt.DWORD), ("monitor", wt.RECT), ("work", wt.RECT), ("flags", wt.DWORD)]

            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(info)
            monitor = self.user.MonitorFromWindow(hwnd, 2)
            if not self.user.GetMonitorInfoW(monitor, ctypes.byref(info)):
                raise OSError("读取屏幕范围失败")
            work = (info.work.left, info.work.top, info.work.right, info.work.bottom)
            height_dip = self.user.GetPropW(hwnd, "HongguoLandscapeHeightDip" if landscape else "HongguoPortraitHeightDip")
            if not isinstance(height_dip, int) or not 120 <= height_dip <= 4000:
                height_dip = None
            x, y, width, height = fitted_rect(work, current, landscape, dpi, chrome, height_dip)
            if current == (x, y, x+width, y+height):
                return True
            # SWP_NOZORDER | SWP_NOACTIVATE: keep focus where the user left it.
            if not self.user.SetWindowPos(hwnd, None, x, y, width, height, 0x14):
                raise OSError("调整红果窗口失败")
            return True
        finally:
            self.user.SetThreadDpiAwarenessContext(previous)
