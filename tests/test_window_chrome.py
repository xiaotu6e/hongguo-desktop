import ctypes
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from desktop_manager.window_chrome import WindowChrome


class WindowChromeTests(unittest.TestCase):
    def test_page_change_hint_requires_a_fresh_native_reply_and_does_not_click(self):
        chrome=self.chrome();chrome.grip=Mock(return_value=99)
        def message(handle,message,wp,lp,flags,timeout,result):
            result._obj.value=1
            return 1
        chrome.window.user.SendMessageTimeoutW.side_effect=message
        self.assertTrue(chrome.take_page_change())
        self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_args.args[:4],(99,0x801f,0,0))
        chrome.window.user.SendMessageTimeoutW.side_effect=None
        chrome.window.user.SendMessageTimeoutW.return_value=0
        self.assertFalse(chrome.take_page_change())
        chrome.window.user.PostMessageW.assert_not_called()
    def test_feed_presentation_only_sends_bounded_mask_and_clears_it(self):
        chrome=self.chrome();chrome.grip=Mock(return_value=99)
        self.assertTrue(chrome.set_fullscreen_presentation((1200,33000,4000,37000)))
        self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_args.args[:4],
                         (99,0x801d,1200|(33000<<16),4000|(37000<<16)))
        chrome.set_fullscreen_presentation((1200,33000,4000,37000))
        self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_count,1)
        self.assertTrue(chrome.set_fullscreen_presentation(None))
        self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_args.args[:4],(99,0x801d,0,0))
        for rect in ((0,0,65536,10),(0,20,10,10),(0.,0,10,10)):
            with self.assertRaises(ValueError):chrome.set_fullscreen_presentation(rect)

    def chrome(self):
        chrome = object.__new__(WindowChrome)
        chrome.process = Mock(pid=51)
        chrome.process.poll.return_value = None
        chrome.hwnd = 42
        chrome.hide_titlebar = True
        chrome.geometry_holding = False
        chrome.input_enabled = True
        chrome.input_event = 1
        chrome._grip = None
        chrome._fullscreen_target_key = None
        chrome._fullscreen_target_sent_at = 0.0
        chrome._lifecycle_lock = threading.RLock()
        chrome.window = SimpleNamespace(user=Mock())
        chrome.kernel = Mock()
        return chrome

    def test_showing_title_keeps_input_process_and_changes_only_presentation(self):
        chrome = self.chrome()
        chrome.grip = Mock(return_value=99)
        chrome.close = Mock()
        chrome.sync(42, False)
        chrome.close.assert_not_called()
        chrome.window.user.PostMessageW.assert_called_once_with(99, 0x8018, 0, 0)
        self.assertFalse(chrome.hide_titlebar)
        chrome.sync(42, False)
        self.assertEqual(chrome.window.user.PostMessageW.call_count, 1)

    def test_grip_enumeration_skips_a_different_companion_and_owner(self):
        chrome = self.chrome()
        user = chrome.window.user
        user.IsWindow.return_value = True
        def process_for(handle, pid):
            pid._obj.value = {10: 52, 11: 51, 12: 51}[handle]
            return 1
        user.GetWindowThreadProcessId.side_effect = process_for
        user.GetWindow.side_effect = lambda handle, _: 43 if handle == 11 else 42
        def class_for(handle, name, length):
            name.value = "HongguoPlayerDragArea"
            return len(name.value)
        user.GetClassNameW.side_effect = class_for
        def enumerate_windows(visit, param):
            for handle in (10, 11, 12):
                if not visit(handle, param):
                    break
            return 1
        user.EnumWindows.side_effect = enumerate_windows
        self.assertEqual(chrome.grip(), 12)
        self.assertEqual(chrome.grip(), 12)
        self.assertEqual(user.EnumWindows.call_count, 1)

    def test_heartbeat_keeps_lease_while_dispatcher_is_occupied(self):
        chrome = self.chrome()
        chrome.grip = Mock(return_value=99)
        stopped = threading.Event()
        pulse = threading.Thread(target=chrome._heartbeat, args=(stopped,))
        try:
            pulse.start()
            # Simulates a dispatcher blocked on the page lock/Android request;
            # no wait_input calls are made while the lease keeps refreshing.
            time.sleep(.46)
            self.assertGreaterEqual(chrome.window.user.SendMessageTimeoutW.call_count, 3)
            self.assertTrue(all(call.args[2] == 1 for call in
                                chrome.window.user.SendMessageTimeoutW.call_args_list))
            chrome.input_enabled = False
            time.sleep(.23)
            self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_args[0][2], 0)
        finally:
            stopped.set()
            pulse.join(timeout=1)
        self.assertFalse(pulse.is_alive())

    def test_wait_input_changes_enabled_state_without_tying_lease_to_its_wait(self):
        chrome = self.chrome()
        chrome.kernel.WaitForSingleObject.return_value = 0
        self.assertTrue(chrome.wait_input(False, 30))
        self.assertFalse(chrome.input_enabled)
        chrome.window.user.SendMessageTimeoutW.assert_not_called()

    def test_diagnostics_report_an_unresponsive_component(self):
        chrome = self.chrome()
        chrome.grip = Mock(return_value=99)
        chrome.window.user.SendMessageTimeoutW.return_value = 0
        self.assertTrue(chrome.input_status()["message_timeout"])
        chrome.window.user.PostMessageW.assert_not_called()

    def test_fullscreen_target_packs_separate_content_coordinates_and_can_be_cleared(self):
        chrome = self.chrome()
        chrome.grip = Mock(return_value=99)
        self.assertTrue(chrome.set_fullscreen_target((1200, 33000, 4000, 37000)))
        message = chrome.window.user.SendMessageTimeoutW.call_args.args
        self.assertEqual(message[:4], (99, 0x801a, 1200 | (33000 << 16), 4000 | (37000 << 16)))
        self.assertTrue(chrome.set_fullscreen_target(None))
        self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_args.args[:4], (99, 0x801a, 0, 0))

    def test_invalid_fullscreen_target_is_never_sent_to_native_hook(self):
        chrome = self.chrome()
        chrome.grip = Mock(return_value=99)
        for rect in ((0, 0, 65536, 10), (10, 20, 0, 30), (0, 10, 20, 10), (0., 0, 20, 10)):
            with self.subTest(rect=rect), self.assertRaises(ValueError):
                chrome.set_fullscreen_target(rect)
        chrome.window.user.SendMessageTimeoutW.assert_not_called()

    def test_fullscreen_request_is_consumed_only_when_native_confirms_a_fresh_click(self):
        chrome = self.chrome()
        chrome.grip = Mock(return_value=99)
        def message(handle, message, wp, lp, flags, timeout, result):
            result._obj.value = 1
            return 1
        chrome.window.user.SendMessageTimeoutW.side_effect = message
        self.assertTrue(chrome.take_fullscreen())
        self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_args.args[:4], (99, 0x801b, 0, 0))
        chrome.window.user.SendMessageTimeoutW.side_effect = None
        chrome.window.user.SendMessageTimeoutW.return_value = 0
        self.assertFalse(chrome.take_fullscreen())

    def test_original_click_has_distinct_packet_and_switching_mode_refreshes_target(self):
        chrome=self.chrome();chrome.grip=Mock(return_value=99)
        rect=(1200,33000,4000,37000)
        chrome.set_fullscreen_target(rect)
        chrome.set_fullscreen_target(rect,passthrough=True)
        self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_count,2)
        self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_args.args[2],
                         1200|(33000<<16)|(1<<32))
        def reply(*args):
            args[-1]._obj.value=2
            return 1
        chrome.window.user.SendMessageTimeoutW.side_effect=reply
        self.assertEqual(chrome.take_fullscreen(),'observed')

    def test_geometry_hold_keeps_title_visible_while_user_preference_still_hides_it(self):
        chrome = self.chrome()
        chrome.geometry_holding = True
        chrome.grip = Mock(return_value=99)
        chrome.sync(42, True)
        self.assertFalse(chrome.hide_titlebar)
        chrome.window.user.PostMessageW.assert_called_once_with(99, 0x8018, 0, 0)
        chrome.end_geometry_change(True)
        self.assertFalse(chrome.geometry_holding)
        self.assertTrue(chrome.hide_titlebar)
        self.assertEqual(chrome.window.user.PostMessageW.call_args.args, (99, 0x8018, 1, 0))

    def test_begin_geometry_waits_for_caption_and_companion_restore_ack(self):
        chrome = self.chrome()
        chrome.grip = Mock(return_value=99)
        chrome.window.user.IsWindow.return_value = True
        chrome.window.user.GetWindowLongPtrW.side_effect = (0, 0xC00000, 0xC00000)
        acknowledged = [False, True]
        def message(handle, message, wp, lp, flags, timeout, result):
            result._obj.value = 8 if acknowledged.pop(0) else 0
            return 1
        chrome.window.user.SendMessageTimeoutW.side_effect = message
        with patch('desktop_manager.window_chrome.time.sleep'):
            self.assertTrue(chrome.begin_geometry_change())
        self.assertTrue(chrome.geometry_holding)
        self.assertFalse(chrome.hide_titlebar)
        self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_count, 2)

    def test_geometry_restore_timeout_stays_held_until_cleanup(self):
        chrome = self.chrome()
        chrome.grip = Mock(return_value=99)
        chrome.window.user.IsWindow.return_value = True
        chrome.window.user.GetWindowLongPtrW.return_value = 0
        with patch('desktop_manager.window_chrome.time.monotonic', side_effect=(0., 0., 1.7)), \
                patch('desktop_manager.window_chrome.time.sleep'):
            self.assertFalse(chrome.begin_geometry_change())
        self.assertTrue(chrome.geometry_holding)
        chrome.end_geometry_change(False)
        self.assertFalse(chrome.geometry_holding)
        self.assertFalse(chrome.hide_titlebar)

    def test_geometry_cleanup_does_not_restart_an_already_closed_companion(self):
        chrome = self.chrome()
        chrome.geometry_holding = True
        chrome.process = None
        chrome.hwnd = None
        chrome._sync = Mock()
        chrome.end_geometry_change(True)
        self.assertFalse(chrome.geometry_holding)
        chrome._sync.assert_not_called()

    def test_unchanged_button_skips_redundant_ipc_and_refreshes_its_native_lease(self):
        chrome = self.chrome()
        chrome.grip = Mock(return_value=99)
        rect = (1200,33000,4000,37000)
        with patch('desktop_manager.window_chrome.time.monotonic', side_effect=(100.,100.3,100.8)):
            self.assertTrue(chrome.set_fullscreen_target(rect))
            self.assertTrue(chrome.set_fullscreen_target(list(rect)))
            self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_count,1)
            self.assertTrue(chrome.set_fullscreen_target(rect))
        self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_count,2)

    def test_repeated_clear_does_not_send_ipc_until_companion_or_owner_changes(self):
        chrome = self.chrome()
        chrome.grip = Mock(return_value=99)
        with patch('desktop_manager.window_chrome.time.monotonic', side_effect=(100.,105.,106.,107.)):
            self.assertTrue(chrome.set_fullscreen_target(None))
            self.assertTrue(chrome.set_fullscreen_target(None))
            self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_count,1)
            chrome.hwnd=43
            self.assertTrue(chrome.set_fullscreen_target(None))
            chrome.process=Mock(pid=52)
            self.assertTrue(chrome.set_fullscreen_target(None))
        self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_count,3)

    def test_failed_target_send_is_retried_and_missing_grip_invalidates_cache(self):
        chrome=self.chrome();chrome.grip=Mock(return_value=99)
        chrome.window.user.SendMessageTimeoutW.return_value=0
        self.assertFalse(chrome.set_fullscreen_target(None))
        chrome.window.user.SendMessageTimeoutW.return_value=1
        self.assertTrue(chrome.set_fullscreen_target(None))
        self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_count,2)
        chrome.grip.return_value=None
        self.assertFalse(chrome.set_fullscreen_target(None))
        chrome.grip.return_value=99
        self.assertTrue(chrome.set_fullscreen_target(None))
        self.assertEqual(chrome.window.user.SendMessageTimeoutW.call_count,3)


if __name__ == "__main__":
    unittest.main()
