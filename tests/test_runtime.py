import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from desktop_manager.runtime_diagnostics import RuntimeDiagnostics, process_state


class RuntimeTests(TestCase):
    def test_normal_exit_and_last_wheel_remain_readable(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = RuntimeDiagnostics(Path(directory), logging.getLogger("test-runtime"))
            runtime.beat("input")
            runtime.update(last_wheel={"ok": True, "page": "feed"})
            runtime.lifecycle("minimized")
            runtime.lifecycle("exiting", "用户选择退出工具")
            runtime.lifecycle("ui_disconnected")
            runtime.process_exit()
            state = json.loads(runtime.path.read_text(encoding="utf-8"))
            self.assertEqual(state["lifecycle"], "stopped")
            self.assertEqual(state["exit_reason"], "用户选择退出工具")
            self.assertFalse(state["tasks"]["input"]["alive"])
            self.assertEqual(state["last_wheel"], {"ok": True, "page": "feed"})

    def test_logging_or_disk_failure_cannot_stop_playback_tasks(self):
        logger = Mock()
        logger.log.side_effect = OSError("log unavailable")
        with tempfile.TemporaryDirectory() as directory:
            runtime = RuntimeDiagnostics(Path(directory), logger)
            with patch.object(Path, "write_text", side_effect=PermissionError("locked")):
                runtime.error("input", RuntimeError("recoverable"))
                self.assertFalse(runtime.write(force=True))
            runtime.beat("input")
            runtime.write(force=True)
            self.assertTrue(runtime.state["tasks"]["input"]["alive"])

    def test_diagnostics_record_dead_bridge_separately_from_live_helper(self):
        process = SimpleNamespace(pid=123, poll=lambda: 0xC0000409)
        self.assertEqual(process_state(process), {"pid": 123, "alive": False, "exit_code": 0xC0000409})
        self.assertFalse(process_state(None)["alive"])

    def test_encrypted_windows_folder_retains_readable_diagnostics_when_rename_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = RuntimeDiagnostics(Path(directory), Mock())
            runtime.update(bridge_diagnostics={"last_exit_code": 0xC0000409,
                                               "recoveries": 1})
            cross_volume = OSError("same-folder rename rejected")
            cross_volume.winerror = 17
            with patch.object(Path, "replace", side_effect=cross_volume):
                self.assertTrue(runtime.write(force=True))
            state = json.loads(runtime.path.read_text(encoding="utf-8"))
            self.assertEqual(state["bridge_diagnostics"]["last_exit_code"], 0xC0000409)
            self.assertEqual(state["bridge_diagnostics"]["recoveries"], 1)

    def test_other_rename_failure_never_overwrites_an_existing_diagnostic_file(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = RuntimeDiagnostics(Path(directory), Mock())
            before = runtime.path.read_text(encoding="utf-8")
            runtime.update(last_error={"message": "new failure"})
            with patch.object(Path, "replace", side_effect=PermissionError("locked")):
                self.assertFalse(runtime.write(force=True))
            self.assertEqual(runtime.path.read_text(encoding="utf-8"), before)


class SupervisorTests(IsolatedAsyncioTestCase):
    async def test_unexpected_input_exception_restarts_and_records_trace(self):
        with tempfile.TemporaryDirectory() as directory:
            logger = Mock()
            runtime = RuntimeDiagnostics(Path(directory), logger)
            attempts = 0
            stopping = False

            async def handler():
                nonlocal attempts, stopping
                attempts += 1
                if attempts == 1:
                    raise AttributeError("stale process")
                stopping = True

            async def fast_sleep(_):
                return None

            with patch("desktop_manager.runtime_diagnostics.asyncio.sleep", fast_sleep):
                await runtime.supervise("input", handler, lambda: stopping)
            self.assertEqual(attempts, 2)
            self.assertEqual(runtime.state["tasks"]["input"]["restarts"], 1)
            self.assertEqual(runtime.state["last_error"]["message"], "stale process")
            errors = [call for call in logger.log.call_args_list if call.args[0] == logging.ERROR]
            self.assertEqual(len(errors), 1)
            self.assertEqual(str(errors[0].args[3]), "stale process")
            self.assertTrue(errors[0].kwargs["exc_info"])

    async def test_explicit_shutdown_does_not_restart_cancelled_task(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = RuntimeDiagnostics(Path(directory), Mock())
            async def handler():
                raise asyncio.CancelledError()
            with self.assertRaises(asyncio.CancelledError):
                await runtime.supervise("input", handler, lambda: False)
            self.assertFalse(runtime.state["tasks"]["input"]["alive"])
            self.assertNotIn("restarts", runtime.state["tasks"]["input"])

    async def test_initialization_error_does_not_repeat_automatic_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = RuntimeDiagnostics(Path(directory), Mock())
            handler = Mock(side_effect=RuntimeError("launch failed"))
            await runtime.supervise("initialize", handler, lambda: False, restart=False)
            handler.assert_called_once()
            self.assertEqual(runtime.state["tasks"]["initialize"]["restarts"], 0)
