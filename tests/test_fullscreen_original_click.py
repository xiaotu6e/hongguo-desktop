from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from main import DesktopApp
from desktop_manager.config import Settings
from desktop_manager.playback_assistant import PlaybackAssistant, Screen

class OriginalClickTests(TestCase):
    def helper(self):
        helper=PlaybackAssistant(Mock(settings=Settings()),Mock(),history=Mock())
        helper.bridge=Mock()
        helper.screen=Screen('player','用户正在看的剧',53,[{'bounds':[0,0,692,1230]}])
        helper.activate_fullscreen=Mock();helper.prepare_landscape=Mock()
        return helper

    def test_already_delivered_click_never_replays_or_overwrites_selection_history(self):
        helper=self.helper()
        wide=Screen('player','',0,[{'bounds':[0,0,1600,900]}],True)
        with patch('desktop_manager.playback_assistant.read_screen',return_value=wide):
            self.assertTrue(helper.observe_fullscreen_click(1)['ok'])
        self.assertTrue(helper.last_landscape['ok'])
        self.assertEqual(helper.last_landscape['input'],'app_original_click')
        helper.activate_fullscreen.assert_not_called();helper.prepare_landscape.assert_not_called()
        helper.bridge.click.assert_not_called();helper.history.record.assert_not_called()
        self.assertTrue(helper.policy.decided)

    def test_delayed_orientation_does_not_replay_and_exit_consumes_auto_entry(self):
        helper=self.helper()
        with patch('desktop_manager.playback_assistant.read_screen',return_value=helper.screen):
            helper.observe_fullscreen_click(1)
        self.assertTrue(helper.landscape_preparing)
        helper._advance_landscape(helper.screen,1.8)
        helper.bridge.click.assert_not_called();helper.activate_fullscreen.assert_not_called()
        helper.cancel_landscape()
        self.assertTrue(helper.fullscreen_done)
        self.assertFalse(helper.landscape_preparing)

    def test_initialized_geometry_expires_on_android_process_change(self):
        app=DesktopApp.__new__(DesktopApp)
        app.landscape_initialized=(42,'9496');app.landscape_checked_at=0
        app.window_chrome=SimpleNamespace(hwnd=42);app.manager=Mock()
        app.manager.command.return_value=SimpleNamespace(returncode=0,stdout='9501\n')
        with patch('main.time.monotonic',return_value=10):
            app.refresh_landscape_process(42)
            self.assertFalse(app.native_player_fullscreen_ready())
        self.assertIsNone(app.landscape_initialized)

    def test_valid_cache_has_short_lease_and_a_new_hwnd_is_not_reused(self):
        app=DesktopApp.__new__(DesktopApp)
        app.landscape_initialized=(42,'9496');app.landscape_checked_at=10
        app.window_chrome=SimpleNamespace(hwnd=42)
        with patch('main.time.monotonic',return_value=12):
            self.assertTrue(app.native_player_fullscreen_ready())
        with patch('main.time.monotonic',return_value=17):
            self.assertFalse(app.native_player_fullscreen_ready())
        app.window_chrome.hwnd=43
        with patch('main.time.monotonic',return_value=12):
            self.assertFalse(app.native_player_fullscreen_ready())
