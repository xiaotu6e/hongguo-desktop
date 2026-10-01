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


if __name__=='__main__':
    unittest.main()
