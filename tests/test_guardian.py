import ctypes
from ctypes import wintypes as wt
from pathlib import Path
import sys
from unittest import TestCase
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from desktop_manager.guardian import GuardianLink


class GuardianLinkTests(TestCase):
    def test_development_interpreter_never_starts_packaged_guardian(self):
        with patch("desktop_manager.guardian.subprocess.Popen") as launch:
            link = GuardianLink(Mock(), executable="python.exe")
            link.pulse()
            link.request_stop()
            self.assertFalse(link.enabled)
            launch.assert_not_called()

    def test_stop_signal_is_shared_with_independent_process_and_heartbeat_is_auto_reset(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        with patch("desktop_manager.guardian.subprocess.Popen"):
            link = GuardianLink(Mock(), executable=Path("fake") / "hongguo_desktop.exe")
        try:
            self.assertEqual(kernel.WaitForSingleObject(link.heartbeat, 0), 0)
            self.assertEqual(kernel.WaitForSingleObject(link.heartbeat, 0), 258)
            self.assertEqual(kernel.WaitForSingleObject(link.stop, 0), 258)
            link.request_stop()
            self.assertEqual(kernel.WaitForSingleObject(link.stop, 0), 0)
            self.assertEqual(kernel.WaitForSingleObject(link.stop, 0), 0)
        finally:
            link.close()

    def test_missing_supervisor_retries_without_spawning_every_heartbeat(self):
        with patch("desktop_manager.guardian.subprocess.Popen", side_effect=FileNotFoundError) as launch, \
                patch.object(GuardianLink, "alive", return_value=False), \
                patch("desktop_manager.guardian.time.monotonic", return_value=20):
            link = GuardianLink(Mock(), executable=Path("fake") / "hongguo_desktop.exe")
            try:
                link.pulse()
                self.assertEqual(launch.call_count, 1)
                with patch("desktop_manager.guardian.time.monotonic", return_value=31):
                    link.pulse()
                self.assertEqual(launch.call_count, 2)
            finally:
                link.close()
