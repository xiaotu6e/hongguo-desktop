"""Keep WSA mouse input available independently of its optional title hiding."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .android import NO_WINDOW, OperationError


def caption_dip(client_width, client_height, content_width, content_height, dpi):
    """Calibrate only from a full activity matching the native client width."""
    delta = client_height - content_height
    value = round(delta * 96 / dpi) if dpi else 0
    return value if abs(client_width-content_width) <= 2 and 16 <= value <= 64 else 30


class WindowChrome:
    def __init__(self, window):
        self.window = window
        self.process = None
        self.hwnd = None
        self.stop_event = None
        self.hide_titlebar = None
        self.geometry_holding = False
        self.input_enabled = False
        self.heartbeat_thread = None
        self.heartbeat_stop = None
        self._grip = None
        self._fullscreen_target_key = None
        self._fullscreen_target_sent_at = 0.0
        self._fullscreen_presentation_key = None
        self._lifecycle_lock = threading.RLock()
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
        self.kernel.CreateEventW.restype = wt.HANDLE
        self.kernel.SetEvent.argtypes = [wt.HANDLE]
        self.kernel.CloseHandle.argtypes = [wt.HANDLE]
        self.kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        self.input_event_name = "Local\\HongguoInput_" + uuid.uuid4().hex
        self.input_event = self.kernel.CreateEventW(None, False, False, self.input_event_name)
        if not self.input_event:
            raise ctypes.WinError(ctypes.get_last_error())
        self.window.user.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        self.window.user.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        self.window.user.FindWindowW.argtypes = [wt.LPCWSTR, wt.LPCWSTR]
        self.window.user.FindWindowW.restype = wt.HWND
        self.window.user.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        self.window.user.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM), wt.LPARAM]
        self.window.user.GetWindow.argtypes = [wt.HWND, wt.UINT]
        self.window.user.GetWindow.restype = wt.HWND
        self.window.user.IsWindow.argtypes = [wt.HWND]
        self.window.user.GetPropW.argtypes = [wt.HWND, wt.LPCWSTR]
        self.window.user.GetPropW.restype = ctypes.c_size_t
        self.window.user.SendMessageTimeoutW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM,
                                                       wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t)]

    def grip(self):
        process = self.process
        if not process or process.poll() is not None:
            return None
        owner = self.hwnd
        def matches(handle):
            if not handle or not self.window.user.IsWindow(handle):
                return False
            pid = wt.DWORD()
            self.window.user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            return pid.value == process.pid and self.window.user.GetWindow(handle, 4) == owner
        if matches(self._grip):
            return self._grip
        found = None
        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def visit(handle, _):
            nonlocal found
            if matches(handle):
                name = ctypes.create_unicode_buffer(128)
                if self.window.user.GetClassNameW(handle, name, 128) and name.value == "HongguoPlayerDragArea":
                    found = handle
                    return False
            return True
        self.window.user.EnumWindows(visit, 0)
        # A companion can close while the enumeration crosses a ctypes call.
        # Do not cache a result belonging to a retired process or owner.
        if self.process is process and self.hwnd == owner and matches(found):
            self._grip = found
            return found
        return None

    def _heartbeat(self, stopped):
        while not stopped.is_set():
            handle = self.grip()
            if handle:
                result = ctypes.c_size_t()
                self.window.user.SendMessageTimeoutW(handle, 0x8016, int(self.input_enabled), 0,
                                                     2, 100, ctypes.byref(result))
            stopped.wait(.2)

    def _start_heartbeat(self):
        stopped = threading.Event()
        self.heartbeat_stop = stopped
        self.heartbeat_thread = threading.Thread(target=self._heartbeat, args=(stopped,),
                                                  name="hongguo-input-heartbeat", daemon=True)
        self.heartbeat_thread.start()

    def input_status(self):
        """Read diagnostics without activating WSA or changing its input state."""
        status = {"native_alive": bool(self.process and self.process.poll() is None),
                  "input_enabled": self.input_enabled, "message_timeout": False}
        handle = self.grip()
        if not handle:
            return status
        for selector, key in enumerate(("flags", "heartbeat_ms", "hook_generation", "hook_calls",
                                        "queued_wheels", "hook_error", "fullscreen_presses",
                                        "fullscreen_queued", "fullscreen_focus_ok")):
            result = ctypes.c_size_t()
            if not self.window.user.SendMessageTimeoutW(handle, 0x8019, selector, 0, 2, 100,
                                                       ctypes.byref(result)):
                status["message_timeout"] = True
                break
            status[key] = result.value
        if status.get("heartbeat_ms") == ctypes.c_size_t(-1).value:
            status["heartbeat_ms"] = None
        return status

    def take_wheel(self):
        """Consume one recent (normalized x, y, wheel delta) window input."""
        handle = self.grip()
        result = ctypes.c_size_t()
        if handle and self.window.user.SendMessageTimeoutW(handle, 0x8015, 0, 0, 2, 400, ctypes.byref(result)):
            packet = result.value
            if packet >> 32 in (1, 2):
                return packet & 65535, (packet >> 16) & 65535, -120 if packet >> 32 == 1 else 120
        return None

    def set_fullscreen_target(self, rect=None, *, passthrough=False):
        """Refresh only the observed Fullscreen button, never the whole player."""
        if rect is not None:
            if (len(rect) != 4 or any(type(value) is not int or not 0 <= value <= 65535 for value in rect)
                    or rect[2] <= rect[0] or rect[3] <= rect[1]):
                raise ValueError("无效的全屏按钮范围")
        else:
            rect = (0, 0, 0, 0)
            passthrough = False
        handle = self.grip()
        if not handle:
            self._fullscreen_target_key = None
            return False
        rect = tuple(rect)
        key = (handle, self.hwnd, self.process.pid if self.process else None, rect, bool(passthrough))
        now = time.monotonic()
        # A cleared target has no lease. Keep an observed button refreshed
        # well within native's 3s TTL, including the slower player observer.
        if (key == self._fullscreen_target_key and
                (rect == (0, 0, 0, 0) or now-self._fullscreen_target_sent_at < .7)):
            return True
        result = ctypes.c_size_t()
        sent = bool(self.window.user.SendMessageTimeoutW(
            handle, 0x801a, rect[0] | (rect[1] << 16) | (int(bool(passthrough)) << 32), rect[2] | (rect[3] << 16),
            2, 100, ctypes.byref(result)))
        if sent:
            self._fullscreen_target_key = key
            self._fullscreen_target_sent_at = now
        return sent

    def take_fullscreen(self):
        """Distinguish an intercepted pair from the app's original click."""
        handle = self.grip()
        result = ctypes.c_size_t()
        if handle and self.window.user.SendMessageTimeoutW(handle, 0x801b, 0, 0, 2, 100, ctypes.byref(result)):
            if result.value == 2:
                return "observed"
            return result.value == 1
        return False

    def take_page_change(self):
        """Read a recent click hint; never consume or replay the app's click."""
        handle = self.grip()
        result = ctypes.c_size_t()
        return bool(handle and self.window.user.SendMessageTimeoutW(handle, 0x801f, 0, 0, 2, 100,
                                                                   ctypes.byref(result)) and result.value == 1)

    def set_fullscreen_presentation(self, mask=None):
        """Draw the relocated feed control without covering the app's series tag."""
        rect = (0, 0, 0, 0) if mask is None else tuple(mask)
        if (len(rect) != 4 or any(type(v) is not int or not 0 <= v <= 65535 for v in rect)
                or (mask is not None and (rect[2] <= rect[0] or rect[3] <= rect[1]))):
            raise ValueError("无效的推荐页按钮显示范围")
        handle = self.grip()
        key = (handle, self.process.pid if self.process else None, rect)
        if not handle:
            self._fullscreen_presentation_key = None
            return False
        if key == getattr(self, "_fullscreen_presentation_key", None):
            return True
        result = ctypes.c_size_t()
        sent = bool(self.window.user.SendMessageTimeoutW(handle, 0x801d,
                    rect[0] | (rect[1] << 16), rect[2] | (rect[3] << 16),
                    2, 100, ctypes.byref(result)))
        if sent:
            self._fullscreen_presentation_key = key
        return sent

    def wait_input(self, enabled=True, timeout_ms=200):
        # The lease must stay refreshed while the caller awaits an Android
        # request or the page-observation lock. It is not tied to this wait.
        self.input_enabled = bool(enabled)
        return self.kernel.WaitForSingleObject(self.input_event, timeout_ms) == 0

    def sync(self, hwnd, enabled, layout=None):
        with self._lifecycle_lock:
            self._sync(hwnd, bool(enabled) and not self.geometry_holding, layout)

    def begin_geometry_change(self):
        """Temporarily restore WSA so resizing updates Android's display ratio."""
        with self._lifecycle_lock:
            hwnd = self.hwnd
            if not hwnd or not self.window.user.IsWindow(hwnd):
                return False
            self.geometry_holding = True
            self._sync(hwnd, False)
            deadline = time.monotonic()+1.6
            while time.monotonic() < deadline:
                if not self.window.user.IsWindow(hwnd):
                    return False
                if self.window.user.GetWindowLongPtrW(hwnd, -16) & 0xC00000:
                    # Ensure the companion finished restoring the original
                    # frame before another SetWindowPos changes its geometry.
                    handle = self.grip()
                    result = ctypes.c_size_t()
                    if handle and self.window.user.SendMessageTimeoutW(handle, 0x8019, 0, 0, 2, 100,
                                                                       ctypes.byref(result)) and result.value & 8:
                        return True
                time.sleep(.02)
            return False

    def end_geometry_change(self, hide_titlebar):
        """Release the temporary hold after the final content size is applied."""
        with self._lifecycle_lock:
            self.geometry_holding = False
            if self.hwnd and self.process:
                self._sync(self.hwnd, bool(hide_titlebar))

    def _sync(self, hwnd, enabled, layout=None):
        if not hwnd:
            self.close()
            return
        if self.process and self.hwnd == hwnd:
            if self.process.poll() is None:
                if self.hide_titlebar != bool(enabled):
                    handle = self.grip()
                    if handle and self.window.user.PostMessageW(handle, 0x8018, int(enabled), 0):
                        self.hide_titlebar = bool(enabled)
                return
            code = self.process.returncode
            self._close(preserve_geometry=True)
            raise OperationError(f"窗口输入辅助已退出（{code}），将重新连接。")
        self._close(preserve_geometry=True)
        executable = Path(__file__).resolve().parents[1] / "assets/player-chrome.exe"
        if not executable.is_file() and os.environ.get("FLET_ASSETS_DIR"):
            executable = Path(os.environ["FLET_ASSETS_DIR"]) / "player-chrome.exe"
        if not executable.is_file():
            raise OperationError("窗口辅助组件缺失，请保留完整的工具目录。")
        height = 30
        if layout:
            previous = self.window.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            try:
                client = wt.RECT()
                if self.window.user.GetClientRect(hwnd, ctypes.byref(client)):
                    height = caption_dip(client.right, client.bottom, layout.width, layout.height,
                                         self.window.user.GetDpiForWindow(hwnd) or 96)
            finally:
                self.window.user.SetThreadDpiAwarenessContext(previous)
        event_name = "Local\\HongguoChromeStop_" + uuid.uuid4().hex
        self.stop_event = self.kernel.CreateEventW(None, True, False, event_name)
        if not self.stop_event:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self.process = subprocess.Popen([str(executable), str(os.getpid()), str(hwnd), str(height),
                                             event_name, self.input_event_name, str(int(bool(enabled)))],
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL, creationflags=NO_WINDOW)
            self.hwnd = hwnd
            self.hide_titlebar = bool(enabled)
            self._start_heartbeat()
        except OSError:
            self.close()
            raise

    def close(self):
        with self._lifecycle_lock:
            self._close()

    def _close(self, preserve_geometry=False):
        self._fullscreen_target_key = None
        self._fullscreen_target_sent_at = 0.0
        if not preserve_geometry:
            self.geometry_holding = False
        self.input_enabled = False
        if self.heartbeat_stop:
            self.heartbeat_stop.set()
        if self.heartbeat_thread:
            self.heartbeat_thread.join(timeout=1)
            self.heartbeat_thread = None
            self.heartbeat_stop = None
        if self.stop_event:
            self.kernel.SetEvent(self.stop_event)
        if self.process:
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                # Keep the restore guard alive and retry later. Killing it
                # could leave a window without its ordinary controls.
                raise OperationError("窗口仍在恢复标题栏，请稍后重试。")
            # Recover visibility even if the native companion itself crashed.
            if self.hwnd and self.window.user.GetPropW(self.hwnd, "HongguoVideoPresentation") == self.process.pid:
                user = self.window.user
                user.SetLayeredWindowAttributes.argtypes = [wt.HWND, wt.DWORD, ctypes.c_ubyte, wt.DWORD]
                user.SetWindowLongPtrW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_ssize_t]
                user.RemovePropW.argtypes = [wt.HWND, wt.LPCWSTR]
                user.SetLayeredWindowAttributes(self.hwnd, 0, 255, 2)
                user.SetWindowLongPtrW(self.hwnd, -20, user.GetWindowLongPtrW(self.hwnd, -20) & ~0x80000)
                user.RemovePropW(self.hwnd, "HongguoVideoPresentation")
            self.process = None
        if self.stop_event:
            self.kernel.CloseHandle(self.stop_event)
            self.stop_event = None
        self.hwnd = None
        self.hide_titlebar = None
        self._grip = None

    def close_player(self):
        hwnd = self.window.find()
        self.close()
        if hwnd and not self.window.user.PostMessageW(hwnd, 0x0010, 0, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return bool(hwnd)
