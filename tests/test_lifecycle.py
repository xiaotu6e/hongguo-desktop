import asyncio
import ctypes
from ctypes import wintypes as wt
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import flet as ft
from main import DesktopApp, run
from desktop_manager.single_instance import SingleInstance
from desktop_manager.runtime_diagnostics import RuntimeDiagnostics


class LifecycleTests(IsolatedAsyncioTestCase):
    def app(self):
        app = DesktopApp.__new__(DesktopApp)
        app.page = SimpleNamespace(window=SimpleNamespace(
            minimized=False, visible=False, destroy=AsyncMock()), update=Mock())
        app.runtime = Mock()
        app.events = Mock()
        app.stopping = False
        app.lock = asyncio.Lock()
        app.window_chrome = Mock()
        app.playback = Mock()
        return app

    async def test_close_button_keeps_input_running_and_taskbar_window_recoverable(self):
        app = self.app()
        await app.on_window_event(SimpleNamespace(type=ft.WindowEventType.CLOSE))
        self.assertTrue(app.page.window.minimized)
        self.assertTrue(app.page.window.visible)
        app.page.window.destroy.assert_not_called()
        app.window_chrome.close.assert_not_called()
        app.playback.close.assert_not_called()
        app.runtime.lifecycle.assert_called_once_with("minimized")

    async def test_explicit_exit_restores_player_before_destroying_helper(self):
        app = self.app()
        order = []
        app.window_chrome.close.side_effect = lambda: order.append("native")
        app.playback.close.side_effect = lambda: order.append("bridge")
        app.page.window.destroy.side_effect = lambda: order.append("window")
        await app.exit_tool()
        self.assertEqual(order, ["native", "bridge", "window"])
        self.assertTrue(app.stopping)
        await app.exit_tool()
        app.page.window.destroy.assert_awaited_once()

    async def test_restore_resets_minimized_property_so_a_second_close_can_minimize_again(self):
        app = self.app()
        await app.on_window_event(SimpleNamespace(type=ft.WindowEventType.CLOSE))
        await app.on_window_event(SimpleNamespace(type=ft.WindowEventType.RESTORE))
        self.assertFalse(app.page.window.minimized)
        await app.on_window_event(SimpleNamespace(type=ft.WindowEventType.CLOSE))
        self.assertTrue(app.page.window.minimized)
        app.page.window.destroy.assert_not_called()

    async def test_exit_cleanup_error_is_logged_and_does_not_trap_user(self):
        app = self.app()
        app.window_chrome.close.side_effect = OSError("native unavailable")
        await app.exit_tool()
        app.runtime.error.assert_called_once()
        app.playback.close.assert_called_once()
        app.page.window.destroy.assert_awaited_once()

    async def test_packaged_exit_persists_stopped_and_released_processes_before_native_destroy(self):
        app = self.app()
        with tempfile.TemporaryDirectory() as directory:
            app.runtime = RuntimeDiagnostics(Path(directory), Mock())
            app.runtime.beat("watch_window_input")
            process = SimpleNamespace(pid=200, poll=lambda: None)
            bridge = SimpleNamespace(process=process, last_exit_code=None,
                                     last_failure=None, stderr_tail=[], recoveries=0,
                                     last_shutdown_forced=False)
            app.playback.bridge = bridge
            app.window_chrome.process = process
            app.window_chrome.hwnd = 100
            app.window_chrome.input_status.return_value = {"native_alive": False, "input_enabled": False}
            app.window_chrome.close.side_effect = lambda: setattr(app.window_chrome, "process", None)
            app.playback.close.side_effect = lambda: setattr(bridge, "process", None)

            async def verify_durable_shutdown():
                state = json.loads(app.runtime.path.read_text(encoding="utf-8"))
                self.assertEqual(state["lifecycle"], "stopped")
                self.assertEqual(state["exit_reason"], "用户选择退出工具")
                self.assertFalse(state["bridge"]["alive"])
                self.assertFalse(state["native"]["alive"])
                self.assertFalse(state["tasks"]["watch_window_input"]["alive"])
                self.assertFalse(state["input"]["input_enabled"])

            app.page.window.destroy.side_effect = verify_durable_shutdown
            await app.exit_tool()
            app.page.window.destroy.assert_awaited_once()
            # Native/session callbacks arriving during teardown retain the
            # completed shutdown instead of changing it back to disconnected.
            app.on_page_disconnect(None)
            self.assertEqual(app.runtime.state["lifecycle"], "stopped")

    async def test_watchers_waking_during_shutdown_never_restart_closed_components(self):
        for phase in ["player_sleep", "player_lock", "input_wait", "input_lock"]:
            with self.subTest(phase=phase):
                app = self.app()
                app.busy = False
                app.chrome_retry_after = 0
                app.settings = SimpleNamespace(hide_titlebar=True)
                app.player_window = Mock()
                app.window_input = Mock()
                app.playback.poll_interval = 0
                app.playback.bridge.process = SimpleNamespace(poll=lambda: None)

                def begin_shutdown(*_):
                    app.stopping = True
                    return True

                class ShutdownOnAcquire:
                    def locked(self):
                        return False

                    async def __aenter__(self):
                        begin_shutdown()

                    async def __aexit__(self, *_):
                        return False

                if phase.endswith("lock"):
                    app.lock = ShutdownOnAcquire()
                app.window_chrome.wait_input.return_value = True
                if phase == "input_wait":
                    app.window_chrome.wait_input.side_effect = begin_shutdown
                with patch("main.asyncio.sleep", new=AsyncMock(
                        side_effect=begin_shutdown if phase == "player_sleep" else None)):
                    if phase.startswith("player"):
                        await app.watch_player_window()
                    else:
                        await app.watch_window_input()
                self.assertTrue(app.stopping)
                app.player_window.find.assert_not_called()
                app.window_chrome.sync.assert_not_called()
                app.playback.tick.assert_not_called()
                app.window_input.dispatch.assert_not_called()
                app.window_chrome.take_wheel.assert_not_called()


class SingleLaunchTests(TestCase):
    def test_packaged_duplicate_restores_primary_instead_of_its_own_same_title_window(self):
        instance = SingleInstance.__new__(SingleInstance)
        instance.user = Mock()
        instance.kernel = Mock()
        instance.trace = Mock()
        instance.callback = lambda function: function
        windows = {101: {"pid": 1000, "title": "红果桌面助手", "iconic": True},
                   202: {"pid": 2000, "title": "红果桌面助手", "iconic": True}}

        def enum(visit, _):
            for hwnd in windows:
                if not visit(hwnd, 0):
                    break

        def title(hwnd, buffer, _):
            buffer.value = windows[hwnd]["title"]

        def process_id(hwnd, pointer):
            ctypes.cast(pointer, ctypes.POINTER(wt.DWORD)).contents.value = windows[hwnd]["pid"]

        def process_path(process, _, buffer, _size):
            buffer.value = r"C:\test-app\hongguo_desktop.exe"
            return True

        def restore(hwnd, _):
            windows[hwnd]["iconic"] = False
            return True

        instance.user.EnumWindows.side_effect = enum
        instance.user.GetWindowTextW.side_effect = title
        instance.user.GetWindowThreadProcessId.side_effect = process_id
        instance.kernel.OpenProcess.side_effect = lambda _, __, pid: pid
        instance.kernel.QueryFullProcessImageNameW.side_effect = process_path
        instance.user.ShowWindowAsync.side_effect = restore
        instance.user.IsWindowVisible.return_value = True
        instance.user.IsIconic.side_effect = lambda hwnd: windows[hwnd]["iconic"]
        with patch("desktop_manager.single_instance.os.getpid", return_value=1000):
            self.assertTrue(instance.restore_existing(timeout=1))
        instance.user.ShowWindowAsync.assert_called_once_with(202, 9)
        instance.user.SetForegroundWindow.assert_called_once_with(202)
        self.assertTrue(windows[101]["iconic"])
        self.assertFalse(windows[202]["iconic"])

    def test_second_shortcut_click_restores_without_launching_another_flet_session(self):
        with patch("main.SingleInstance") as guard_class, patch("main.ft.run") as launch:
            guard_class.return_value.acquire.return_value = False
            run()
            guard_class.return_value.restore_existing.assert_called_once_with(timeout=5)
            launch.assert_not_called()

    def test_primary_guard_is_kept_through_flet_and_released_after_exit(self):
        with patch("main.SingleInstance") as guard_class, patch("main.ft.run") as launch:
            guard_class.return_value.acquire.return_value = True
            launch.side_effect = lambda *_: self.assertFalse(guard_class.return_value.close.called)
            run()
            launch.assert_called_once()
            guard_class.return_value.close.assert_called_once()
