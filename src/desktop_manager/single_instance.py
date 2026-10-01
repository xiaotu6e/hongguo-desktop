"""Keep one assistant session and restore its window on another launch."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import time


class SingleInstance:
    def __init__(self, name="Local\\HongguoDesktopHelperPrimary"):
        self.name = name
        self.handle = None
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
        self.kernel.CreateMutexW.restype = wt.HANDLE
        self.kernel.CloseHandle.argtypes = [wt.HANDLE]
        self.kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        self.kernel.OpenProcess.restype = wt.HANDLE
        self.kernel.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
        self.kernel.QueryFullProcessImageNameW.restype = wt.BOOL
        self.callback = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        self.user.EnumWindows.argtypes = [self.callback, wt.LPARAM]
        self.user.GetWindowTextW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        self.user.GetClassNameW.argtypes = [wt.HWND, wt.LPWSTR, ctypes.c_int]
        self.user.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        self.user.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
        self.user.ShowWindowAsync.argtypes = [wt.HWND, ctypes.c_int]
        self.user.ShowWindowAsync.restype = wt.BOOL
        self.user.IsWindowVisible.argtypes = [wt.HWND]
        self.user.IsWindowVisible.restype = wt.BOOL
        self.user.IsIconic.argtypes = [wt.HWND]
        self.user.IsIconic.restype = wt.BOOL
        self.user.SetForegroundWindow.argtypes = [wt.HWND]

    def trace(self, event, **details):
        """A duplicate launch has no Page/logging setup; retain its own evidence."""
        try:
            folder = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "HongguoDesktopHelper"
            folder.mkdir(parents=True, exist_ok=True)
            record = {"at": datetime.now().astimezone().isoformat(timespec="milliseconds"),
                      "pid": os.getpid(), "event": event, **details}
            with (folder / "instance-recovery.log").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False)+"\n")
        except (OSError, TypeError, ValueError):
            pass

    def acquire(self):
        if self.handle:
            return True
        ctypes.set_last_error(0)
        handle = self.kernel.CreateMutexW(None, False, self.name)
        error = ctypes.get_last_error()
        if not handle:
            self.trace("mutex_failed", error=error)
            raise ctypes.WinError(error)
        if error == 183:  # ERROR_ALREADY_EXISTS
            self.kernel.CloseHandle(handle)
            self.trace("duplicate_detected", executable=sys.executable)
            return False
        self.handle = handle
        self.trace("primary_acquired", executable=sys.executable)
        return True

    def restore_existing(self, timeout=5):
        deadline = time.monotonic()+timeout
        attempt = 0
        self.trace("restore_begin", timeout=timeout)
        while True:
            found = []
            attempt += 1
            visited = 0

            @self.callback
            def visit(hwnd, _):
                nonlocal visited
                visited += 1
                title = ctypes.create_unicode_buffer(128)
                self.user.GetWindowTextW(hwnd, title, len(title))
                pid = wt.DWORD()
                self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                # A packaged duplicate already owns a Flutter native window
                # before its Python entry executes. Restoring that temporary
                # window and then exiting leaves the primary minimized.
                if pid.value == os.getpid():
                    if attempt == 1:
                        self.trace("skip_current_process_window", hwnd=hwnd, title=title.value)
                    return True
                if title.value != "红果桌面助手":
                    return True
                process = self.kernel.OpenProcess(0x1000, False, pid.value)
                if process:
                    try:
                        path = ctypes.create_unicode_buffer(32768)
                        size = wt.DWORD(len(path))
                        query_ok = self.kernel.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size))
                        error = ctypes.get_last_error() if not query_ok else None
                        self.trace("candidate", hwnd=hwnd, title=title.value, candidate_pid=pid.value,
                                   path=path.value, query_ok=bool(query_ok), error=error, attempt=attempt)
                        if query_ok:
                            # The Flet development frontend runs as flet.exe;
                            # the packaged frontend runs in the app executable.
                            if Path(path.value).name.lower() in {"hongguo_desktop.exe", "flet.exe"}:
                                found.append(hwnd)
                    finally:
                        self.kernel.CloseHandle(process)
                else:
                    self.trace("candidate_process_unreadable", hwnd=hwnd, candidate_pid=pid.value,
                               title=title.value, error=ctypes.get_last_error())
                return not found

            self.user.EnumWindows(visit, 0)
            if found:
                hwnd = found[0]
                queued = self.user.ShowWindowAsync(hwnd, 9)  # SW_RESTORE also unhides.
                foreground = self.user.SetForegroundWindow(hwnd)
                self.trace("restore_requested", hwnd=hwnd, queued=bool(queued),
                           foreground=bool(foreground), attempt=attempt)
                # ShowWindowAsync crosses to the primary's UI thread. Returning
                # only after its state changes makes success meaningful.
                while time.monotonic() < deadline:
                    if self.user.IsWindowVisible(hwnd) and not self.user.IsIconic(hwnd):
                        self.trace("restore_succeeded", hwnd=hwnd)
                        return True
                    time.sleep(.05)
                self.trace("restore_failed", hwnd=hwnd, visible=bool(self.user.IsWindowVisible(hwnd)),
                           minimized=bool(self.user.IsIconic(hwnd)))
                return False
            if time.monotonic() >= deadline:
                self.trace("no_primary_window", visited=visited, attempts=attempt)
                return False
            time.sleep(.1)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
