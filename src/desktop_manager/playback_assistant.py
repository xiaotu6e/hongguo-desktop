"""Conservative, entry-triggered automation of Hongguo's existing UI controls."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import time

from .config import Settings, data_directory
from .ui_bridge import UiBridge


def short_id(node):
    return node.get("id", "").rsplit("/", 1)[-1]


@dataclass
class Screen:
    kind: str
    title: str
    episode: int
    nodes: list[dict]
    landscape: bool = False

    def ident(self, name):
        return next((n for n in self.nodes if short_id(n) == name), None)

    def text(self, text):
        return next((n for n in self.nodes if n.get("text") == text), None)


def read_screen(snapshot: dict) -> Screen:
    # WSA sometimes exposes off-screen pages and the activity behind a dialog.
    # Only use the top root, with each node's center inside that root.
    all_nodes = snapshot.get("nodes", [])
    if not all_nodes:
        return Screen("other", "", 0, [])
    root = all_nodes[0]
    l, t, r, b = root["bounds"]
    nodes = []
    for n in all_nodes:
        x1, y1, x2, y2 = n["bounds"]
        if n["path"].split("/")[0] == root["path"] and x2 > x1 and y2 > y1 and l <= (x1+x2)/2 < r and t <= (y1+y2)/2 < b:
            nodes.append(n)
    screen = Screen("other", "", 0, nodes)
    if screen.text("清晰度") and screen.text("倍速") and screen.ident("action_text"):
        screen.kind = "quality"
    elif screen.ident("jk6") and screen.ident("i42"):
        screen.kind = "episodes"
        screen.title = screen.ident("i42")["text"]
    elif screen.ident("arc") and screen.ident("gbx"):
        screen.kind = "feed"
        if screen.ident("d4"):
            screen.title = screen.ident("d4")["text"].strip()
        if screen.ident("h1h"):
            m = re.match(r"第\s*(\d+)\s*集", screen.ident("h1h")["text"])
            screen.episode = int(m[1]) if m else 0
    elif screen.ident("kea") and (screen.ident("k_4") or screen.ident("eft")):
        screen.kind = "player"
        if screen.ident("d4"):
            screen.title = screen.ident("d4")["text"].strip()
        if screen.ident("k_4"):
            m = re.search(r"第\s*(\d+)\s*集", screen.ident("k_4")["text"])
            screen.episode = int(m[1]) if m else 0
    elif screen.ident("f8h") and screen.ident("h8p"):
        screen.kind, screen.landscape = "player", True
    return screen


class WatchHistory:
    """Only episodes observed in the actual player, never recommendation previews."""
    def __init__(self, path: Path | None = None):
        self.path = path or data_directory() / "watch_history.json"
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            self.items = {key: item for key, item in value.items()
                          if isinstance(key, str) and isinstance(item, dict)
                          and type(item.get("episode")) is int and type(item.get("seen_at")) is int} if isinstance(value, dict) else {}
        except (OSError, ValueError):
            self.items = {}

    def contains(self, title):
        return title in self.items

    def record(self, title, episode):
        if not title or episode < 1:
            return
        if self.items.get(title, {}).get("episode") == episode:
            return
        self.items[title] = {"episode": episode, "seen_at": int(time.time())}
        self.items = dict(sorted(self.items.items(), key=lambda pair: pair[1].get("seen_at", 0))[-2000:])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        payload = json.dumps(self.items, ensure_ascii=False, indent=2)
        temporary.write_text(payload, encoding="utf-8")
        try:
            temporary.replace(self.path)
        except OSError as error:
            if getattr(error, "winerror", None) != 17:
                raise
            self.path.write_text(payload, encoding="utf-8")


@dataclass
class Entry:
    title: str
    episode: int
    jump: bool
    reason: str
    from_feed: bool = False


class EntryPolicy:
    def __init__(self):
        self.preview = None
        self.active = ""
        self.pending = None
        self.decided = False

    def observe(self, screen: Screen, now: float, settings: Settings, watched: bool) -> Entry | None:
        if screen.kind == "feed":
            # Redguo replaces the episode caption with a hot comment after a
            # few seconds. Keep the earlier number for the same visible title.
            episode = screen.episode
            if not episode and self.preview and self.preview[0] == screen.title:
                episode = self.preview[1]
            self.preview = (screen.title, episode, now) if screen.title else None
            self.active, self.pending, self.decided = "", None, False
            return None
        if screen.kind != "player" or not screen.title or not screen.episode:
            return None
        if screen.title != self.active:
            self.active, self.decided = screen.title, False
            preview = self.preview
            self.preview = None  # This feed transition can only be consumed once.
            matches = bool(preview and now-preview[2] < 8 and preview[:2] == (screen.title, screen.episode))
            from_feed = bool(preview and now-preview[2] < 8 and preview[0] == screen.title)
            self.pending = [screen.episode, now, matches, from_feed]
        if self.decided:
            return None
        if screen.episode != self.pending[0]:
            self.pending = [screen.episode, now, False, self.pending[3]]  # App resumed or user changed the episode.
        # Only a possible rewind needs the full resume-protection interval.
        # A known series, different episode, or "keep" can never jump back.
        could_jump = (settings.start_rule == "smart" and self.pending[2] and screen.episode > 1
                      and not (settings.protect_history and watched))
        if now-self.pending[1] < (1.8 if could_jump else .35):
            return None
        self.decided = True
        if settings.start_rule == "keep":
            reason = "按设置保留当前集数"
        elif settings.protect_history and watched:
            reason = "本机已记录观看，保留当前集数"
        elif not self.pending[2]:
            reason = "保留红果当前进度"
        elif screen.episode == 1:
            reason = "当前已经是第 1 集"
        else:
            return Entry(screen.title, screen.episode, True, "推荐集数与进入集数一致", self.pending[3])
        return Entry(screen.title, screen.episode, False, reason, self.pending[3])


class PlaybackAssistant:
    def __init__(self, manager, emit, history=None, prepare_landscape=None):
        self.manager, self.emit = manager, emit
        self.bridge = UiBridge(manager)
        self.history = history or WatchHistory()
        self.policy = EntryPolicy()
        self.screen = Screen("other", "", 0, [])
        self.snapshot_data = {}
        self.title = ""
        self.episode = 0
        self.stage = ""
        self.deadline = 0.0
        self.wait_until = 0.0
        self.fullscreen_done = False
        # Retain choices across feed returns, minimization and bridge recovery.
        # A series may receive automatic preparation only once in this session.
        self.entered_titles: set[str] = set()
        self.quality_done = False
        self.quality_target = ""
        self.menu_owned = False
        self.cancel_menu = False
        self.quality_ready_at = 0.0
        self.landscape_content = False
        self.quality_attempts = 0
        self.quality_open_at = 0.0
        self.fast_until = 0.0
        self.manual_navigation_until = 0.0
        self.prepare_landscape = prepare_landscape
        self.landscape_preparing = False
        self.landscape_ready_at = None
        self.landscape_clicked = False
        self.landscape_origin = None
        self.landscape_manual = False
        self.landscape_mode = None
        self.landscape_cached = False
        self.last_landscape = None
        self.last_settings = self.preferences()

    @property
    def poll_interval(self):
        if self.stage or (self.policy.active and not self.policy.decided) or time.monotonic() < self.fast_until:
            return .16
        return .30 if self.screen.kind == "feed" else .8

    def preferences(self):
        s = self.manager.settings
        return s.start_rule, s.protect_history, s.auto_fullscreen, s.preferred_quality

    def close(self):
        self.bridge.close()

    def reset(self):
        if self.landscape_preparing:
            self._finish_landscape(False, "reset")
        self.close()
        self.policy = EntryPolicy()
        self.title, self.stage = "", ""
        self.menu_owned = False
        self.manual_navigation_until = 0.0
        self._clear_landscape()

    def manual_episode_change(self, now):
        # A swipe briefly exposes the ordinary player underneath the clean
        # page. It is an explicit episode change, not a new series entry.
        self.manual_navigation_until = now+2
        self.policy.preview = None
        self.fast_until = now+1.2
        self.consume_fullscreen()
        if self.landscape_preparing:
            self._finish_landscape(False, "manual_navigation")

    def consume_fullscreen(self, title=""):
        self.fullscreen_done = True
        title = title or self.title or self.policy.active
        if title:
            self.entered_titles.add(title)

    def _clear_landscape(self):
        self.landscape_preparing = False
        self.landscape_ready_at = None
        self.landscape_clicked = False
        self.landscape_origin = None
        self.landscape_manual = False
        self.landscape_mode = None
        self.landscape_cached = False

    def cancel_landscape(self):
        if self.landscape_preparing:
            self._finish_landscape(False, "cancel_requested")

    def _record_landscape(self, reason, **values):
        screen = self.screen
        origin = self.landscape_origin
        self.last_landscape = {
            **(self.last_landscape or {}),
            "reason": reason,
            "origin": {"kind": origin[0], "title": origin[1], "episode": origin[2]} if origin else None,
            "current": {"kind": screen.kind, "title": screen.title, "episode": screen.episode},
            "root_bounds": screen.nodes[0]["bounds"] if screen.nodes else None,
            "clicked": self.landscape_clicked,
            **values,
        }

    def _finish_landscape(self, success, reason=None):
        origin, manual = self.landscape_origin, self.landscape_manual
        self._record_landscape(reason or ("landscape_entered" if success else "cancelled"), ok=bool(success))
        self._clear_landscape()
        self.stage = ""
        # Both a successful transition and a cancelled one consume this attempt.
        # Returning from fullscreen must not immediately trigger another click.
        self.fullscreen_done = True
        if success:
            if manual and origin and origin[1]:
                self.title = self.policy.active = origin[1]
                self.policy.pending, self.policy.preview, self.policy.decided = None, None, True
                if origin[0] == "player" and origin[2]:
                    self.episode = origin[2]
                    self.history.record(self.title, self.episode)
            self.emit("已进入横屏全屏播放，窗口将自动适配。")

    def _begin_landscape(self, screen, now, *, manual=False):
        node = screen.text("全屏观看")
        if not node or not node.get("enabled", True) or screen.landscape:
            self._record_landscape("fullscreen_unavailable")
            return False
        self.landscape_origin = (screen.kind, screen.title, screen.episode)
        # Manual clicks remain available, but they consume any automatic attempt.
        self.consume_fullscreen(screen.title)
        self.landscape_manual = manual
        self.landscape_mode = self.manager.settings.window_mode
        self.landscape_ready_at = None
        self.landscape_clicked = False
        self.landscape_preparing = True
        self.stage, self.deadline = "landscape_prepare", now+4
        self.fast_until = time.monotonic()+1.2
        self.last_landscape = {"manual": manual, "window_mode": self.landscape_mode, "callback": None}
        self._record_landscape("preparing")
        # True requests a resize, None preserves fixed portrait/maximized or
        # legacy callers, and False rejects a missing/minimized window.
        try:
            prepared = self.prepare_landscape(screen) if self.prepare_landscape else None
        except Exception:
            self._finish_landscape(False, "prepare_error")
            raise
        self._record_landscape("prepared", callback=prepared)
        self.landscape_cached = prepared == "cached"
        if prepared is False:
            self._finish_landscape(False, "prepare_rejected")
            return False
        if prepared is None:
            self.landscape_clicked = True
            try:
                clicked = self.click(node, now)
            except Exception:
                self._finish_landscape(False, "click_error")
                raise
            self._finish_landscape(clicked, "unresized_click_sent" if clicked else "click_rejected")
            return clicked
        return True

    def request_landscape(self, now=None):
        """Consume one explicit Fullscreen click, including the recommendation feed."""
        now = time.monotonic() if now is None else now
        if self.landscape_preparing:
            return {"ok": False, "method": "landscape_prepare", "pending": True}
        previous = self.screen
        self.snapshot_data = self.bridge.snapshot()
        screen = self.screen = read_screen(self.snapshot_data)
        if screen.kind not in {"feed", "player"} or screen.landscape:
            self._record_landscape("request_not_available")
            return {"ok": False, "method": "landscape_prepare"}
        if previous.kind in {"feed", "player"}:
            changed = (previous.kind != screen.kind
                       or bool(previous.title and screen.title and previous.title != screen.title)
                       or bool(previous.episode and screen.episode and previous.episode != screen.episode))
            if changed:
                self._record_landscape("request_page_changed", origin={
                    "kind": previous.kind, "title": previous.title, "episode": previous.episode})
                return {"ok": False, "method": "landscape_prepare"}
        accepted = self._begin_landscape(screen, now, manual=True)
        return {"ok": accepted, "method": "landscape_prepare"}

    def _advance_landscape(self, screen, now):
        origin = self.landscape_origin
        settings = self.manager.settings
        if now > self.deadline:
            self._finish_landscape(False, "entry_timeout" if self.landscape_clicked else "prepare_timeout")
            return
        if settings.window_mode != self.landscape_mode:
            self._finish_landscape(False, "window_mode_changed")
            return
        if not self.landscape_manual and not settings.auto_fullscreen:
            self._finish_landscape(False, "automatic_disabled")
            return
        if screen.landscape:
            # LandActivity is exposed before WSA applies its new display
            # orientation. Keep the preparation until its root is truly wide.
            if screen.nodes:
                left, top, right, bottom = screen.nodes[0]["bounds"]
                if right-left > bottom-top:
                    self._finish_landscape(True)
            return
        if screen.kind in {"quality", "episodes"}:
            self._finish_landscape(False, "menu_opened")
            return
        if screen.kind in {"feed", "player"} and origin:
            changed = (screen.kind != origin[0]
                       or bool(screen.title and origin[1] and screen.title != origin[1])
                       or bool(screen.episode and origin[2] and screen.episode != origin[2]))
            if changed:
                self._finish_landscape(False, "page_changed")
                return
        if self.landscape_clicked or screen.kind not in {"feed", "player"}:
            return
        node = screen.text("全屏观看")
        if not node or not node.get("enabled", True) or not screen.nodes:
            return
        left, top, right, bottom = screen.nodes[0]["bounds"]
        if not self.landscape_cached and right-left <= bottom-top:
            self.landscape_ready_at = None
            return
        if not self.landscape_cached and self.landscape_ready_at is None:
            # A resized root can appear before Application resources finish
            # updating. Require another fresh snapshot after a short settle.
            self.landscape_ready_at = now+.30
            self._record_landscape("geometry_settling")
            return
        if not self.landscape_cached and now < self.landscape_ready_at:
            return
        # The binding below comes from this tick's new snapshot. Never click
        # the original portrait node, whose bounds changed during the resize.
        self.landscape_clicked = self.fullscreen_done = True
        self.deadline = now+2
        self._record_landscape("fullscreen_click_sending")
        try:
            clicked = self.click(node, now)
        except Exception:
            self._finish_landscape(False, "click_error")
            raise
        if not clicked:
            self._finish_landscape(False, "click_rejected")
        else:
            self._record_landscape("fullscreen_click_sent")

    def settings_changed(self):
        # Never re-run a first-episode jump in the middle of an existing series.
        previous, current = self.last_settings, self.preferences()
        self.last_settings = current
        if previous[2:] != current[2:]:
            if self.landscape_preparing:
                self._finish_landscape(False, "preferences_changed")
            self.cancel_menu = self.menu_owned
            self.quality_done = False
            self.quality_attempts = 0
            # Changing quality or opting in must not reopen the current player.
            # Automatic fullscreen applies only to the next new series entry.
            self.consume_fullscreen()
            if self.title and self.stage not in {"jump", "first"}:
                self.stage = "prepare"
                self.deadline = time.monotonic()+15

    def click(self, node, now, *, tap=False):
        if node and self.bridge.click(node, tap=tap):
            # Page recognition gates the next step; no fixed one-second pause.
            self.wait_until = now + .12
            self.fast_until = time.monotonic()+1.2
            return True
        return False

    def dismiss_quality(self, screen, now):
        marker = screen.text("清晰度") if screen.kind == "quality" else None
        if marker and self.bridge.dismiss(marker):
            self.wait_until = now+.12
            self.fast_until = time.monotonic()+1.2
            return True
        return False

    def tick(self, now=None):
        now = time.monotonic() if now is None else now
        settings = self.manager.settings
        self.settings_changed()
        previous = self.screen
        self.snapshot_data = self.bridge.snapshot()
        screen = self.screen = read_screen(self.snapshot_data)
        if previous.landscape and not screen.landscape:
            self.consume_fullscreen()
        if screen.kind in {"episodes", "quality"} and not self.menu_owned:
            self.consume_fullscreen()
        if screen.kind == "player" and (screen.landscape or not screen.ident("d4")):
            self.consume_fullscreen()
        if self.landscape_preparing:
            self._advance_landscape(screen, now)
            return
        if self.cancel_menu:
            if self.menu_owned and screen.kind == "quality":
                self.dismiss_quality(screen, now)
            self.cancel_menu = self.menu_owned = False
            return
        if screen.kind == "feed":
            self.manual_navigation_until = 0.0
            self.policy.observe(screen, now, settings, False)
            self.title, self.stage, self.menu_owned = "", "", False
            return  # No clicks in the recommendation feed, ever.
        if screen.kind == "player":
            if now < self.manual_navigation_until:
                if screen.title:
                    self.title = self.policy.active = screen.title
                    self.policy.decided, self.policy.pending = True, None
                if self.title and screen.episode:
                    self.episode = screen.episode
                    self.history.record(self.title, self.episode)
                return
            entry = self.policy.observe(screen, now, settings, self.history.contains(screen.title))
            if entry:
                returning = entry.title in self.entered_titles
                self.entered_titles.add(entry.title)
                self.title, self.episode = entry.title, entry.episode
                self.stage = "jump" if entry.jump else "prepare"
                self.deadline = now + 15
                self.landscape_content = bool(screen.text("全屏观看")) or screen.landscape
                self.quality_done = False
                self.fullscreen_done = returning or not entry.from_feed
                self.menu_owned = False
                self.quality_attempts = 0
                self.emit(f"《{entry.title}》：{entry.reason}")
                if not entry.jump:
                    self.history.record(self.title, self.episode)
            elif self.title and screen.episode and screen.episode != self.episode and self.stage not in {"first", "jump"}:
                self.episode = screen.episode
                self.history.record(self.title, self.episode)
                # The app retains its selected quality across episodes; avoid
                # reopening a menu on every cut. A new series is prepared again.
        if not self.title or now < self.wait_until:
            return
        if self.stage in {"prepare", "quality", "quality_wait", "fullscreen"} and self.deadline and now > self.deadline:
            if self.menu_owned and screen.kind == "quality":
                self.dismiss_quality(screen, now)
            self.stage, self.menu_owned = "", False
            self.emit("本次播放偏好未能全部应用，已停止重试；下次进入剧集时再试。")
            return
        if self.stage in {"jump", "first"}:
            if now > self.deadline:
                self.emit("未能确认切到第 1 集，已停止重试，保留当前播放。")
                self.stage, self.menu_owned = "prepare", False
                return
            if screen.kind == "player" and screen.episode == 1:
                self.episode = 1
                self.history.record(self.title, 1)
                self.emit(f"《{self.title}》已从第 1 集开始")
                self.stage = "prepare"
            elif self.stage == "jump" and screen.kind == "player":
                if screen.title != self.title or screen.episode != self.episode:
                    self.stage = "prepare"  # User/app changed it after the decision.
                    return
                node = screen.text("从第1集开始看")
                if self.click(node, now):
                    self.stage = "first"
                elif self.click(screen.ident("kea"), now):
                    self.stage, self.menu_owned = "first", True
            elif self.stage == "first" and self.menu_owned and screen.kind == "episodes" and screen.title == self.title:
                one = next((n for n in screen.nodes if short_id(n) == "jk6" and n["text"] == "1"), None)
                group = next((n for n in screen.nodes if short_id(n) == "h1p" and re.match(r"1\s*[-—]\s*\d+", n["text"])), None)
                self.click(one or group, now)
            if self.stage != "prepare":
                return
        if self.stage == "prepare" and screen.kind == "player":
            self.landscape_content = bool(screen.text("全屏观看")) or screen.landscape
            if not self.quality_done and settings.preferred_quality != "keep":
                if self.click(screen.ident("f3v"), now):
                    self.quality_attempts += 1
                    self.quality_open_at = now
                    self.stage, self.menu_owned, self.deadline = "quality", True, now+8
                    return
                if screen.landscape:
                    # Do not exit fullscreen or pause playback to expose controls.
                    self.quality_done = True
                    self.emit("已在横屏播放；清晰度偏好将在下次进入剧集时应用。")
            else:
                self.quality_done = True
            if self.quality_done:
                self.stage = "fullscreen"
        if self.stage == "quality" and self.menu_owned:
            if screen.kind == "quality":
                choices = [(int(n["text"][:-1]), n) for n in screen.nodes if re.fullmatch(r"\d+P", n["text"]) and n.get("enabled")]
                wanted = int(settings.preferred_quality) if settings.preferred_quality != "keep" else 0
                available = [item for item in choices if item[0] <= wanted]
                if available:
                    value, node = max(available, key=lambda pair: pair[0])
                    if self.click(node, now, tap=True):
                        self.quality_target = str(value)
                        self.stage, self.quality_ready_at = "quality_wait", now+.55
                        self.emit(f"已选择 {value}P 清晰度" + ("（本集未提供偏好的档位）" if value != wanted else ""))
                else:
                    if not choices and now-self.quality_open_at < .8:
                        return
                    # A very early menu can cache an empty quality list even
                    # after the video loads. Reopen at most twice once ready.
                    if not choices and self.quality_attempts < 3:
                        if self.dismiss_quality(screen, now):
                            self.stage, self.menu_owned = "prepare", False
                            self.wait_until = now+.35
                        return
                    self.emit("本集未找到可用的清晰度选项，保留红果设置。")
                    self.stage, self.quality_ready_at = "quality_wait", now
            elif now > self.deadline:
                self.stage, self.menu_owned, self.quality_done = "fullscreen", False, True
            return
        if self.stage == "quality_wait":
            if screen.kind == "quality" and self.menu_owned:
                if now < self.quality_ready_at:
                    return
                # The same menu already offers portrait clean-screen playback.
                # Use it directly instead of closing and reopening controls.
                clean = screen.text("清屏播放")
                if settings.auto_fullscreen and not self.fullscreen_done and not self.landscape_content and clean:
                    if self.click(clean, now):
                        self.quality_done = self.fullscreen_done = True
                        self.menu_owned, self.stage = False, ""
                        self.emit("已进入竖屏清屏播放。")
                    return
                if not self.dismiss_quality(screen, now):
                    return
                self.quality_done, self.menu_owned, self.stage = True, False, "fullscreen"
                return
            if screen.kind != "player":
                return
            # A changed quality can close the menu itself. Continue on this
            # observed player frame, never send an extra Back to the player.
            self.quality_done, self.menu_owned, self.stage = True, False, "fullscreen"
        if self.stage == "fullscreen" and screen.kind == "player":
            if not settings.auto_fullscreen or self.fullscreen_done or screen.landscape:
                self.stage, self.fullscreen_done = "", True
                return
            node = screen.text("全屏观看")
            if node:
                self._begin_landscape(screen, now)
            elif screen.ident("d4") and screen.ident("eft"):
                if self.click(screen.ident("eft"), now):
                    self.fullscreen_done, self.stage = True, ""
                    self.emit("已进入竖屏清屏播放。")
            elif not screen.ident("d4"):
                self.fullscreen_done, self.stage = True, ""  # Already clean.
