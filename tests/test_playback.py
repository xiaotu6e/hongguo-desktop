import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from desktop_manager.config import Settings, SettingsStore
from desktop_manager.playback_assistant import EntryPolicy, PlaybackAssistant, Screen, WatchHistory, read_screen

CHECKS = Path(__file__).resolve().parent / "fixtures"


def page(kind, episode=3, title="测试剧"):
    return Screen(kind, title, episode, [])


class EntryTests(unittest.TestCase):
    def decision(self, entered=3, watched=False, settings=None):
        policy = EntryPolicy()
        settings = settings or Settings()
        self.assertIsNone(policy.observe(page("feed"), 0, settings, watched))
        self.assertIsNone(policy.observe(page("player", entered), 1, settings, watched))
        return policy, policy.observe(page("player", entered), 3, settings, watched)

    def test_matching_recommendation_starts_first_once(self):
        policy, decision = self.decision()
        self.assertTrue(decision.jump)
        self.assertIsNone(policy.observe(page("player", 1), 5, Settings(), False))
        self.assertIsNone(policy.observe(page("player", 2), 8, Settings(), False))
        self.assertIsNone(policy.observe(page("player", 9), 11, Settings(), False))

    def test_different_entered_episode_and_watched_series_preserve_progress(self):
        self.assertFalse(self.decision(entered=8)[1].jump)
        self.assertFalse(self.decision(watched=True)[1].jump)
        self.assertFalse(self.decision(settings=Settings(start_rule="keep"))[1].jump)
        self.assertTrue(self.decision(watched=True, settings=Settings(protect_history=False))[1].jump)

    def test_resume_changes_while_entry_settles(self):
        p, s = EntryPolicy(), Settings()
        p.observe(page("feed"), 0, s, False)
        p.observe(page("player"), 1, s, False)
        self.assertIsNone(p.observe(page("player", 8), 2, s, False))
        self.assertFalse(p.observe(page("player", 8), 4, s, False).jump)

    def test_caption_replaced_with_hot_comment_retains_number(self):
        p, s = EntryPolicy(), Settings()
        p.observe(page("feed", 6), 0, s, False)
        p.observe(page("feed", 0), 10, s, False)
        p.observe(page("player", 6), 11, s, False)
        self.assertTrue(p.observe(page("player", 6), 13, s, False).jump)

    def test_unknown_episode_other_title_and_expired_feed_do_not_jump(self):
        for preview, enter, delay in [(page("feed", 0), page("player"), 1),
                                      (page("feed"), page("player", title="其他剧"), 1),
                                      (page("feed"), page("player"), 20)]:
            p, s = EntryPolicy(), Settings()
            p.observe(preview, 0, s, False)
            p.observe(enter, delay, s, False)
            self.assertFalse(p.observe(enter, delay+2, s, False).jump)

    def test_already_open_player_is_not_treated_as_feed_entry(self):
        p, s = EntryPolicy(), Settings()
        p.observe(page("player", 18), 0, s, False)
        self.assertFalse(p.observe(page("player", 18), 2, s, False).jump)

    def test_only_possible_rewinds_wait_for_resume(self):
        p, s = EntryPolicy(), Settings()
        p.observe(page("feed"), 0, s, False)
        p.observe(page("player"), 1, s, False)
        self.assertIsNone(p.observe(page("player"), 1.4, s, False))
        self.assertIsNone(p.observe(page("player"), 2.7, s, False))
        self.assertTrue(p.observe(page("player"), 2.9, s, False).jump)
        for watched, current in [(True, 3), (False, 8), (False, 1)]:
            p = EntryPolicy()
            p.observe(page("feed"), 0, s, watched)
            p.observe(page("player", current), 1, s, watched)
            self.assertFalse(p.observe(page("player", current), 1.4, s, watched).jump)

    def test_saved_preferences_and_local_history_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SettingsStore(Path(directory)/"settings.json")
            s = Settings(start_rule="keep", preferred_quality="720", auto_fullscreen=False, protect_history=False)
            store.save(s)
            self.assertEqual(store.load(), s)
            h = WatchHistory(Path(directory)/"history.json")
            h.record("剧甲", 7)
            self.assertTrue(WatchHistory(h.path).contains("剧甲"))
            self.assertFalse(WatchHistory(h.path).contains("剧乙"))


class ScreenTests(unittest.TestCase):
    def fixture(self, name):
        return json.loads((CHECKS / (name+".json")).read_text(encoding="utf-8"))

    def test_real_feed_and_actual_player_are_distinguished(self):
        feed = read_screen(self.fixture("automation-feed"))
        player = read_screen(self.fixture("automation-player"))
        self.assertEqual((feed.kind, feed.episode), ("feed", 6))
        self.assertEqual((player.kind, player.episode), ("player", 6))
        self.assertEqual(feed.title, player.title)
        self.assertFalse(any(n["desc"] == "更多" for n in player.nodes))  # Off-screen detail panel.

    def test_modal_never_exposes_player_controls_behind_it(self):
        screen = read_screen(self.fixture("automation-episodes"))
        self.assertEqual(screen.kind, "episodes")
        self.assertIsNone(screen.ident("kea"))
        self.assertTrue(all(n["path"].startswith("0/") or n["path"] == "0" for n in screen.nodes))

    def test_feed_observation_never_clicks_or_records_history(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = Mock(settings=Settings())
            helper = PlaybackAssistant(manager, Mock(), WatchHistory(Path(directory)/"history.json"))
            helper.bridge = Mock()
            helper.bridge.snapshot.return_value = self.fixture("automation-feed")
            for now in [0, 1, 5, 10]:
                helper.tick(now)
            helper.bridge.click.assert_not_called()
            helper.bridge.back.assert_not_called()
            self.assertFalse(helper.history.items)

    def test_all_disabled_makes_no_player_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = Settings(start_rule="keep", preferred_quality="keep", auto_fullscreen=False)
            helper = PlaybackAssistant(Mock(settings=settings), Mock(), WatchHistory(Path(directory)/"history.json"))
            helper.bridge = Mock()
            helper.bridge.snapshot.return_value = self.fixture("automation-player")
            for now in [0, 2, 4, 6]: helper.tick(now)
            helper.bridge.click.assert_not_called()
            helper.bridge.back.assert_not_called()

    def test_wheel_transition_does_not_reopen_quality_or_restart_series(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = PlaybackAssistant(Mock(settings=Settings()), Mock(), WatchHistory(Path(directory)/"history.json"))
            helper.bridge = Mock()
            helper.bridge.snapshot.return_value = self.fixture("automation-player")
            helper.manual_episode_change(5)
            for now in [5.1, 5.5, 7.1, 8]:
                helper.tick(now)
            self.assertEqual(helper.stage, "")
            helper.bridge.click.assert_not_called()
            helper.bridge.dismiss.assert_not_called()
            self.assertTrue(helper.history.contains(helper.screen.title))

    def test_returning_to_feed_clears_wheel_transition_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = PlaybackAssistant(Mock(settings=Settings()), Mock(), WatchHistory(Path(directory)/"history.json"))
            helper.bridge = Mock()
            helper.manual_episode_change(5)
            helper.bridge.snapshot.return_value = self.fixture("automation-feed")
            helper.tick(5.1)
            self.assertEqual(helper.manual_navigation_until, 0)
            helper.bridge.snapshot.return_value = self.fixture("automation-player")
            helper.tick(5.2)
            helper.tick(7.3)
            self.assertTrue(helper.stage)
            helper.bridge.click.assert_called()

    def test_missing_1080_chooses_highest_available_lower_quality(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = PlaybackAssistant(Mock(settings=Settings()), Mock(), WatchHistory(Path(directory)/"history.json"))
            fixture = self.fixture("automation-more")
            fixture["nodes"] = [n for n in fixture["nodes"] if n["text"] != "1080P"]
            helper.bridge = Mock()
            helper.bridge.snapshot.return_value = fixture
            helper.title, helper.stage, helper.menu_owned = "测试剧", "quality", True
            helper.tick(5)
            self.assertEqual(helper.bridge.click.call_args.args[0]["text"], "720P")

    def test_unresponsive_control_stops_and_closes_only_owned_menu(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = PlaybackAssistant(Mock(settings=Settings()), Mock(), WatchHistory(Path(directory)/"history.json"))
            helper.bridge = Mock()
            helper.bridge.snapshot.return_value = self.fixture("automation-more")
            helper.bridge.click.return_value = False
            helper.title, helper.stage, helper.menu_owned, helper.deadline = "测试剧", "quality", True, 5
            helper.tick(4)
            helper.tick(6)
            self.assertEqual(helper.stage, "")
            helper.bridge.dismiss.assert_called_once()
            helper.bridge.back.assert_not_called()
            calls = helper.bridge.click.call_count
            helper.tick(8)
            self.assertEqual(helper.bridge.click.call_count, calls)

    def test_quality_auto_closes_no_extra_back_and_fullscreen_is_immediate(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = PlaybackAssistant(Mock(settings=Settings()), Mock(), WatchHistory(Path(directory)/"history.json"))
            helper.bridge = Mock()
            helper.bridge.snapshot.return_value = self.fixture("automation-player")
            helper.title, helper.stage, helper.menu_owned = "测试剧", "quality_wait", True
            helper.quality_ready_at = 8  # Player is already ready before this fallback time.
            helper.tick(5)
            helper.bridge.dismiss.assert_not_called()
            helper.bridge.back.assert_not_called()
            self.assertEqual(helper.bridge.click.call_args.args[0]["id"], "com.phoenix.read:id/eft")

    def test_portrait_clean_uses_same_quality_menu(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = PlaybackAssistant(Mock(settings=Settings()), Mock(), WatchHistory(Path(directory)/"history.json"))
            helper.bridge = Mock()
            helper.bridge.snapshot.return_value = self.fixture("automation-more")
            helper.title, helper.stage, helper.menu_owned = "测试剧", "quality_wait", True
            helper.quality_ready_at = 5.5
            helper.tick(5.1)
            helper.bridge.click.assert_not_called()
            helper.tick(5.6)
            self.assertEqual(helper.bridge.click.call_args.args[0]["text"], "清屏播放")
            helper.bridge.dismiss.assert_not_called()
            self.assertTrue(helper.fullscreen_done)

    def test_first_episode_confirmation_prepares_quality_without_extra_poll(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = PlaybackAssistant(Mock(settings=Settings()), Mock(), WatchHistory(Path(directory)/"history.json"))
            fixture = self.fixture("automation-player")
            for n in fixture["nodes"]:
                if n["id"].endswith("/k_4"): n["text"] = "第1集"
            helper.bridge = Mock()
            helper.bridge.snapshot.return_value = fixture
            helper.title, helper.stage, helper.deadline = "测试剧", "first", 10
            helper.tick(5)
            self.assertEqual(helper.episode, 1)
            self.assertEqual(helper.bridge.click.call_args.args[0]["id"], "com.phoenix.read:id/f3v")

    def test_loading_quality_menu_reopens_with_a_bounded_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            helper = PlaybackAssistant(Mock(settings=Settings()), Mock(), WatchHistory(Path(directory)/"history.json"))
            fixture = self.fixture("automation-more")
            fixture["nodes"] = [n for n in fixture["nodes"] if not n["text"].endswith("P")]
            self.assertEqual(read_screen(fixture).kind, "quality")
            helper.bridge = Mock()
            helper.bridge.snapshot.return_value = fixture
            helper.title, helper.stage, helper.menu_owned = "测试剧", "quality", True
            helper.quality_open_at, helper.quality_attempts = 5, 1
            helper.tick(5.3)
            helper.bridge.dismiss.assert_not_called()
            helper.tick(5.9)
            self.assertEqual(helper.stage, "prepare")
            helper.bridge.dismiss.assert_called_once()
            helper.stage, helper.menu_owned, helper.quality_attempts = "quality", True, 3
            helper.tick(7)
            self.assertEqual(helper.stage, "quality_wait")
            helper.bridge.back.assert_not_called()


if __name__ == "__main__":
    unittest.main()
