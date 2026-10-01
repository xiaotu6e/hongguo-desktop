from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from desktop_manager.config import Settings
from desktop_manager.playback_assistant import PlaybackAssistant, WatchHistory, read_screen
from main import DesktopApp, visible_app_pid


def window_session(pid="321", package="com.phoenix.read", visible=True, drawn=True):
    return (f"  Window #0 Window{{abcd u0 {package}/MainActivity}}:\n"
            f"    mSession=Session{{efgh {pid}:u0a10070}}\n"
            f"    isVisible={'true' if visible else 'false'}\n"
            f"    mDrawState={'HAS_DRAWN' if drawn else 'NO_SURFACE'}\n")


def snapshot(kind="player", width=692, height=1230, title="测试剧", episode=3, shift=0):
    def node(name, text="", bounds=None):
        return {"path": "0/"+name, "id": "com.phoenix.read:id/"+name,
                "text": text, "desc": "", "class": "android.widget.TextView",
                "enabled": True, "clickable": True,
                "bounds": bounds or [0, 0, width, min(height, 40)]}
    nodes = [{"path": "0", "id": "", "text": "", "desc": "", "enabled": True,
              "bounds": [0, 0, width, height]}]
    if kind == "land":
        nodes += [node("f8h"), node("h8p")]
    else:
        if kind == "feed":
            nodes += [node("arc"), node("gbx"), node("h1h", f"第{episode}集")]
        else:
            nodes += [node("kea"), node("k_4", f"第{episode}集")]
        nodes += [node("d4", title), node("jmk", "全屏观看",
                    [width//2-60+shift, height//2-15, width//2+60+shift, height//2+15])]
    return {"nodes": nodes, "ok": True}


class LandscapePreparationTests(TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.prepare = Mock(return_value=True)
        self.helper = PlaybackAssistant(
            Mock(settings=Settings(preferred_quality="keep", start_rule="keep")), Mock(),
            WatchHistory(Path(self.directory.name)/"history.json"), prepare_landscape=self.prepare)
        self.helper.bridge = Mock()
        self.helper.bridge.click.return_value = True

    def source(self, kind="player"):
        value = snapshot(kind)
        self.helper.screen = read_screen(value)
        self.helper.bridge.snapshot.return_value = value
        return value

    def ready_and_click(self, kind="player"):
        self.helper.bridge.snapshot.return_value = snapshot(kind, 1600, 900)
        self.helper.tick(5.1)
        self.helper.bridge.click.assert_not_called()
        self.helper.tick(5.39)
        self.helper.bridge.click.assert_not_called()
        newest = snapshot(kind, 1600, 900, shift=15)
        self.helper.bridge.snapshot.return_value = newest
        self.helper.tick(5.41)
        self.assertIs(self.helper.bridge.click.call_args.args[0], newest["nodes"][-1])
        self.assertTrue(self.helper.landscape_preparing)
        self.helper.tick(5.6)
        self.helper.bridge.click.assert_called_once()

    def test_automatic_fullscreen_prepares_size_then_uses_a_new_settled_node_once(self):
        self.source()
        self.helper.title = self.helper.policy.active = "测试剧"
        self.helper.policy.decided = True
        self.helper.stage = "fullscreen"
        self.helper.tick(5)
        self.prepare.assert_called_once()
        self.helper.bridge.click.assert_not_called()
        self.assertTrue(self.helper.landscape_preparing)
        self.ready_and_click()
        self.helper.bridge.snapshot.return_value = snapshot("land", 1600, 900)
        self.helper.tick(5.7)
        self.assertFalse(self.helper.landscape_preparing)
        self.source()
        self.helper.tick(6)
        self.helper.bridge.click.assert_called_once()  # User exited fullscreen.

    def test_explicit_feed_fullscreen_works_with_automatic_fullscreen_disabled(self):
        self.helper.manager.settings.auto_fullscreen = False
        self.helper.last_settings = self.helper.preferences()
        self.source("feed")
        self.helper.tick(4)
        self.helper.bridge.click.assert_not_called()
        self.assertTrue(self.helper.request_landscape(now=5)["ok"])
        self.ready_and_click("feed")
        self.assertFalse(self.helper.history.items)
        self.helper.bridge.snapshot.return_value = snapshot("land", 1600, 900)
        self.helper.tick(5.7)
        self.assertEqual(self.helper.policy.active, "测试剧")
        self.assertTrue(self.helper.policy.decided)

    def test_changed_title_or_episode_rejects_stale_native_intent_before_resizing(self):
        for title, episode in [("另一部剧", 3), ("测试剧", 4)]:
            with self.subTest(title=title, episode=episode):
                self.source("feed")
                self.helper.bridge.snapshot.return_value = snapshot("feed", title=title, episode=episode)
                self.assertFalse(self.helper.request_landscape(now=5)["ok"])
                self.prepare.assert_not_called()
                self.helper.bridge.click.assert_not_called()

    def test_navigation_during_preparation_cancels_without_late_click(self):
        self.source("feed")
        self.helper.request_landscape(now=5)
        self.helper.bridge.snapshot.return_value = snapshot("feed", 1600, 900, title="另一部剧")
        self.helper.tick(5.5)
        self.helper.tick(6)
        self.assertFalse(self.helper.landscape_preparing)
        self.helper.bridge.click.assert_not_called()

    def test_episode_transition_cancellation_retains_its_exact_reason_and_geometry(self):
        self.source()
        self.helper.request_landscape(now=5)
        self.helper.bridge.snapshot.return_value = snapshot("player", 1600, 900, episode=4)
        self.helper.tick(5.1)
        state = self.helper.last_landscape
        self.assertEqual(state["reason"], "page_changed")
        self.assertEqual(state["origin"]["episode"], 3)
        self.assertEqual(state["current"]["episode"], 4)
        self.assertEqual(state["root_bounds"], [0, 0, 1600, 900])
        self.assertIs(state["callback"], True)
        self.assertFalse(state["clicked"])
        self.helper.bridge.click.assert_not_called()

    def test_unresized_click_failure_is_distinct_from_preparation_failure(self):
        self.source()
        self.prepare.return_value = None
        self.helper.bridge.click.return_value = False
        self.assertFalse(self.helper.request_landscape(now=5)["ok"])
        self.assertEqual(self.helper.last_landscape["reason"], "click_rejected")
        self.assertIsNone(self.helper.last_landscape["callback"])
        self.assertTrue(self.helper.last_landscape["clicked"])

    def test_preparation_deadline_does_not_resize_or_click_forever(self):
        self.source("feed")
        self.helper.request_landscape(now=5)
        self.helper.tick(9.1)
        self.helper.tick(10)
        self.assertFalse(self.helper.landscape_preparing)
        self.prepare.assert_called_once()
        self.helper.bridge.click.assert_not_called()

    def test_reset_and_explicit_wheel_cancellation_remove_pending_request(self):
        self.source("feed")
        self.helper.request_landscape(now=5)
        self.helper.cancel_landscape()
        self.assertFalse(self.helper.landscape_preparing)
        self.source("feed")
        self.helper.request_landscape(now=6)
        self.helper.reset()
        self.assertFalse(self.helper.landscape_preparing)
        self.assertEqual(self.helper.stage, "")
        self.helper.bridge.click.assert_not_called()

    def test_failed_prepare_callback_cannot_leave_a_request_for_later_replay(self):
        self.source("feed")
        self.prepare.side_effect = OSError("window unavailable")
        with self.assertRaises(OSError):
            self.helper.request_landscape(now=5)
        self.assertFalse(self.helper.landscape_preparing)
        self.helper.tick(6)
        self.helper.bridge.click.assert_not_called()

    def test_lost_fullscreen_reply_does_not_send_another_fullscreen_action(self):
        self.source("feed")
        self.helper.request_landscape(now=5)
        self.helper.bridge.snapshot.return_value = snapshot("feed", 1600, 900)
        self.helper.tick(5.1)
        self.helper.bridge.click.side_effect = OSError("response lost")
        with self.assertRaises(OSError):
            self.helper.tick(5.5)
        self.helper.tick(5.7)
        self.helper.bridge.click.assert_called_once()
        self.assertFalse(self.helper.landscape_preparing)

    def test_cached_route_uses_a_fresh_node_without_waiting_for_a_wide_source_root(self):
        self.source("feed")
        self.prepare.return_value = "cached"
        self.helper.request_landscape(now=5)
        self.helper.bridge.click.assert_not_called()
        newest = snapshot("feed", shift=15)
        self.helper.bridge.snapshot.return_value = newest
        self.helper.tick(5.1)
        self.assertIs(self.helper.bridge.click.call_args.args[0], newest["nodes"][-1])
        self.helper.bridge.snapshot.return_value = snapshot("land", 692, 1230)
        self.helper.tick(5.2)
        self.assertTrue(self.helper.landscape_preparing)
        self.helper.bridge.snapshot.return_value = snapshot("land", 615, 346)
        self.helper.tick(5.3)
        self.assertFalse(self.helper.landscape_preparing)
        self.assertEqual(self.helper.last_landscape["reason"], "landscape_entered")
        self.helper.bridge.click.assert_called_once()

    def test_cached_route_still_cancels_a_changed_page_before_clicking(self):
        self.source("feed")
        self.prepare.return_value = "cached"
        self.helper.request_landscape(now=5)
        self.helper.bridge.snapshot.return_value = snapshot("feed", title="另一部剧")
        self.helper.tick(5.1)
        self.assertFalse(self.helper.landscape_preparing)
        self.helper.bridge.click.assert_not_called()


class WindowPreparationTests(TestCase):
    def app(self, mode="auto", iconic=False, zoomed=False):
        app = DesktopApp.__new__(DesktopApp)
        app.stopping = app.busy = False
        app.settings = Settings(window_mode=mode)
        app.playback = SimpleNamespace(landscape_preparing=True)
        app.manager = Mock()
        app.manager.command.return_value = subprocess.CompletedProcess([], 0, window_session(), "")
        app.player_window = Mock()
        app.player_window.find.return_value = 123
        app.player_window.user.IsIconic.return_value = iconic
        app.player_window.user.IsZoomed.return_value = zoomed
        app.player_window.resize.return_value = True
        app.window_chrome = Mock(geometry_holding=False)
        app.window_chrome.begin_geometry_change.return_value = True
        app.last_window_layout = None
        return app

    def test_prepared_landscape_is_not_shrunk_back_using_old_portrait_snapshot(self):
        app = self.app()
        app.fit_player_window()
        app.player_window.find.assert_not_called()
        app.player_window.resize.assert_not_called()

    def test_fixed_portrait_minimization_and_maximization_are_preserved(self):
        for mode, iconic, zoomed, width, height, expected in [
                ("portrait", False, False, 692, 1230, None),
                ("auto", True, False, 692, 1230, False),
                ("auto", False, True, 1600, 900, True),
                ("auto", False, True, 692, 1230, None)]:
            with self.subTest(mode=mode, iconic=iconic, zoomed=zoomed):
                app = self.app(mode, iconic, zoomed)
                result = app.prepare_landscape_window(read_screen(snapshot(width=width, height=height)))
                self.assertIs(result, expected)
                app.player_window.resize.assert_not_called()
                self.assertEqual(app.last_landscape_window["iconic"], iconic)
                self.assertEqual(app.last_landscape_window["zoomed"], zoomed)

    def test_auto_and_landscape_size_before_activity_creation(self):
        for mode in ["auto", "landscape"]:
            with self.subTest(mode=mode):
                app = self.app(mode)
                self.assertTrue(app.prepare_landscape_window(read_screen(snapshot())))
                self.assertEqual(app.last_window_layout, (123, mode, True))
                self.assertTrue(app.player_window.resize.call_args.args[2])

    def test_hidden_title_is_restored_before_resize_and_no_resize_on_restore_timeout(self):
        app = self.app()
        order = Mock()
        order.attach_mock(app.window_chrome.begin_geometry_change, "restore_caption")
        order.attach_mock(app.player_window.resize, "resize")
        self.assertTrue(app.prepare_landscape_window(read_screen(snapshot())))
        self.assertEqual([call[0] for call in order.mock_calls], ["restore_caption", "resize"])
        app = self.app()
        app.window_chrome.begin_geometry_change.return_value = False
        self.assertFalse(app.prepare_landscape_window(read_screen(snapshot())))
        app.player_window.resize.assert_not_called()
        self.assertEqual(app.last_landscape_window["reason"], "caption_restore_timeout")

    def test_finish_releases_hold_only_after_preparation_and_never_during_shutdown(self):
        app = self.app()
        app.window_chrome.geometry_holding = True
        app.finish_window_geometry()
        app.window_chrome.end_geometry_change.assert_not_called()
        app.playback.landscape_preparing = False
        app.finish_window_geometry()
        app.window_chrome.end_geometry_change.assert_called_once_with(True)
        app.window_chrome.end_geometry_change.reset_mock()
        app.stopping = True
        app.finish_window_geometry()
        app.window_chrome.end_geometry_change.assert_not_called()

    def test_visible_title_needs_no_presentation_change_for_resize(self):
        app = self.app()
        app.settings.hide_titlebar = False
        self.assertTrue(app.prepare_landscape_window(read_screen(snapshot())))
        app.window_chrome.begin_geometry_change.assert_not_called()

    def test_cached_geometry_requires_the_same_native_window_and_android_process(self):
        app = self.app()
        app.landscape_initialized = (123, "321")
        self.assertEqual(app.prepare_landscape_window(read_screen(snapshot())), "cached")
        app.window_chrome.begin_geometry_change.assert_not_called()
        app.player_window.resize.assert_not_called()
        app.manager.command.return_value = subprocess.CompletedProcess([], 0, window_session("322"), "")
        self.assertIs(app.prepare_landscape_window(read_screen(snapshot())), True)
        app.window_chrome.begin_geometry_change.assert_called_once()
        self.assertIsNone(app.landscape_initialized)

    def test_cache_is_trusted_only_after_successful_actual_wide_landscape(self):
        app = self.app()
        app.landscape_candidate = (123, "321")
        app.playback.landscape_preparing = False
        app.playback.last_landscape = {"reason": "landscape_entered", "ok": True}
        app.playback.screen = read_screen(snapshot("land", 692, 1230))
        app.finish_window_geometry()
        self.assertIsNone(getattr(app, "landscape_initialized", None))
        app.landscape_candidate = (123, "321")
        app.playback.screen = read_screen(snapshot("land", 1600, 900))
        app.finish_window_geometry()
        self.assertEqual(app.landscape_initialized, (123, "321"))

    def test_one_transient_direction_does_not_resize_and_a_stable_direction_does(self):
        app = self.app()
        app.playback.landscape_preparing = False
        app.playback.screen = read_screen(snapshot("land", 1600, 900))
        app.last_window_layout = (123, "auto", True)
        app.playback.screen = read_screen(snapshot())
        with patch("main.time.monotonic", return_value=5):
            app.fit_player_window()
        app.player_window.resize.assert_not_called()
        app.playback.screen = read_screen(snapshot("land", 1600, 900))
        app.fit_player_window()
        self.assertIsNone(app.pending_window_layout)
        app.playback.screen = read_screen(snapshot())
        with patch("main.time.monotonic", return_value=6):
            app.fit_player_window()
        with patch("main.time.monotonic", return_value=6.19):
            app.fit_player_window()
        app.player_window.resize.assert_called_once()

    def test_confirmed_landscape_can_fit_immediately_without_an_extra_debounce(self):
        app = self.app()
        app.playback.landscape_preparing = False
        app.playback.screen = read_screen(snapshot("land", 615, 346))
        app.playback.last_landscape = {"reason": "landscape_entered", "ok": True}
        app.fit_player_window()
        app.player_window.resize.assert_called_once()


class VisibleAppPidTests(TestCase):
    def test_only_drawn_visible_exact_package_window_supplies_the_process(self):
        dump = (window_session("1631", visible=False)
                + window_session("1669", package="com.phoenix.read.worker")
                + window_session("1700", drawn=False)
                + window_session("1284"))
        self.assertEqual(visible_app_pid(dump), "1284")

    def test_splash_and_missing_session_never_create_a_trusted_marker(self):
        splash = window_session("1631").replace("com.phoenix.read/MainActivity", "Splash Screen com.phoenix.read")
        missing = window_session().replace("mSession=Session{efgh 321:u0a10070}", "")
        self.assertIsNone(visible_app_pid(splash+missing))
