"""Connect the packaged helper to its independent Windows process supervisor."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as wt
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

GUARDIAN_MUTEX = r"Local\HongguoDesktopHelperGuardian"


class GuardianLink:
    def __init__(self, logger, executable=None):
        self.logger = logger
        self.executable = Path(executable or sys.executable)
        self.enabled = self.executable.name.lower() == "hongguo_desktop.exe"
        self.recovery = os.environ.get("HONGGUO_GUARDIAN_RECOVERY") == "1"
        self.last_launch = -10.0
        self.stop = self.heartbeat = None
        if not self.enabled:
            return
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
        self.kernel.CreateEventW.restype = wt.HANDLE
        self.kernel.OpenMutexW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]
        self.kernel.OpenMutexW.restype = wt.HANDLE
        self.kernel.SetEvent.argtypes = [wt.HANDLE]
        self.kernel.CloseHandle.argtypes = [wt.HANDLE]
        self.stop = self.kernel.CreateEventW(None, True, False,
                                            rf"Local\HongguoDesktopHelperStop_{os.getpid()}")
        self.heartbeat = self.kernel.CreateEventW(None, False, False,
                                                 rf"Local\HongguoDesktopHelperBeat_{os.getpid()}")
        if not self.stop or not self.heartbeat:
            raise ctypes.WinError(ctypes.get_last_error())
        self.pulse()

    def alive(self):
        if not self.enabled:
            return False
        handle = self.kernel.OpenMutexW(0x00100000, False, GUARDIAN_MUTEX)
        if handle:
            self.kernel.CloseHandle(handle)
        return bool(handle)

    def ensure(self):
        if not self.enabled or self.alive():
            return
        now = time.monotonic()
        if now - self.last_launch < 10:
            return
        self.last_launch = now
        try:
            guardian = self.executable.with_name("helper-guardian.exe")
            # No Python console is required. The native guardian relaunches
            # independently if it inherited a caller's Windows Job Object.
            subprocess.Popen([str(guardian), "--attach", str(os.getpid()),
                              "--helper", str(self.executable)],
                             cwd=str(self.executable.parent), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=subprocess.CREATE_NO_WINDOW)
            self.logger.info("已请求独立后台守护，关联主进程 PID=%s", os.getpid())
        except OSError:
            self.logger.exception("后台守护启动失败，将再次尝试")

    def pulse(self):
        if self.enabled:
            self.kernel.SetEvent(self.heartbeat)
            self.ensure()

    def request_stop(self):
        if self.enabled:
            self.kernel.SetEvent(self.stop)

    def close(self):
        if self.enabled:
            for handle in (self.stop, self.heartbeat):
                if handle:
                    self.kernel.CloseHandle(handle)
            self.stop = self.heartbeat = None
