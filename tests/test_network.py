import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from desktop_manager.android import AndroidManager, OperationError, WsaInstallation
from desktop_manager.config import Settings, SettingsStore


class NetworkTests(unittest.TestCase):
    def manager(self, values, *, reject=False):
        calls = []

        def run(args, timeout):
            calls.append(args)
            action = args[7:]
            if action == ["list", "global"]:
                output = "\n".join(f"{k}={v}" for k, v in values.items())
            else:
                operation, _, key, *rest = action
                if not reject:
                    if operation == "put":
                        values[key] = rest[0]
                    elif operation == "delete":
                        values.pop(key, None)
                output = ""
            return subprocess.CompletedProcess(args, 0, output, "")

        return AndroidManager(Settings(), runner=run, adb=Path("adb.exe")), calls

    def test_explicit_proxy_and_pac_are_removed_only_on_the_wsa_device(self):
        values = {"http_proxy": "old.example:8080", "global_http_proxy_host": "old.example",
                  "global_http_proxy_port": "8080", "global_proxy_pac_url": "https://old.example/pac",
                  "global_http_proxy_exclusion_list": "localhost", "unrelated": "preserved"}
        manager, calls = self.manager(values)
        manager.configure_direct_network()
        self.assertEqual(values, {"http_proxy": ":0", "unrelated": "preserved"})
        self.assertTrue(all(args[1:5] == ["-P", "5038", "-s", "127.0.0.1:58526"] for args in calls))
        self.assertTrue(all(args[5:8] == ["shell", "settings", "list"] or args[5:7] == ["shell", "settings"] for args in calls))

    def test_correct_configuration_is_not_rewritten_on_repeated_launches(self):
        manager, calls = self.manager({"http_proxy": ":0", "global_http_proxy_host": "null",
                                      "global_http_proxy_port": "0", "global_proxy_pac_url": ""})
        manager.configure_direct_network()
        manager.configure_direct_network()
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(args[-2:] == ["list", "global"] for args in calls))

    def test_successful_command_without_actual_change_is_rejected(self):
        manager, _ = self.manager({"http_proxy": "old.example:8080"}, reject=True)
        with self.assertRaisesRegex(OperationError, "未确认"):
            manager.configure_direct_network()

    def test_permission_failure_is_not_reported_as_direct(self):
        manager, _ = self.manager({})
        with patch.object(manager, "command", return_value=subprocess.CompletedProcess([], 0, "SecurityException", "")):
            with self.assertRaisesRegex(OperationError, "无法读取"):
                manager.configure_direct_network()

    def test_ready_connection_applies_preference_and_failure_does_not_prevent_startup(self):
        messages = []
        manager = AndroidManager(Settings(), notify=messages.append, adb=Path("adb.exe"))
        manager.installation = WsaInstallation(Path("WSA"), "WSA_family")
        with patch.object(manager, "state", return_value="device"), \
                patch.object(manager, "command", return_value=subprocess.CompletedProcess([], 0, "1\n", "")), \
                patch.object(manager, "configure_direct_network", side_effect=OperationError("测试权限失败")) as apply, \
                patch("desktop_manager.android.os.startfile") as activate:
            self.assertFalse(manager.ensure_ready())
        apply.assert_called_once_with(timeout=2)
        activate.assert_not_called()
        self.assertTrue(any("未完成" in message for message in messages))

    def test_disabled_preference_keeps_network_configuration(self):
        manager = AndroidManager(Settings(keep_wsa_direct=False), adb=Path("adb.exe"))
        manager.installation = WsaInstallation(Path("WSA"), "WSA_family")
        with patch.object(manager, "state", return_value="device"), \
                patch.object(manager, "command", return_value=subprocess.CompletedProcess([], 0, "1\n", "")), \
                patch.object(manager, "configure_direct_network") as apply:
            manager.ensure_ready()
        apply.assert_not_called()

    def test_preference_migrates_saves_and_validates_without_resetting_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"settings.json"
            path.write_text('{"preferred_quality":"1080","hide_titlebar":true}', encoding="utf-8")
            store = SettingsStore(path)
            settings = store.load()
            self.assertTrue(settings.keep_wsa_direct)
            settings.keep_wsa_direct = False
            store.save(settings)
            self.assertFalse(store.load().keep_wsa_direct)
            self.assertEqual(store.load().preferred_quality, "1080")
            self.assertTrue(store.load().hide_titlebar)
            with self.assertRaises(ValueError):
                Settings(keep_wsa_direct="false").validate()


if __name__ == "__main__":
    unittest.main()
