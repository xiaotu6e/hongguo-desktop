"""Regression tests for startup races and optional metadata blocking playback."""
import asyncio
import queue
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from desktop_manager.android import AndroidManager, CommandTimeout, OperationError, WsaInstallation, run_process
from desktop_manager.config import HONGGUO, Settings
from desktop_manager.playback_window import WsaPlayerWindow
from main import DesktopApp

READY = f"  Window #1 Window{{abc u0 {HONGGUO}/MainActivity}}:\n mHasSurface=true mDrawState=HAS_DRAWN isVisible=true\n"


class StartupTests(TestCase):
    def manager(self, runner):
        manager = AndroidManager(Settings(startup_timeout=20, keep_wsa_direct=False), runner=runner, adb=Path("fake-adb.exe"))
        manager.installation = WsaInstallation(Path("WSA"), "WSA_family")
        return manager

    def test_cold_adb_daemon_has_its_own_budget_before_connecting(self):
        calls = []
        def runner(args, timeout):
            calls.append((args, timeout))
            output = "device\n" if "get-state" in args else "1\n" if "getprop" in args else ""
            return subprocess.CompletedProcess(args, 0, output, "")
        manager = self.manager(runner)
        with patch("desktop_manager.android.os.startfile") as activate:
            manager.ensure_ready()
        self.assertEqual(calls[0][0][-1], "start-server")
        self.assertGreater(calls[0][1], 5)
        self.assertEqual(sum("start-server" in args for args, _ in calls), 1)
        activate.assert_not_called()

    def test_short_connect_timeout_does_not_abort_the_whole_startup(self):
        connects = 0
        def runner(args, timeout):
            nonlocal connects
            if "connect" in args:
                connects += 1
                if connects == 1:
                    raise CommandTimeout(args, timeout)
            output = "device\n" if "get-state" in args else "1\n" if "getprop" in args else ""
            return subprocess.CompletedProcess(args, 0, output, "")
        manager = self.manager(runner)
        with patch("desktop_manager.android.os.startfile") as activate, patch("desktop_manager.android.time.sleep"):
            self.assertTrue(manager.ensure_ready())
        self.assertEqual(connects, 2)
        self.assertEqual(manager.connection_state, "device")
        activate.assert_called_once_with("wsa://" + HONGGUO)

    def test_repair_bootstraps_a_lost_server_even_after_a_cached_success(self):
        calls = []
        def runner(args, timeout):
            calls.append((args, timeout))
            return subprocess.CompletedProcess(args, 0, "device\n" if "get-state" in args else "", "")
        manager = self.manager(runner)
        manager._server_started = True
        self.assertEqual(manager.reconnect(), "device")
        self.assertEqual(calls[0][0][-1], "start-server")
        self.assertGreater(calls[0][1], 5)
        self.assertIn("connect", calls[1][0])

    def test_temporary_boot_probe_timeout_recovers_without_reactivating_app(self):
        boots = 0
        def runner(args, timeout):
            nonlocal boots
            if "getprop" in args:
                boots += 1
                if boots == 1:
                    raise CommandTimeout(args, timeout)
            output = "device\n" if "get-state" in args else "1\n" if "getprop" in args else ""
            return subprocess.CompletedProcess(args, 0, output, "")
        manager = self.manager(runner)
        with patch("desktop_manager.android.os.startfile") as activate, patch("desktop_manager.android.time.sleep"):
            self.assertFalse(manager.ensure_ready())
        self.assertEqual(boots, 2)
        activate.assert_not_called()

    def test_already_drawn_app_is_ready_on_first_probe_without_pid_or_sleep(self):
        calls = []
        def runner(args, timeout):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, READY, "")
        manager = self.manager(runner)
        with patch.object(manager, "ensure_ready", return_value=False), patch.object(manager, "is_installed", return_value=True), \
                patch("desktop_manager.android.os.startfile"), patch("desktop_manager.android.time.sleep") as sleep:
            manager.launch()
        sleep.assert_not_called()
        self.assertEqual(len(calls), 1)
        self.assertNotIn("pidof", calls[0])

    def test_failed_probe_retains_command_stage_and_budget(self):
        args = ["adb.exe", "-P", "5038", "connect", "127.0.0.1:58526"]
        with patch("desktop_manager.android.subprocess.run", side_effect=subprocess.TimeoutExpired(args, 3)):
            with self.assertRaises(CommandTimeout) as caught:
                run_process(args, timeout=3)
        self.assertEqual(caught.exception.args_run, args)
        self.assertIn("本机 Android", str(caught.exception))
        self.assertNotIn("网络", str(caught.exception))

    def test_shutdown_disconnect_is_success_even_if_adb_exits_255(self):
        manager = self.manager(Mock())
        with patch.object(manager, "state", side_effect=["device", "offline"]), \
                patch.object(manager, "command", return_value=subprocess.CompletedProcess([], 255, "", "")) as command, \
                patch("desktop_manager.android.open_wsa_settings") as settings:
            manager.shutdown()
        command.assert_called_once_with("shell", "svc", "power", "shutdown", timeout=5)
        settings.assert_not_called()

    def test_shutdown_without_adb_authorization_uses_wsa_settings_fallback(self):
        manager = self.manager(Mock())
        with patch.object(manager, "state", side_effect=["unauthorized", "offline"]), \
                patch.object(manager, "command") as command, \
                patch("desktop_manager.android.open_wsa_settings") as settings, \
                patch("desktop_manager.android.run_process", return_value=subprocess.CompletedProcess([], 0, "", "")):
            manager.shutdown()
        settings.assert_called_once_with()
        command.assert_not_called()


class StartupUiTests(IsolatedAsyncioTestCase):
    def app(self, list_apps):
        app = DesktopApp.__new__(DesktopApp)
        app.lock = asyncio.Lock()
        app.busy = app.stopping = False
        app.error_text = ""
        app.events = queue.Queue()
        app.settings = SimpleNamespace(auto_open=True)
        app.manager = SimpleNamespace(connection_state="device", installation=object(), list_apps=list_apps,
                                      state=Mock(side_effect=AssertionError("redundant state probe")))
        app.store = Mock()
        app.log = Mock()
        app.clear_window_input_targets = Mock()
        app.render = Mock()
        app.apps = []
        app.apps_loading = False
        app.apps_refresh_task = None
        return app

    async def test_slow_app_list_never_keeps_successful_launch_busy(self):
        release = threading.Event()
        started = asyncio.Event()
        loop = asyncio.get_running_loop()
        def list_apps():
            loop.call_soon_threadsafe(started.set)
            release.wait(2)
            return [{"package": HONGGUO, "name": "红果短剧"}]
        app = self.app(list_apps)
        try:
            result = await asyncio.wait_for(app.perform("打开应用", lambda: "opened"), timeout=1)
            await asyncio.wait_for(started.wait(), timeout=1)
            self.assertEqual(result, "opened")
            self.assertFalse(app.busy)
            self.assertTrue(app.connected)
            self.assertTrue(app.apps_loading)
            self.assertFalse(app.lock.locked())
            app.manager.state.assert_not_called()
        finally:
            release.set()
            await app.apps_refresh_task
        self.assertEqual(app.apps[0]["package"], HONGGUO)

    async def test_optional_app_list_failure_keeps_player_connected_and_ui_usable(self):
        app = self.app(Mock(side_effect=CommandTimeout(["shell", "cmd", "package"], 4)))
        await app.perform("打开应用", lambda: None)
        await app.apps_refresh_task
        self.assertFalse(app.busy)
        self.assertTrue(app.connected)
        self.assertEqual(app.error_text, "")
        self.assertFalse(app.apps_loading)

    async def test_auto_open_does_not_wait_for_application_metadata(self):
        app = self.app(Mock())
        app.store.load_warning = ""
        app.wsa_found = app.connected = True
        app.refresh = AsyncMock()
        app.launch_favorite = AsyncMock()
        await app.initialize()
        app.launch_favorite.assert_awaited_once_with(None)

    async def test_process_recovery_reconnects_without_reopening_or_navigating_player(self):
        app = self.app(Mock())
        app.store.load_warning = ""
        app.wsa_found = app.connected = True
        app.guardian = SimpleNamespace(recovery=True)
        app.refresh = AsyncMock()
        app.launch_favorite = AsyncMock()
        await app.initialize()
        app.refresh.assert_awaited_once_with(None)
        app.launch_favorite.assert_not_awaited()

    async def test_background_recovery_shows_its_own_status_without_global_loading(self):
        app = self.app(Mock())
        app.view = "home"
        app.home = app.apps_view = app.settings_view = app.activity_view = Mock(return_value=None)
        app.content = SimpleNamespace(content=None)
        app.progress = SimpleNamespace(visible=True)
        app.status = SimpleNamespace(value="")
        app.status_dot = SimpleNamespace(bgcolor="")
        app.page = Mock()
        app.wsa_found = True
        app.connected = False
        app.connection_recovering = True
        DesktopApp.render(app)
        self.assertIn("播放辅助", app.status.value)
        self.assertIn("恢复连接", app.status.value)
        self.assertFalse(app.progress.visible)
        app.connection_recovering = False
        app.connected = True
        DesktopApp.render(app)
        self.assertEqual(app.status.value, "Android 已连接")


class NativeStartupWindowTests(TestCase):
    def window(self, include_real=True):
        window = WsaPlayerWindow.__new__(WsaPlayerWindow)
        window.user = Mock()
        window.kernel = Mock()
        window.callback_type = lambda fn: fn
        windows = {20: ("com.phoenix.read(splash)", 100), 30: ("com.phoenix.read(splash)", 200)}
        if include_real:
            windows[10] = ("com.phoenix.read", 100)
        window.user.IsWindowVisible.return_value = True
        def enum(visit, _):
            for hwnd in windows:
                if not visit(hwnd, 0):
                    break
        window.user.EnumWindows.side_effect = enum
        window.user.GetWindowTextW.side_effect = lambda hwnd, buf, size: setattr(buf, "value", "红果免费短剧")
        window.user.GetClassNameW.side_effect = lambda hwnd, buf, size: setattr(buf, "value", windows[hwnd][0])
        window.user.GetWindowThreadProcessId.side_effect = lambda hwnd, ptr: setattr(ptr._obj, "value", windows[hwnd][1])
        window.kernel.QueryFullProcessImageNameW.side_effect = lambda handle, flags, buf, size: (setattr(buf, "value", "C:/WSA/WsaClient.exe") or True)
        return window

    def test_loading_window_is_never_selected_as_the_player(self):
        self.assertEqual(self.window().find(), 10)

    def test_only_stale_loader_of_the_verified_player_process_is_hidden(self):
        window = self.window()
        window.hide_loaded_splashes()
        window.user.ShowWindow.assert_called_once_with(20, 0)

    def test_loader_is_left_visible_until_a_real_player_exists(self):
        window = self.window(include_real=False)
        self.assertIsNone(window.find())
        window.hide_loaded_splashes()
        window.user.ShowWindow.assert_not_called()
