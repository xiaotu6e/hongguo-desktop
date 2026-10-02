from pathlib import Path
import sys
import unittest
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from desktop_manager.playback_window import PlayerLayout,WsaPlayerWindow


class PlaybackWindowTests(unittest.TestCase):
    def window(self, bounds=(400,300,2000,1200)):
        window=object.__new__(WsaPlayerWindow)
        user=window.user=Mock()
        user.IsIconic.return_value=False
        user.IsZoomed.return_value=False
        user.GetDpiForWindow.return_value=144
        user.GetWindowLongPtrW.return_value=0
        user.SetThreadDpiAwarenessContext.return_value=123
        def current(hwnd, rect):
            rect._obj.left,rect._obj.top,rect._obj.right,rect._obj.bottom=bounds
            return True
        def monitor(handle,info):
            info._obj.work.left,info._obj.work.top,info._obj.work.right,info._obj.work.bottom=(0,0,3840,2120)
            return True
        user.GetWindowRect.side_effect=current
        user.GetMonitorInfoW.side_effect=monitor
        return window

    def test_same_target_size_and_position_does_not_send_another_resize(self):
        window=self.window()
        self.assertTrue(window.resize(42,PlayerLayout(True,1600,900),True))
        window.user.SetWindowPos.assert_not_called()
        self.assertEqual(window.user.SetThreadDpiAwarenessContext.call_args.args,(123,))

    def test_manual_same_target_still_restores_the_user_window(self):
        window=self.window()
        window.user.IsIconic.return_value=True
        self.assertTrue(window.resize(42,PlayerLayout(True,1600,900),True,manual=True))
        window.user.ShowWindow.assert_called_once_with(42,9)
        window.user.SetWindowPos.assert_not_called()

    def test_same_size_outside_monitor_still_moves_back_into_view(self):
        window=self.window(bounds=(-100,300,1500,1200))
        self.assertTrue(window.resize(42,PlayerLayout(True,1600,900),True))
        window.user.SetWindowPos.assert_called_once_with(42,None,24,300,1600,900,0x14)

    def test_user_landscape_size_is_used_instead_of_default_after_orientation_switch(self):
        window=self.window()
        window.user.GetPropW.return_value=360
        self.assertTrue(window.resize(42,PlayerLayout(True,1600,900),True))
        call=window.user.SetWindowPos.call_args.args
        self.assertEqual(call[4:6],(960,540))
        window.user.GetPropW.assert_called_once_with(42,'HongguoLandscapeHeightDip')

    def test_user_portrait_size_is_independent_from_landscape_size(self):
        window=self.window()
        window.user.GetPropW.return_value=640
        self.assertTrue(window.resize(42,PlayerLayout(False,692,1230),False))
        self.assertEqual(window.user.SetWindowPos.call_args.args[4:6],(540,960))
        window.user.GetPropW.assert_called_once_with(42,'HongguoPortraitHeightDip')

    def test_saved_size_is_fitted_to_smaller_monitor(self):
        window=self.window()
        window.user.GetPropW.return_value=4000
        self.assertTrue(window.resize(42,PlayerLayout(False,692,1230),False))
        args=window.user.SetWindowPos.call_args.args
        self.assertLessEqual(args[3]+args[5],2096)
        self.assertLessEqual(abs(args[4]-args[5]*9/16),.51)


if __name__=='__main__':
    unittest.main()
