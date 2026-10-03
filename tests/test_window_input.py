from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from desktop_manager.window_input import WindowInput
from desktop_manager.ui_bridge import UiBridge
from desktop_manager.playback_assistant import PlaybackAssistant, Screen
from desktop_manager.android import OperationError
from desktop_manager.config import Settings


class WindowInputTests(TestCase):
    def test_original_fullscreen_click_is_only_observed_and_never_replayed(self):
        playback=Mock();chrome=Mock()
        chrome.take_fullscreen.return_value='observed'
        playback.observe_fullscreen_click.return_value={'ok':True,'method':'fullscreen_observed'}
        self.assertEqual(WindowInput(playback,chrome).dispatch()['method'],'fullscreen_observed')
        playback.observe_fullscreen_click.assert_called_once_with()
        playback.request_landscape.assert_not_called()
        playback.bridge.click.assert_not_called()
        chrome.take_wheel.assert_not_called()

    def test_only_initialized_player_passes_the_real_click_to_wsa(self):
        for kind,ready in [('player',True),('player',False),('feed',True)]:
            with self.subTest(kind=kind,ready=ready):
                chrome=Mock()
                playback=Mock(screen=Screen(kind,'剧名',53,self.overlap_nodes()))
                input=WindowInput(playback,chrome,lambda:ready)
                rect=input.refresh_targets()
                if kind=='player' and ready:
                    chrome.set_fullscreen_target.assert_called_once_with(rect,passthrough=True)
                else:
                    chrome.set_fullscreen_target.assert_called_once_with(rect)
                input.clear_targets()
                self.assertFalse(input.fullscreen_passthrough)

    def test_normal_page_click_only_requests_observation_without_android_actions(self):
        playback=Mock();chrome=Mock()
        chrome.take_fullscreen.return_value=False;chrome.take_wheel.return_value=None
        chrome.take_page_change.return_value=True
        self.assertEqual(WindowInput(playback,chrome).dispatch(),{'ok':True,'method':'page_refresh'})
        playback.bridge.click.assert_not_called();playback.bridge.wheel.assert_not_called()
        playback.request_landscape.assert_not_called();playback.tick.assert_not_called()

    def overlap_nodes(self):
        return [{'path':'0','bounds':[0,0,800,1200]},
                {'path':'0/1','bounds':[300,700,500,750],'clickable':True,'enabled':True},
                {'path':'0/1/0','bounds':[340,710,480,740],'text':'全屏观看','enabled':True},
                {'path':'0/2','bounds':[280,735,620,780],'clickable':True,'enabled':True},
                {'path':'0/2/0','bounds':[400,740,600,770],'text':'共34万人在追','enabled':True}]

    def test_feed_overlap_leaves_gap_and_does_not_cover_or_capture_series_tag(self):
        nodes=self.overlap_nodes();chrome=Mock()
        input=WindowInput(Mock(screen=Screen('feed','剧名',3,nodes)),chrome)
        rect=input.refresh_targets()
        shown=input.fullscreen_presentation
        self.assertEqual(shown['button_bounds'],[300,685,500,727])
        self.assertEqual(shown['original_bounds'],[300,700,500,750])
        self.assertEqual(shown['tag_bounds'],[280,735,620,780])
        self.assertGreaterEqual(shown['tag_bounds'][1]-shown['button_bounds'][3],8)
        self.assertLess(rect[3],round(735*65535/1200))
        mask=chrome.set_fullscreen_presentation.call_args.args[0]
        self.assertEqual(mask[3],round(735*65535/1200))

    def test_nonoverlapping_tag_player_and_disabled_tag_keep_original_fullscreen(self):
        for kind,tag_y,enabled in [('feed',780,True),('player',735,True),('feed',735,False)]:
            with self.subTest(kind=kind,tag_y=tag_y,enabled=enabled):
                nodes=self.overlap_nodes()
                nodes[3]=dict(nodes[3],bounds=[280,tag_y,620,tag_y+45],enabled=enabled)
                input=WindowInput(Mock(screen=Screen(kind,'剧名',3,nodes)),Mock())
                rect=input.refresh_targets()
                self.assertIsNone(input.fullscreen_presentation)
                self.assertEqual(rect[1],round(700*65535/1200))
                input.chrome.set_fullscreen_presentation.assert_called_once_with(None)

    def test_relocated_feed_button_is_removed_during_navigation_or_preparation(self):
        playback=Mock(screen=Screen('feed','剧名',3,self.overlap_nodes()))
        input=WindowInput(playback,Mock());input.refresh_targets()
        self.assertIsNotNone(input.fullscreen_presentation)
        playback.stage='landscape_prepare'
        self.assertIsNone(input.refresh_targets())
        self.assertIsNone(input.fullscreen_presentation)
        self.assertEqual(input.chrome.set_fullscreen_presentation.call_args.args,(None,))

    def test_every_page_receives_wheel_without_waiting_for_presentation_or_polling(self):
        for kind,landscape,playing in [('feed',False,True),('player',False,False),
                                      ('player',True,True),('player',False,True),
                                      ('episodes',False,False),('quality',False,True)]:
            with self.subTest(kind=kind,landscape=landscape):
                playback=Mock(screen=SimpleNamespace(kind=kind,landscape=landscape),snapshot_data={'playing':playing})
                chrome=Mock();chrome.take_fullscreen.return_value=False;chrome.take_wheel.return_value=(14000,32000,-120)
                WindowInput(playback,chrome).dispatch()
                playback.bridge.wheel.assert_called_once_with(14000,32000,-120)
                playback.tick.assert_not_called()
                if kind=='player':playback.manual_episode_change.assert_called_once()
                else:playback.manual_episode_change.assert_not_called()

    def test_expired_or_cancelled_wheel_does_not_replay(self):
        playback=Mock();chrome=Mock();chrome.take_fullscreen.return_value=False;chrome.take_wheel.return_value=None
        WindowInput(playback,chrome).dispatch()
        playback.bridge.wheel.assert_not_called();playback.tick.assert_not_called()

    def test_scroll_boundary_does_not_trigger_a_fallback_tap_or_swipe(self):
        playback=Mock(screen=SimpleNamespace(kind='episodes'))
        playback.bridge.wheel.return_value={'ok':False,'edge':True}
        chrome=Mock();chrome.take_fullscreen.return_value=False;chrome.take_wheel.return_value=(32000,55000,120)
        result=WindowInput(playback,chrome).dispatch()
        self.assertTrue(result['edge'])
        playback.bridge.wheel.assert_called_once()
        playback.bridge.click.assert_not_called()
        playback.bridge.back.assert_not_called()

    def test_outside_pointer_or_invalid_delta_never_sends_an_android_command(self):
        bridge=UiBridge(Mock());bridge.request=Mock()
        for values in [(-1,100,120),(100,65536,120),(100,100,0),(100,100,1000)]:
            self.assertFalse(bridge.wheel(*values)['ok'])
        bridge.request.assert_not_called()

    def test_fullscreen_click_requests_preparation_once_without_overwriting_wheel(self):
        playback=Mock()
        playback.request_landscape.return_value={'ok':True,'method':'landscape_prepare'}
        chrome=Mock();chrome.take_fullscreen.return_value=True
        result=WindowInput(playback,chrome).dispatch()
        self.assertTrue(result['ok'])
        playback.request_landscape.assert_called_once_with()
        chrome.set_fullscreen_target.assert_called_once_with(None)
        chrome.take_wheel.assert_not_called()
        playback.bridge.wheel.assert_not_called()

    def test_fullscreen_target_covers_only_closest_enabled_button_in_feed_and_player(self):
        nodes=[{'path':'0','bounds':[600,400,1400,1600]},
               {'path':'0/0','bounds':[600,400,1400,1600],'clickable':True},
               {'path':'0/0/1','bounds':[900,1100,1100,1150],'clickable':True,'enabled':True},
               {'path':'0/0/1/0','bounds':[940,1110,1090,1140],'text':'全屏观看','enabled':True}]
        for kind in ('feed','player'):
            with self.subTest(kind=kind):
                playback=Mock(screen=Screen(kind,'',0,nodes))
                chrome=Mock()
                rect=WindowInput(playback,chrome).refresh_targets()
                self.assertEqual(rect,(24576,38229,40959,40959))
                chrome.set_fullscreen_target.assert_called_once_with(rect)

    def test_fullscreen_target_is_cleared_for_dialog_landscape_or_disabled_button(self):
        nodes=[{'path':'0','bounds':[0,0,800,1200]},
               {'path':'0/1','bounds':[300,700,500,750],'clickable':True,'enabled':True},
               {'path':'0/1/0','bounds':[310,705,490,745],'text':'全屏观看','enabled':True}]
        for screen in (Screen('episodes','',0,nodes),Screen('player','',0,nodes,True),
                       Screen('feed','',0,[nodes[0],dict(nodes[1],enabled=False),nodes[2]])):
            playback=Mock(screen=screen);chrome=Mock()
            self.assertIsNone(WindowInput(playback,chrome).refresh_targets())
            chrome.set_fullscreen_target.assert_called_once_with(None)

    def test_malformed_fullscreen_ancestor_does_not_capture_the_entire_video(self):
        nodes=[{'path':'0','bounds':[0,0,800,1200],'clickable':True},
               {'path':'0/0','bounds':[300,700,500,750],'text':'全屏观看','enabled':True}]
        playback=Mock(screen=Screen('feed','',0,nodes));chrome=Mock()
        self.assertIsNone(WindowInput(playback,chrome).refresh_targets())
        chrome.set_fullscreen_target.assert_called_once_with(None)

    def test_landscape_preparation_does_not_intercept_a_second_fullscreen_click(self):
        nodes=[{'path':'0','bounds':[0,0,800,1200]},
               {'path':'0/1','bounds':[300,700,500,750],'clickable':True,'enabled':True},
               {'path':'0/1/0','bounds':[310,705,490,745],'text':'全屏观看','enabled':True}]
        playback=Mock(screen=Screen('feed','',0,nodes),stage='landscape_prepare')
        chrome=Mock()
        self.assertIsNone(WindowInput(playback,chrome).refresh_targets())
        chrome.set_fullscreen_target.assert_called_once_with(None)

    def test_unconfirmed_fullscreen_handler_result_is_reported_and_not_replayed(self):
        playback=Mock();playback.request_landscape.return_value=None
        chrome=Mock();chrome.take_fullscreen.return_value=True
        with self.assertRaises(OperationError):
            WindowInput(playback,chrome).dispatch()
        chrome.set_fullscreen_target.assert_called_once_with(None)
        playback.bridge.click.assert_not_called()

    def test_feed_wheel_cancels_pending_landscape_before_scrolling_to_another_drama(self):
        playback=PlaybackAssistant(Mock(settings=Settings()),Mock(),history=Mock())
        playback.screen=Screen('feed','当前推荐剧',3,[])
        playback.stage='landscape_prepare'
        playback.landscape_preparing=True
        playback.landscape_origin=('feed','当前推荐剧',3)
        playback.bridge=Mock()
        def scroll(*packet):
            self.assertFalse(playback.landscape_preparing)
            self.assertEqual(playback.stage,'')
            return {'ok':True}
        playback.bridge.wheel.side_effect=scroll
        chrome=Mock();chrome.take_fullscreen.return_value=False
        chrome.take_wheel.return_value=(32000,32000,-120)
        self.assertTrue(WindowInput(playback,chrome).dispatch()['ok'])
        self.assertTrue(playback.fullscreen_done)
        playback.bridge.click.assert_not_called()
