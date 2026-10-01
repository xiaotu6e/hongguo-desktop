"""Read and operate only Hongguo's accessible controls over the existing WSA ADB."""
from __future__ import annotations

import base64
from collections import deque
import json
import os
from pathlib import Path
import queue
import subprocess
import threading
import time

from .android import AndroidManager, NO_WINDOW, OperationError


class _ConnectionLost(OperationError):
    """A transport failure, for which a read-only request may be retried."""


class UiBridge:
    def __init__(self, manager: AndroidManager):
        self.manager = manager
        self.process = None
        self.replies = queue.Queue()
        self.errors = deque(maxlen=8)
        self.reader_threads = []
        self.lock = threading.RLock()
        self.last_exit_code = None
        self.last_shutdown_forced = False
        self.last_failure = None
        self.recoveries = 0
        self.last_snapshot = None
        self._snapshot_timings = deque(maxlen=20)

    @property
    def stderr_tail(self):
        return list(self.errors)

    @property
    def recent_snapshot_ms(self):
        return list(self._snapshot_timings)

    def start(self):
        with self.lock:
            self._start()

    def _start(self):
        if self.process and self.process.poll() is None:
            return
        exited = self.process
        self.close()
        if exited is not None:
            # A periodic poll can discover an ADB crash before a request ever
            # touches the broken pipe. Preserve that session's diagnostics
            # before replacing its stderr buffer with the new connection.
            self.last_failure = {
                "message": "红果页面连接进程已退出。", "pid": exited.pid,
                "exit_code": self.last_exit_code, "stderr": self.stderr_tail,
                "terminated_by_helper": False, "kill_requested": False,
            }
        jar = Path(__file__).resolve().parents[1] / "assets/hongguo-ui-bridge.jar"
        if not jar.is_file() and os.environ.get("FLET_ASSETS_DIR"):
            jar = Path(os.environ["FLET_ASSETS_DIR"]) / "hongguo-ui-bridge.jar"
        if not jar.is_file():
            raise OperationError("播放辅助组件缺失，请保留工具目录的完整内容。")
        remote = "/data/local/tmp/hongguo-ui-bridge.jar"
        result = self.manager.command("push", str(jar), remote, timeout=8)
        if result.returncode:
            raise OperationError("播放辅助连接未就绪。")
        settings = self.manager.settings
        self.replies = queue.Queue()
        self.errors = deque(maxlen=8)
        try:
            self.process = subprocess.Popen(
                [str(self.manager.adb), "-P", str(settings.server_port), "-s", settings.endpoint,
                 "shell", "CLASSPATH=" + remote, "app_process", "/system/bin", "HongguoUi"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW,
            )
        except OSError as error:
            raise self._lost("无法启动红果页面连接。", detail=str(error)) from error
        replies = self.replies
        errors = self.errors
        def read(stream):
            failure = "bridge_closed"
            try:
                for line in stream:
                    try:
                        obj = json.loads(line)
                        if isinstance(obj, dict):
                            replies.put(obj)
                    except ValueError:
                        pass
            except (OSError, ValueError) as error:
                failure = "bridge_read_failed: " + str(error)
            finally:
                # This queue belongs to this process only. An old reader must
                # never report its EOF into a newly connected bridge.
                replies.put({"error": failure})
        def read_errors(stream):
            try:
                for line in stream:
                    errors.append(line.strip()[-1000:])
            except (OSError, ValueError):
                pass
        self.reader_threads = [
            threading.Thread(target=read, args=(self.process.stdout,), daemon=True,
                             name="HongguoBridgeReply"),
            threading.Thread(target=read_errors, args=(self.process.stderr,), daemon=True,
                             name="HongguoBridgeError"),
        ]
        for thread in self.reader_threads:
            thread.start()
        if not self.receive().get("ready"):
            raise self._lost("播放辅助服务启动失败。")
        if exited is not None:
            self.recoveries += 1

    def receive(self) -> dict:
        try:
            return self.replies.get(timeout=6)
        except queue.Empty as error:
            raise self._lost("读取红果页面超时。") from error

    def request(self, command: str) -> dict:
        with self.lock:
            try:
                return self._request_once(command)
            except _ConnectionLost:
                # A failed action may already have reached Android. Replaying
                # wheel/click/back could skip a second drama or episode.
                if command != "snapshot":
                    raise
                reply = self._request_once(command)
                self.recoveries += 1
                return reply

    def _request_once(self, command: str) -> dict:
        self.start()
        try:
            self.process.stdin.write(command + "\n")
            self.process.stdin.flush()
            reply = self.receive()
        except (OSError, ValueError) as error:
            raise self._lost("红果页面连接已断开。", detail=str(error)) from error
        if reply.get("error"):
            message = "读取红果页面失败：" + str(reply["error"])
            if str(reply["error"]).startswith("bridge_"):
                raise self._lost(message)
            self.close()
            raise OperationError(message)
        return reply

    def _lost(self, message, *, detail=None):
        process = self.process
        self.close()
        code = self.last_exit_code if process is not None else None
        self.last_failure = {
            "message": message, "pid": getattr(process, "pid", None),
            "exit_code": code, "stderr": self.stderr_tail,
            # A kill request may race with an external exit. Record the
            # request itself without assigning an unprovable cause of death.
            "terminated_by_helper": None if process is not None and self.last_shutdown_forced else False,
            "kill_requested": self.last_shutdown_forced if process is not None else False,
        }
        if detail:
            self.last_failure["detail"] = detail
        if code and not self.last_shutdown_forced:
            rendered = f"0x{code & 0xffffffff:08X}" if code < 0 or code > 0xffff else str(code)
            message += f"（ADB 退出码 {rendered}）"
        if self.errors:
            message += " " + " | ".join(self.stderr_tail[-3:])[-500:]
        return _ConnectionLost(message)

    def snapshot(self) -> dict:
        with self.lock:
            started = time.perf_counter()
            reply = None
            try:
                reply = self.request("snapshot")
                return reply
            finally:
                # Includes connection/recovery work, unlike the Java tree
                # timing. Keep failed reads visible without retaining stale
                # diagnostics from the preceding successful snapshot.
                elapsed = round((time.perf_counter() - started) * 1000, 3)
                diagnostics = reply.get("diagnostics") if isinstance(reply, dict) else None
                self.last_snapshot = {
                    "request_ms": elapsed, "ok": bool(reply and reply.get("ok")),
                    "diagnostics": diagnostics if isinstance(diagnostics, dict) else None,
                }
                self._snapshot_timings.append(elapsed)

    def wheel(self, x: int, y: int, delta: int) -> dict:
        if not (0 <= x <= 65535 and 0 <= y <= 65535 and delta in (-120, 120)):
            return {"ok": False}
        return self.request(f"wheel\t{x}\t{y}\t{delta}")

    def click(self, node: dict, *, tap=False) -> bool:
        return self.node_action("tap" if tap else "click", node)

    def dismiss(self, marker: dict) -> bool:
        """Back only if the observed menu marker still exists at action time."""
        return self.node_action("dismiss", marker)

    def node_action(self, action: str, node: dict) -> bool:
        def encode(value):
            return base64.b64encode(value.encode("utf-8")).decode("ascii")
        bounds = json.dumps(node["bounds"], separators=(",", ":"))
        command = "\t".join([action, node["path"], encode(node["id"]), encode(node["text"]), bounds])
        return bool(self.request(command).get("ok"))

    def back(self) -> bool:
        return bool(self.request("back").get("ok"))

    def close(self):
        with self.lock:
            self._close()

    def _close(self):
        process, self.process = self.process, None
        threads, self.reader_threads = self.reader_threads, []
        if process:
            self.last_shutdown_forced = False
            try:
                if process.poll() is None:
                    process.stdin.write("quit\n")
                    process.stdin.flush()
                    process.stdin.close()
                    process.wait(timeout=1)
            except (OSError, ValueError, subprocess.TimeoutExpired):
                try:
                    # BrokenPipe can mean ADB exited between poll() and the
                    # quit write. Do not report that external exit as a kill.
                    if process.poll() is None:
                        self.last_shutdown_forced = True
                        process.kill()
                    process.wait(timeout=1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            finally:
                self.last_exit_code = process.poll()
                # Let EOF release the reader's text-stream lock before closing
                # its pipe. Every handle is closed even if ADB already crashed.
                for thread in threads:
                    thread.join(timeout=.25)
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        try:
                            stream.close()
                        except (OSError, ValueError):
                            pass
