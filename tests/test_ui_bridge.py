"""Transport recovery tests using in-memory pipes; never connects to WSA."""
import io
import json
from pathlib import Path
import queue
import subprocess
import sys
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from desktop_manager.android import OperationError
from desktop_manager.ui_bridge import UiBridge


class InputPipe(io.StringIO):
    def __init__(self, *, broken=False):
        super().__init__()
        self.commands = []
        self.broken = broken

    def write(self, value):
        if self.broken:
            raise BrokenPipeError("simulated ADB crash")
        self.commands.append(value)
        return super().write(value)


class FailedReader(io.StringIO):
    def __iter__(self):
        yield '{"ready":true}\n'
        raise OSError("simulated pipe read failure")


class FakeProcess:
    def __init__(self, output, *, code=None, stderr="", broken=False, stuck=False):
        self.pid = 100
        self.returncode = code
        self.stdin = InputPipe(broken=broken)
        self.stdout = io.StringIO(output)
        self.stderr = io.StringIO(stderr)
        self.stuck = stuck
        self.killed = False

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.stuck and self.returncode is None:
            raise subprocess.TimeoutExpired("fake-adb", timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def kill(self):
        self.killed = True
        self.returncode = -9


class UiBridgeTests(TestCase):
    def setUp(self):
        self.manager = Mock(
            adb=Path("fake-adb-never-executed.exe"),
            settings=SimpleNamespace(server_port=5038, endpoint="127.0.0.1:58526"),
        )
        self.manager.command.return_value = SimpleNamespace(returncode=0)
        self.bridge = UiBridge(self.manager)
        self.jar = patch("desktop_manager.ui_bridge.Path.is_file", return_value=True)
        self.jar.start()
        self.addCleanup(self.jar.stop)
        self.addCleanup(self.bridge.close)

    def assert_pipes_closed(self, process):
        self.assertTrue(all(stream.closed for stream in
                            (process.stdin, process.stdout, process.stderr)))

    def test_snapshot_reconnects_once_after_external_adb_crash(self):
        crash_code = -1073740791  # Windows 0xC0000409.
        failed = FakeProcess('{"ready":true}\n', code=crash_code, stderr="ADB failed\n")
        recovered = FakeProcess('{"ready":true}\n{"ok":true,"nodes":[]}\n')
        with patch("desktop_manager.ui_bridge.subprocess.Popen", side_effect=[failed, recovered]) as spawn:
            self.assertEqual(self.bridge.snapshot(), {"ok": True, "nodes": []})
        self.assertEqual(spawn.call_count, 2)
        self.assertEqual(self.bridge.recoveries, 1)
        self.assertEqual(self.bridge.last_failure["exit_code"], crash_code)
        self.assertEqual(self.bridge.last_failure["stderr"], ["ADB failed"])
        self.assertFalse(self.bridge.last_failure["terminated_by_helper"])
        self.assert_pipes_closed(failed)

    def test_snapshot_discovers_an_already_exited_process_and_preserves_its_diagnostics(self):
        crash_code = -1073740791
        failed = FakeProcess("", code=crash_code)
        self.bridge.process = failed
        self.bridge.errors.append("previous ADB stderr")
        recovered = FakeProcess('{"ready":true}\n{"ok":true,"nodes":[]}\n')
        with patch("desktop_manager.ui_bridge.subprocess.Popen", return_value=recovered) as spawn:
            self.assertEqual(self.bridge.snapshot()["nodes"], [])
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(self.bridge.recoveries, 1)
        self.assertEqual(self.bridge.last_failure["pid"], failed.pid)
        self.assertEqual(self.bridge.last_failure["exit_code"], crash_code)
        self.assertEqual(self.bridge.last_failure["stderr"], ["previous ADB stderr"])
        self.assertFalse(self.bridge.last_failure["terminated_by_helper"])
        self.assertEqual(self.bridge.stderr_tail, [])
        self.assert_pipes_closed(failed)

    def test_explicit_normal_close_does_not_count_as_a_failed_connection(self):
        previous = FakeProcess("")
        self.bridge.process = previous
        self.bridge.close()
        next_process = FakeProcess('{"ready":true}\n{"ok":true,"nodes":[]}\n')
        with patch("desktop_manager.ui_bridge.subprocess.Popen", return_value=next_process):
            self.assertEqual(self.bridge.snapshot()["nodes"], [])
        self.assertEqual(self.bridge.recoveries, 0)
        self.assertIsNone(self.bridge.last_failure)

    def test_wheel_click_and_back_are_never_replayed_after_disconnect(self):
        for command in ["wheel\t100\t100\t-120", "click\t0\t\t\t[0,0,1,1]", "back"]:
            with self.subTest(command=command):
                failed = FakeProcess('{"ready":true}\n', code=-1073740791)
                with patch("desktop_manager.ui_bridge.subprocess.Popen", return_value=failed) as spawn:
                    with self.assertRaisesRegex(OperationError, "0xC0000409"):
                        self.bridge.request(command)
                self.assertEqual(spawn.call_count, 1)
                self.assertEqual(failed.stdin.commands.count(command + "\n"), 1)
                self.assert_pipes_closed(failed)

    def test_second_failed_snapshot_is_reported_without_a_reconnect_loop(self):
        processes = [FakeProcess('{"ready":true}\n', code=1) for _ in range(2)]
        with patch("desktop_manager.ui_bridge.subprocess.Popen", side_effect=processes) as spawn:
            with self.assertRaises(OperationError):
                self.bridge.snapshot()
        self.assertEqual(spawn.call_count, 2)
        self.assertEqual(self.bridge.recoveries, 0)
        for process in processes:
            self.assert_pipes_closed(process)

    def test_reader_failure_delivers_terminal_reply_instead_of_waiting_for_timeout(self):
        failed = FakeProcess("")
        failed.stdout = FailedReader()
        with patch("desktop_manager.ui_bridge.subprocess.Popen", return_value=failed):
            with self.assertRaisesRegex(OperationError, "bridge_read_failed"):
                self.bridge.wheel(100, 100, -120)
        self.assert_pipes_closed(failed)

    def test_old_reader_eof_stays_in_its_own_queue_after_reconnection(self):
        first = FakeProcess('{"ready":true}\n')
        second = FakeProcess('{"ready":true}\n{"ok":true,"nodes":[]}\n')
        with patch("desktop_manager.ui_bridge.subprocess.Popen", side_effect=[first, second]):
            self.bridge.start()
            old_queue = self.bridge.replies
            self.bridge.close()
            self.assertEqual(self.bridge.snapshot()["nodes"], [])
        self.assertIsNot(self.bridge.replies, old_queue)
        self.assertEqual(old_queue.get_nowait(), {"error": "bridge_closed"})
        self.assert_pipes_closed(first)

    def test_dead_process_pipes_are_all_closed_and_exit_code_is_preserved(self):
        dead = FakeProcess("", code=-1073740791)
        self.bridge.process = dead
        self.bridge.close()
        self.assert_pipes_closed(dead)
        self.assertEqual(self.bridge.last_exit_code, -1073740791)
        self.assertIsNone(self.bridge.process)
        self.assertEqual(dead.stdin.commands, [])

    def test_stuck_process_is_killed_before_its_pipes_are_closed(self):
        stuck = FakeProcess("", stuck=True)
        self.bridge.process = stuck
        self.bridge.close()
        self.assertTrue(stuck.killed)
        self.assertTrue(self.bridge.last_shutdown_forced)
        self.assert_pipes_closed(stuck)

    def test_external_exit_during_quit_write_is_not_misreported_as_a_helper_kill(self):
        process = FakeProcess("")
        self.bridge.process = process

        def exits_during_quit(value):
            process.returncode = 1
            raise BrokenPipeError("ADB exited externally before quit")

        process.stdin.write = Mock(side_effect=exits_during_quit)
        error = self.bridge._lost("连接已断开。")
        self.assertFalse(process.killed)
        self.assertFalse(self.bridge.last_shutdown_forced)
        self.assertEqual(self.bridge.last_failure["exit_code"], 1)
        self.assertFalse(self.bridge.last_failure["kill_requested"])
        self.assertFalse(self.bridge.last_failure["terminated_by_helper"])
        self.assertIn("ADB 退出码 1", str(error))
        self.assert_pipes_closed(process)

    def test_kill_request_does_not_claim_proven_cause_of_exit(self):
        process = FakeProcess("", stuck=True)
        self.bridge.process = process
        self.bridge._lost("连接超时。")
        self.assertTrue(process.killed)
        self.assertTrue(self.bridge.last_failure["kill_requested"])
        self.assertIsNone(self.bridge.last_failure["terminated_by_helper"])
        self.assert_pipes_closed(process)

    def test_timeout_records_failure_and_cleans_up(self):
        process = FakeProcess("")
        self.bridge.process = process
        self.bridge.replies = Mock()
        self.bridge.replies.get.side_effect = queue.Empty
        with self.assertRaisesRegex(OperationError, "超时"):
            self.bridge.receive()
        self.assert_pipes_closed(process)
        self.assertIn("超时", self.bridge.last_failure["message"])

    def test_android_action_error_is_reported_without_transport_replay(self):
        process = FakeProcess('{"ready":true}\n{"error":"java.lang.SecurityException"}\n')
        with patch("desktop_manager.ui_bridge.subprocess.Popen", return_value=process) as spawn:
            with self.assertRaisesRegex(OperationError, "SecurityException"):
                self.bridge.snapshot()
        self.assertEqual(spawn.call_count, 1)
        self.assert_pipes_closed(process)

    def test_non_protocol_output_is_ignored(self):
        process = FakeProcess('debug output\n[1,2]\n{"ready":true}\n{"ok":true,"nodes":[]}\n')
        with patch("desktop_manager.ui_bridge.subprocess.Popen", return_value=process):
            self.assertEqual(self.bridge.snapshot()["nodes"], [])

    def test_snapshot_records_transport_timing_without_changing_the_reply(self):
        reply = {"ok": True, "playing": None, "nodes": [], "diagnostics": {
            "snapshot_ms": 8.5, "rootCount": 2, "rootIndex": 1, "visited": 3, "nodes": 0,
        }}
        process = FakeProcess('{"ready":true}\n' + json.dumps(reply) + '\n')
        with patch("desktop_manager.ui_bridge.subprocess.Popen", return_value=process), \
                patch("desktop_manager.ui_bridge.time.perf_counter", side_effect=[10, 10.025]):
            self.assertEqual(self.bridge.snapshot(), reply)
        self.assertEqual(self.bridge.last_snapshot, {
            "request_ms": 25.0, "ok": True, "diagnostics": reply["diagnostics"],
        })
        self.assertEqual(self.bridge.recent_snapshot_ms, [25.0])

    def test_failed_snapshot_clears_old_diagnostics_and_records_failure_duration(self):
        self.bridge.last_snapshot = {"ok": True, "diagnostics": {"snapshot_ms": 1}}
        processes = [FakeProcess('{"ready":true}\n', code=1) for _ in range(2)]
        with patch("desktop_manager.ui_bridge.subprocess.Popen", side_effect=processes) as spawn, \
                patch("desktop_manager.ui_bridge.time.perf_counter", side_effect=[10, 10.25]):
            with self.assertRaises(OperationError):
                self.bridge.snapshot()
        self.assertEqual(spawn.call_count, 2)
        self.assertEqual(self.bridge.last_snapshot, {
            "request_ms": 250.0, "ok": False, "diagnostics": None,
        })
        self.assertEqual(self.bridge.recent_snapshot_ms, [250.0])

    def test_old_bridge_reply_remains_compatible_and_timing_history_is_bounded(self):
        for _ in range(25):
            with patch.object(self.bridge, "request", return_value={"ok": True, "nodes": []}):
                self.assertEqual(self.bridge.snapshot(), {"ok": True, "nodes": []})
        self.assertIsNone(self.bridge.last_snapshot["diagnostics"])
        self.assertEqual(len(self.bridge.recent_snapshot_ms), 20)
