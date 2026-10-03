import ctypes
from pathlib import Path
import sys
from unittest import TestCase
from unittest.mock import Mock, call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from desktop_manager.playback_window import WsaPlayerWindow


class NativeFullscreenSafetyTests(TestCase):
    def test_deferred_click_keeps_pointer_until_app_outcome_then_restores_once(self):
        window=self.window()
        with patch('desktop_manager.playback_window.time.sleep'):
            self.assertTrue(window.tap_control(5,[0,0,1600,900],[750,820,880,855],defer_restore=True))
        self.assertEqual(window.user.SetCursorPos.call_count,1)
        self.assertIsNotNone(window._pending_tap_pointer)
        window.finish_tap_control()
        self.assertEqual(window.user.SetCursorPos.call_args,call(10,20))
        self.assertIsNone(window._pending_tap_pointer)
        count=window.user.SetCursorPos.call_count
        window.finish_tap_control()
        self.assertEqual(window.user.SetCursorPos.call_count,count)

    def test_deferred_restore_respects_user_pointer_movement(self):
        window=self.window()
        with patch('desktop_manager.playback_window.time.sleep'):
            self.assertTrue(window.tap_control(5,[0,0,1600,900],[750,820,880,855],defer_restore=True))
        window.user.SetCursorPos(100,200)
        window.finish_tap_control()
        self.assertEqual(window.user.SetCursorPos.call_args,call(100,200))
        self.assertIsNone(window._pending_tap_pointer)

    def window(self):
        window = WsaPlayerWindow.__new__(WsaPlayerWindow)
        u = window.user = Mock()
        u.GetForegroundWindow.return_value = 5
        u.GetAncestor.side_effect = lambda h, _: 5 if h in (5, 7) else h
        u.WindowFromPoint.return_value = 7
        u.IsIconic.return_value = False
        u.GetAsyncKeyState.return_value = 0
        u.SetThreadDpiAwarenessContext.return_value = 1
        u.GetSystemMetrics.side_effect = lambda key: {76:0,77:0,78:3840,79:2160}[key]
        cursor = [10, 20]

        def client(_, pointer):
            pointer._obj.right, pointer._obj.bottom = 1600, 900
            return True

        def screen(_, pointer):
            pointer._obj.x += 50
            pointer._obj.y += 50
            return True

        def read_cursor(pointer):
            pointer._obj.x, pointer._obj.y = cursor
            return True

        def move(x, y):
            cursor[:] = [x, y]
            return True

        u.GetClientRect.side_effect = client
        u.ClientToScreen.side_effect = screen
        u.GetCursorPos.side_effect = read_cursor
        u.SetCursorPos.side_effect = move
        return window

    def test_changed_focus_never_moves_or_clicks_the_pointer(self):
        window = self.window()
        window.user.GetForegroundWindow.return_value = 99
        self.assertFalse(window.tap_control(5, [0, 0, 1600, 900], [750, 820, 880, 855]))
        window.user.mouse_event.assert_not_called()
        window.user.SetCursorPos.assert_not_called()

    def test_occluded_target_never_sends_mouse_input(self):
        window = self.window()
        window.user.WindowFromPoint.return_value = 99
        self.assertFalse(window.tap_control(5, [0, 0, 1600, 900], [750, 820, 880, 855]))
        window.user.mouse_event.assert_not_called()
        window.user.SetCursorPos.assert_not_called()

    def test_outside_button_never_uses_window_apis(self):
        window = self.window()
        self.assertFalse(window.tap_control(5, [0, 0, 1600, 900], [750, 950, 880, 980]))
        window.user.GetForegroundWindow.assert_not_called()

    def test_one_click_uses_client_coordinates_and_restores_the_pointer(self):
        window = self.window()
        with patch('desktop_manager.playback_window.time.sleep'):
            self.assertTrue(window.tap_control(5, [0, 0, 1600, 900], [750, 820, 880, 855]))
        events = window.user.mouse_event.call_args_list
        self.assertEqual([event.args[0] for event in events], [0xC001, 0xC002, 0xC004])
        self.assertEqual(events[0].args[1:], events[1].args[1:])
        self.assertEqual(events[1].args[1:], events[2].args[1:])
        self.assertEqual(window.user.SetCursorPos.call_args_list[-1], call(10, 20))

    def test_user_pointer_movement_cancels_before_button_down_and_is_preserved(self):
        window = self.window()
        def moved(seconds):
            if seconds == .05:
                window.user.SetCursorPos(100, 200)
        with patch('desktop_manager.playback_window.time.sleep', side_effect=moved):
            self.assertFalse(window.tap_control(5, [0, 0, 1600, 900], [750, 820, 880, 855]))
        self.assertEqual([c.args[0] for c in window.user.mouse_event.call_args_list], [0xC001])
        self.assertEqual(window.user.SetCursorPos.call_args_list[-1], call(100, 200))
