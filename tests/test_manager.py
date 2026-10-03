import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from desktop_manager.android import AndroidManager, OperationError, WsaInstallation, app_window_drawn
from desktop_manager.apk_info import ApkInfo, inspect_apk
from desktop_manager.config import HONGGUO, Settings, SettingsStore
from desktop_manager.playback_window import fitted_rect, player_layout
from desktop_manager.window_chrome import caption_dip


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.responses = []
        self.clock = 0.0
        clock_patch = patch("desktop_manager.android.time.monotonic", side_effect=lambda: self.clock)
        clock_patch.start()
        self.addCleanup(clock_patch.stop)
        def sleep(seconds):
            self.clock += seconds
        sleep_patch = patch("desktop_manager.android.time.sleep", side_effect=sleep)
        sleep_patch.start()
        self.addCleanup(sleep_patch.stop)
        def run(args, timeout):
            self.clock += 1
            self.calls.append(args)
            code, out, err = self.responses.pop(0) if self.responses else (0, "", "")
            return subprocess.CompletedProcess(args, code, out, err)
        self.manager = AndroidManager(Settings(keep_wsa_direct=False), runner=run, adb=Path("adb.exe"))
        self.manager.installation = WsaInstallation(Path("WSA"), "WSA_family")
        self.manager._server_started = True

    def test_ready_wsa_does_not_activate_an_app_or_files_to_connect(self):
        self.responses = [(0, "", ""), (0, "device\n", ""), (0, "1\n", "")]
        with patch("desktop_manager.android.os.startfile") as activate:
            self.assertFalse(self.manager.ensure_ready())
        activate.assert_not_called()

    def test_cold_connect_wakes_the_favorite_without_opening_files(self):
        self.responses = [(0, "", ""), (1, "", "offline"),
                          (0, "", ""), (0, "device\n", ""), (0, "1\n", "")]
        with patch("desktop_manager.android.os.startfile") as activate:
            self.assertTrue(self.manager.ensure_ready())
        activate.assert_called_once_with("wsa://" + self.manager.settings.favorite)

    def test_cold_custom_app_launch_reuses_the_target_wake_activation(self):
        package = "com.example.player"
        ready = f'  Window #1 Window{{123 u0 {package}/MainActivity}}:\n mHasSurface=true mDrawState=HAS_DRAWN\n'
        self.responses = [(0, "", ""), (1, "", "offline"),
                          (0, "", ""), (0, "device\n", ""), (0, "1\n", ""),
                          (0, "package:/data/app/player.apk\n", ""),
                          (0, ready, "")]
        with patch("desktop_manager.android.os.startfile") as activate, patch("desktop_manager.android.time.sleep"):
            self.manager.launch(package)
        activate.assert_called_once_with("wsa://" + package)

    def test_cold_install_wakes_the_favorite_and_keeps_its_package_validation(self):
        info = ApkInfo(Path("C:/example/app.apk"), "com.example.newapp", "测试应用", "1", 1)
        self.responses = [(0, "", ""), (1, "", "offline"),
                          (0, "", ""), (0, "device\n", ""), (0, "1\n", ""),
                          (0, "Success\n", ""), (0, "package:/data/app/new.apk\n", "")]
        with patch("desktop_manager.android.inspect_apk", return_value=info), \
                patch("desktop_manager.android.os.startfile") as activate:
            self.assertIs(self.manager.install(info.path), info)
        activate.assert_called_once_with("wsa://" + HONGGUO)
        self.assertTrue(any("install" in call and "-r" in call for call in self.calls))

    def test_missing_custom_app_keeps_the_install_prompt_after_a_cold_wake(self):
        package = "com.example.missing"
        self.responses = [(0, "", ""), (1, "", "offline"),
                          (0, "", ""), (0, "device\n", ""), (0, "1\n", ""), (1, "", "")]
        with patch("desktop_manager.android.os.startfile") as activate:
            with self.assertRaisesRegex(OperationError, "尚未安装"):
                self.manager.launch(package)
        activate.assert_called_once_with("wsa://" + package)

    def test_unauthorized_connection_does_not_open_an_extra_app_window(self):
        self.responses = [(0, "", ""), (1, "", "unauthorized")]
        with patch("desktop_manager.android.os.startfile") as activate:
            with self.assertRaisesRegex(OperationError, "允许"):
                self.manager.ensure_ready()
        activate.assert_not_called()

    def test_all_device_commands_target_wsa_even_when_usb_phone_present(self):
        self.responses = [(0, "device\n", ""), (0, "package:com.phoenix.read\n", ""), (0, "", "")]
        self.assertEqual(self.manager.state(), "device")
        self.manager.list_apps()
        self.manager.stop_app(HONGGUO)
        for command in self.calls:
            self.assertEqual(command[1:5], ["-P", "5038", "-s", "127.0.0.1:58526"])

    def test_emulator_configuration_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "只管理 WSA"):
            Settings(engine="emulator").validate()

    def test_existing_wsa_settings_stay_on_wsa(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"adb_port":58526,"auto_open":true,"favorite":"com.phoenix.read"}', encoding="utf-8")
            settings = SettingsStore(path).load()
            self.assertEqual(settings.endpoint, "127.0.0.1:58526")
            self.assertTrue(settings.auto_open)
            self.assertTrue(settings.hide_titlebar)

    def test_titlebar_preference_is_saved_and_rejects_string_booleans(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            store.save(Settings(hide_titlebar=False))
            self.assertFalse(store.load().hide_titlebar)
            with self.assertRaises(ValueError):
                store.save(Settings(hide_titlebar="false"))
            self.assertFalse(store.load().hide_titlebar)

    def test_retired_crop_setting_does_not_reset_other_preferences_or_survive_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"immersive_playback": True, "start_rule": "keep",
                                       "preferred_quality": "720", "auto_open": False}), encoding="utf-8")
            store = SettingsStore(path)
            settings = store.load()
            self.assertFalse(store.load_warning)
            self.assertEqual(settings.start_rule, "keep")
            self.assertEqual(settings.preferred_quality, "720")
            self.assertFalse(settings.auto_open)
            store.save(settings)
            self.assertNotIn("immersive_playback", json.loads(path.read_text(encoding="utf-8")))

    def test_title_height_uses_monitor_dpi_and_ignores_transient_popup_size(self):
        self.assertEqual(caption_dip(692, 1275, 692, 1230, 144), 30)
        self.assertEqual(caption_dip(800, 1564, 800, 1500, 192), 32)
        self.assertEqual(caption_dip(1600, 900, 300, 500, 144), 30)
        self.assertEqual(caption_dip(800, 900, 800, 1200, 96), 30)

    def test_emulator_settings_migrate_back_without_launching(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text('{"engine":"emulator","auto_open":true}', encoding="utf-8")
            settings = SettingsStore(path).load()
            self.assertEqual(settings.engine, "wsa")
            self.assertFalse(settings.auto_open)

    def test_invalid_runtime_settings_are_rejected(self):
        for settings in (Settings(startup_timeout=0), Settings(engine="other"), Settings(adb_port=1), Settings(window_mode="stretch")):
            with self.assertRaises(ValueError):
                settings.validate()

    def test_rejects_shell_metacharacters_before_running_adb(self):
        with self.assertRaises(OperationError):
            self.manager.stop_app("com.example.app; reboot")
        self.assertEqual(self.calls, [])

    def test_install_requires_success_and_installed_package(self):
        info = ApkInfo(Path("C:/example/app.apk"), HONGGUO, "红果短剧", "1", 1)
        self.responses = [(0, "Success\n", ""), (0, "", "")]
        with patch("desktop_manager.android.inspect_apk", return_value=info), patch.object(self.manager, "ensure_ready"):
            with self.assertRaisesRegex(OperationError, "未能确认"):
                self.manager.install(info.path)
        self.assertIn("-r", self.calls[0])
        self.assertNotIn("-d", self.calls[0])
        self.assertNotIn("-g", self.calls[0])

    def test_signature_mismatch_preserves_existing_app(self):
        info = ApkInfo(Path("C:/example/app.apk"), HONGGUO, "红果短剧", "1", 1)
        self.responses = [(1, "", "Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]")]
        with patch("desktop_manager.android.inspect_apk", return_value=info), patch.object(self.manager, "ensure_ready"):
            with self.assertRaisesRegex(OperationError, "签名不同"):
                self.manager.install(info.path)
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("uninstall", self.calls[0])

    def test_unauthorized_is_distinguished_from_offline(self):
        self.responses = [(1, "", "error: device unauthorized")]
        self.assertEqual(self.manager.state(), "unauthorized")

    def test_config_roundtrip_and_corrupt_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            settings = Settings(auto_open=False, startup_timeout=45)
            store.save(settings)
            self.assertEqual(store.load(), settings)
            path.write_text('{"adb_port": "58526"}', encoding="utf-8")
            self.assertEqual(store.load(), Settings())
            self.assertTrue(store.load_warning)

    def test_invalid_port_does_not_overwrite_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            store = SettingsStore(path)
            store.save(Settings())
            with self.assertRaises(ValueError):
                store.save(Settings(adb_port=5038))
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["adb_port"], 58526)

    def test_encrypted_windows_folder_can_save_when_rename_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory) / "settings.json")
            error = OSError("Cross-volume rename")
            error.winerror = 17
            with patch.object(Path, "replace", side_effect=error):
                store.save(Settings(auto_open=False))
            self.assertFalse(store.load().auto_open)

    def test_apk_manifest_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            apk = Path(directory) / "中文安装包.apk"
            with zipfile.ZipFile(apk, "w") as archive:
                archive.writestr("AndroidManifest.xml", '<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="com.example.app" android:versionName="2.0" android:versionCode="2"><application android:label="Example" /></manifest>')
            info = inspect_apk(apk)
            self.assertEqual((info.package, info.name, info.version), ("com.example.app", "Example", "2.0"))
            apk.write_bytes(b"broken")
            with self.assertRaisesRegex(ValueError, "损坏"):
                inspect_apk(apk)

    def test_display_settings_reject_unknown_values(self):
        with self.assertRaises(OperationError):
            self.manager.apply_display_settings("9.0", "1")
        self.assertEqual(self.calls, [])

    def test_splash_screen_is_not_reported_as_ready(self):
        splash = '  Window #5 Window{123 u0 Splash Screen com.phoenix.read}:\n mHasSurface=true mDrawState=HAS_DRAWN\n'
        self.assertFalse(app_window_drawn(splash, HONGGUO))
        ready = '  Window #5 Window{123 u0 com.phoenix.read/com.example.MainActivity}:\n mHasSurface=true mDrawState=HAS_DRAWN\n'
        self.assertTrue(app_window_drawn(ready, HONGGUO))

    def test_cold_boot_dropped_activation_is_retried_before_process_exists(self):
        ready = '  Window #5 Window{123 u0 com.phoenix.read/com.example.MainActivity}:\n mHasSurface=true mDrawState=HAS_DRAWN\n'
        self.responses = [(0, "", ""), (1, "", "")] * 4 + [(0, ready, "")]
        with patch.object(self.manager, "ensure_ready", return_value=False), patch.object(self.manager, "is_installed", return_value=True), \
                patch("desktop_manager.android.os.startfile") as activate, patch("desktop_manager.android.time.sleep"):
            self.manager.launch(HONGGUO)
        self.assertEqual(activate.call_count, 2)
        self.assertTrue(all(call.args == ("wsa://" + HONGGUO,) for call in activate.call_args_list))

    def test_running_app_with_a_splash_is_not_repeatedly_activated(self):
        splash = '  Window #5 Window{123 u0 Splash Screen com.phoenix.read}:\n mHasSurface=true mDrawState=HAS_DRAWN\n'
        self.responses = [(0, splash, ""), (0, "123\n", "")] * 30
        with patch.object(self.manager, "ensure_ready", return_value=False), patch.object(self.manager, "is_installed", return_value=True), \
                patch("desktop_manager.android.os.startfile") as activate, patch("desktop_manager.android.time.sleep"):
            with self.assertRaises(OperationError):
                self.manager.launch(HONGGUO)
        activate.assert_called_once_with("wsa://" + HONGGUO)

    def test_landscape_detection_uses_visible_player_not_previous_activity(self):
        hidden = '  Window #1 Window{x u0 com.phoenix.read/com.dragon.read.component.shortvideo.impl.fullscreen.ShortSeriesLandActivity}:\n Requested w=1600 h=900\n isVisible=false mDrawState=NO_SURFACE\n'
        feed = '  Window #2 Window{y u0 com.phoenix.read/com.dragon.read.pages.main.MainFragmentActivity}:\n Requested w=1600 h=900\n isVisible=true mDrawState=HAS_DRAWN\n'
        layout = player_layout(hidden + feed)
        self.assertFalse(layout.landscape)
        self.assertEqual((layout.width, layout.height), (1600, 900))
        self.assertTrue(player_layout(hidden.replace('isVisible=false', 'isVisible=true').replace('NO_SURFACE', 'HAS_DRAWN') + feed).landscape)

    def test_other_apps_and_splash_do_not_trigger_window_adaptation(self):
        self.assertIsNone(player_layout('  Window #1 Window{x u0 com.other.app/Main}:\n Requested w=1600 h=900\n isVisible=true mDrawState=HAS_DRAWN\n'))
        self.assertIsNone(player_layout('  Window #1 Window{x u0 Splash Screen com.phoenix.read}:\n Requested w=1600 h=900\n isVisible=true mDrawState=HAS_DRAWN\n'))

    def test_window_fits_small_and_negative_coordinate_monitors_without_stretching(self):
        for work in [(0, 0, 1366, 728), (-1920, -1080, 0, -40)]:
            for landscape in [True, False]:
                x, y, width, height = fitted_rect(work, (3000, 3000, 3600, 4000), landscape, 144, (22, 56))
                self.assertGreaterEqual(x, work[0])
                self.assertGreaterEqual(y, work[1])
                self.assertLessEqual(x + width, work[2])
                self.assertLessEqual(y + height, work[3])
                self.assertAlmostEqual((width - 22) / (height - 56), 16 / 9 if landscape else 9 / 16, delta=.003)


if __name__ == "__main__":
    unittest.main()
