"""Size the existing WSA Hongguo window without stretching the video itself."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from dataclasses import dataclass
from pathlib import Path
import re


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
                landscape: bool, dpi: int, chrome: tuple[int, int]) -> tuple[int, int, int, int]:
    left, top, right, bottom = work
    margin = max(8, round(16 * dpi / 96))
    available_w = right - left - 2 * margin - chrome[0]
    available_h = bottom - top - 2 * margin - chrome[1]
    ratio = 16 / 9 if landscape else 9 / 16
    target_h = round((600 if landscape else 820) * dpi / 96)
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
        self.user.IsWindowVisible.argtypes = [wt.HWND]
        self.user.IsIconic.argtypes = [wt.HWND]
        self.user.IsZoomed.argtypes = [wt.HWND]
        self.user.GetWindowRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        self.user.GetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int]
        self.user.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        self.user.GetDpiForWindow.argtypes = [wt.HWND]
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
            x, y, width, height = fitted_rect(work, current, landscape, dpi, chrome)
            if current == (x, y, x+width, y+height):
                return True
            # SWP_NOZORDER | SWP_NOACTIVATE: keep focus where the user left it.
            if not self.user.SetWindowPos(hwnd, None, x, y, width, height, 0x14):
                raise OSError("调整红果窗口失败")
            return True
        finally:
            self.user.SetThreadDpiAwarenessContext(previous)
