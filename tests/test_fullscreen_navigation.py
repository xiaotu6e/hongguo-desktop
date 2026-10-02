"""Exercise complete user navigation, including returns through recommendation pages."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from desktop_manager.config import Settings
from desktop_manager.playback_assistant import PlaybackAssistant, WatchHistory, read_screen


class FullscreenNavigationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = Settings(start_rule="keep", preferred_quality="keep", auto_fullscreen=True)
        self.helper = PlaybackAssistant(Mock(settings=self.settings), Mock(),
            WatchHistory(Path(self.directory.name) / "history.json"))
        self.helper.bridge = Mock()
        self.helper.bridge.click.return_value = True

    def fixture(self, kind, title="剧甲"):
        name = {"feed": "automation-feed", "player": "automation-player",
                "quality": "automation-more", "episodes": "automation-episodes"}[kind]
        data = json.loads((Path(__file__).parent / "fixtures" / (name + ".json")).read_text(encoding="utf-8"))
        for node in data["nodes"]:
            if node.get("id", "").rsplit("/", 1)[-1] in {"d4", "i42"}:
                node["text"] = title
        return data

    def tick(self, kind, now, title="剧甲"):
        self.helper.bridge.snapshot.return_value = self.fixture(kind, title)
        self.helper.tick(now)

    def enter(self, now=0, title="剧甲"):
        self.tick("feed", now, title)
        self.tick("player", now + 1, title)
        self.tick("player", now + 2, title)

    def fullscreen_clicks(self):
        return [call.args[0] for call in self.helper.bridge.click.call_args_list
                if call.args[0].get("id", "").endswith("/eft")
                or call.args[0].get("text") in {"全屏观看", "清屏播放"}]

    def test_default_uses_manual_fullscreen(self):
        self.assertFalse(Settings().auto_fullscreen)

    def test_first_confirmed_entry_can_enter_fullscreen_once(self):
        self.enter()
        self.assertEqual(len(self.fullscreen_clicks()), 1)

    def test_return_via_recommendation_does_not_enter_fullscreen_again(self):
        self.enter()
        self.enter(5)
        self.assertEqual(len(self.fullscreen_clicks()), 1)

    def test_return_from_episode_selection_does_not_enter_fullscreen_again(self):
        self.enter()
        self.tick("episodes", 4)
        self.enter(5)
        self.assertEqual(len(self.fullscreen_clicks()), 1)

    def test_opening_episode_selection_cancels_pending_automatic_entry(self):
        self.tick("feed", 0)
        self.tick("player", 1)
        self.tick("episodes", 1.1)
        self.tick("player", 2)
        self.tick("player", 3)
        self.assertEqual(self.fullscreen_clicks(), [])

    def test_reconnection_retains_consumed_fullscreen_choice(self):
        self.enter()
        self.helper.reset()
        self.enter(5)
        self.assertEqual(len(self.fullscreen_clicks()), 1)

    def test_a_different_series_can_auto_enter_but_revisiting_the_first_cannot(self):
        self.enter(title="剧甲")
        self.enter(5, "剧乙")
        self.assertEqual(len(self.fullscreen_clicks()), 2)
        self.enter(10, "剧甲")
        self.assertEqual(len(self.fullscreen_clicks()), 2)

    def test_attaching_to_an_existing_player_does_not_auto_enter(self):
        self.tick("player", 0)
        self.tick("player", 2)
        self.assertEqual(self.fullscreen_clicks(), [])

    def test_enabling_automatic_preference_applies_to_next_series(self):
        self.settings.auto_fullscreen = False
        self.enter()
        self.settings.auto_fullscreen = True
        self.tick("player", 4)
        self.tick("player", 5)
        self.assertEqual(self.fullscreen_clicks(), [])
        self.enter(10, "剧乙")
        self.assertEqual(len(self.fullscreen_clicks()), 1)

    def test_quality_change_does_not_reenter_from_player_or_quality_menu(self):
        self.enter()
        self.settings.preferred_quality = "1080"
        self.tick("player", 4)
        self.tick("quality", 5)
        self.tick("quality", 6)
        self.tick("player", 7)
        self.tick("player", 8)
        self.assertEqual(len(self.fullscreen_clicks()), 1)
        self.assertTrue(any(c.args[0].get("text") == "1080P"
                            for c in self.helper.bridge.click.call_args_list))

    def test_explicit_fullscreen_click_remains_available_after_exit(self):
        self.settings.auto_fullscreen = False
        data = self.fixture("player")
        root = data["nodes"][0]
        left, top, right, bottom = root["bounds"]
        data["nodes"].append({"path": "0/fullscreen", "id": "", "text": "全屏观看",
            "desc": "", "enabled": True, "bounds": [left, top, right, top + 30]})
        for now in (1, 5):
            self.helper.screen = read_screen(data)
            self.helper.bridge.snapshot.return_value = data
            self.assertTrue(self.helper.request_landscape(now)["ok"])
        self.assertEqual(len(self.fullscreen_clicks()), 2)


if __name__ == "__main__":
    unittest.main()
