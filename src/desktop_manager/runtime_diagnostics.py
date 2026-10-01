"""Persistent, best-effort diagnostics and supervision for the desktop helper.

Writing a log must never stop input handling. All diagnostics stay in the helper's
local data folder, and a failed background task is restarted rather than silently
leaving the Android window without its desktop controls.
"""
from __future__ import annotations

import asyncio
import atexit
import faulthandler
import json
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path


def process_state(process) -> dict:
    if process is None:
        return {"pid": None, "alive": False, "exit_code": None}
    try:
        code = process.poll()
        return {"pid": process.pid, "alive": code is None, "exit_code": code}
    except (AttributeError, OSError, ValueError):
        return {"pid": None, "alive": False, "exit_code": None}


class RuntimeDiagnostics:
    def __init__(self, directory: Path, logger: logging.Logger, notify=None):
        self.directory = directory
        self.path = directory / "runtime-state.json"
        self.logger = logger
        self.notify = notify
        self.lock = threading.RLock()
        self.state = {
            "helper_pid": os.getpid(), "started_at": self.timestamp(),
            "lifecycle": "running", "exit_reason": None, "tasks": {},
            "last_error": None, "last_wheel": None,
        }
        self.last_written = 0.0
        self.fault_stream = None
        self.log(logging.INFO, "工具进程已启动，PID=%s", os.getpid())
        self.write(force=True)

    @staticmethod
    def timestamp():
        return datetime.now().astimezone().isoformat(timespec="seconds")

    def log(self, level, message, *args, **kwargs):
        try:
            self.logger.log(level, message, *args, **kwargs)
        except Exception:
            pass

    def update(self, **values):
        with self.lock:
            self.state.update(values)

    def lifecycle(self, value, reason=None):
        with self.lock:
            # An ordinary disconnect after an explicit exit must retain its cause.
            if self.state["lifecycle"] in {"exiting", "stopped"} and value != "stopped":
                return
            self.state["lifecycle"] = value
            if reason:
                self.state["exit_reason"] = reason
        self.log(logging.INFO, "工具生命周期：%s%s", value, " ("+reason+")" if reason else "")
        self.write(force=True)

    def beat(self, name):
        with self.lock:
            task = self.state["tasks"].setdefault(name, {})
            task.update(alive=True, status="running", last_active_at=self.timestamp())

    def error(self, source, error, *, exc_info=None):
        details = {"source": source, "message": str(error), "at": self.timestamp()}
        self.update(last_error=details)
        self.log(logging.ERROR, "%s: %s", source, error, exc_info=exc_info)
        self.write(force=True)

    def write(self, force=False):
        now = time.monotonic()
        if not force and now-self.last_written < 3:
            return False
        with self.lock:
            self.last_written = now
            self.state["updated_at"] = self.timestamp()
            try:
                payload = json.dumps(self.state, ensure_ascii=False, indent=2)
                temporary = self.path.with_name(f"runtime-state.{os.getpid()}.tmp")
                temporary.write_text(payload, encoding="utf-8")
                try:
                    temporary.replace(self.path)
                except OSError as error:
                    if getattr(error, "winerror", None) != 17:
                        raise
                    # Encrypted/redirected Windows folders can reject even a
                    # same-folder rename. Follow SettingsStore's proven local
                    # fallback while retaining the staged diagnostic copy.
                    self.path.write_text(payload, encoding="utf-8")
                return True
            except (OSError, ValueError, TypeError):
                return False

    async def supervise(self, name, handler, stopping, *, restart=True):
        failures = 0
        while not stopping():
            self.beat(name)
            started = time.monotonic()
            try:
                await handler()
                if stopping() or not restart:
                    with self.lock:
                        self.state["tasks"][name].update(alive=False, status="stopped" if stopping() else "completed")
                    return
                raise RuntimeError("后台任务意外结束")
            except asyncio.CancelledError:
                with self.lock:
                    self.state["tasks"][name].update(alive=False, status="cancelled")
                raise
            except Exception as error:
                failures = 1 if time.monotonic()-started > 10 else failures+1
                with self.lock:
                    task = self.state["tasks"][name]
                    task.update(alive=False, status="retrying" if restart else "failed",
                                restarts=task.get("restarts", 0)+int(restart),
                                last_error=str(error), last_failed_at=self.timestamp())
                self.error(name, error, exc_info=True)
                if not restart or stopping():
                    return
                if self.notify:
                    try:
                        self.notify("后台辅助遇到异常，正在自动恢复："+name)
                    except Exception:
                        pass
                await asyncio.sleep(min(2**(failures-1), 30))

    def install_exception_hooks(self, loop):
        previous_sys = sys.excepthook
        previous_thread = threading.excepthook
        previous_loop = loop.get_exception_handler()

        def sys_error(kind, value, traceback):
            self.error("未处理的 Python 异常", value, exc_info=(kind, value, traceback))
            previous_sys(kind, value, traceback)

        def thread_error(args):
            self.error("线程异常："+args.thread.name, args.exc_value,
                       exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
            previous_thread(args)

        def loop_error(active_loop, context):
            error = context.get("exception") or RuntimeError(context.get("message", "异步任务异常"))
            self.error("异步任务异常", error,
                       exc_info=(type(error), error, error.__traceback__))
            if previous_loop:
                previous_loop(active_loop, context)

        sys.excepthook = sys_error
        threading.excepthook = thread_error
        loop.set_exception_handler(loop_error)
        try:
            self.fault_stream = (self.directory / "fatal.log").open("a", encoding="utf-8")
            faulthandler.enable(file=self.fault_stream, all_threads=True)
        except (OSError, RuntimeError):
            pass
        atexit.register(self.process_exit)

    def process_exit(self):
        """Persist a completed shutdown explicitly or from the atexit fallback."""
        with self.lock:
            reason = self.state.get("exit_reason") or "正常进程退出"
            self.state["lifecycle"] = "stopped"
            self.state["exit_reason"] = reason
            self.state["stopped_at"] = self.timestamp()
            for task in self.state["tasks"].values():
                task["alive"] = False
                if task.get("status") == "running":
                    task["status"] = "stopped"
        self.write(force=True)
        self.log(logging.INFO, "工具进程正常退出：%s", reason)
