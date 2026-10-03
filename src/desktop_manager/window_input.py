"""Handle native window input immediately, independently of presentation mode."""
import time
from .android import OperationError


class WindowInput:
    def __init__(self, playback, chrome, native_player_ready=None):
        self.playback, self.chrome = playback, chrome
        self.fullscreen_presentation = None
        self.native_player_ready = native_player_ready
        self.fullscreen_passthrough = False

    def refresh_targets(self):
        """Limit click interception to the current, enabled Fullscreen button."""
        screen = self.playback.screen
        nodes = getattr(screen, "nodes", [])
        rect = None
        mask = None
        self.fullscreen_presentation = None
        preparing = (getattr(self.playback, "landscape_preparing", False) is True or
                     getattr(self.playback, "stage", "") == "landscape_prepare")
        if screen.kind in {"feed", "player"} and not screen.landscape and not preparing and nodes:
            root = nodes[0]
            bounds = root.get("bounds", [])
            if len(bounds) == 4:
                left, top, right, bottom = bounds
                width, height = right-left, bottom-top
                if width > 0 and height > 0:
                    for label in nodes:
                        if label.get("text") != "全屏观看" or not label.get("enabled", True):
                            continue
                        path = label.get("path", "")
                        ancestors = [node for node in nodes if node.get("clickable") and node.get("enabled", True)
                                     and (node.get("path") == path or path.startswith(node.get("path", "")+"/"))]
                        if not ancestors:
                            continue
                        button = max(ancestors, key=lambda node: len(node["path"].split("/")))
                        button_bounds = button.get("bounds", [])
                        if len(button_bounds) != 4:
                            continue
                        x1, y1, x2, y2 = button_bounds
                        x1, y1, x2, y2 = max(x1, left), max(y1, top), min(x2, right), min(y2, bottom)
                        # A malformed ancestry must never intercept the video,
                        # drag area, or an entire screen as a Fullscreen button.
                        if x2 <= x1 or y2 <= y1 or (x2-x1)*(y2-y1) > width*height*.2:
                            continue
                        original = (x1, y1, x2, y2)
                        if screen.kind == "feed":
                            for tag in nodes:
                                if "在追" not in tag.get("text", "") or not tag.get("enabled", True):
                                    continue
                                tag_path = tag.get("path", "")
                                parents = [n for n in nodes if n.get("clickable") and n.get("enabled", True)
                                           and (n.get("path") == tag_path or tag_path.startswith(n.get("path", "")+"/"))]
                                if not parents:
                                    continue
                                tag_button = max(parents, key=lambda n: len(n["path"].split("/")))
                                tag_bounds = tag_button.get("bounds", ())
                                if len(tag_bounds) != 4:
                                    continue
                                tx1, ty1, tx2, ty2 = tag_bounds
                                if tx2 <= tx1 or ty2 <= ty1 or (tx2-tx1)*(ty2-ty1) > width*height*.2:
                                    continue
                                if not (max(x1, tx1) < min(x2, tx2) and max(y1, ty1) < min(y2, ty2)):
                                    continue
                                # Keep the app's series tag and its hit area intact.
                                # Present a compact Fullscreen button just above it,
                                # masking only the visible part of the old button.
                                gap = max(3, round(height*8/1230))
                                button_height = max(18, round((y2-y1)*.85))
                                new_bottom = ty1-gap
                                new_top = new_bottom-button_height
                                if new_top < top or y1-new_top > y2-y1:
                                    continue
                                old_visible = (x1, y1, x2, min(y2, ty1))
                                x1, y1, x2, y2 = (x1, new_top, x2, new_bottom)
                                mask_bounds = old_visible if old_visible[3] > old_visible[1] else (x1,y1,x2,y2)
                                mask = tuple(round((value-origin)*65535/size) for value,origin,size in
                                             zip(mask_bounds,(left,top,left,top),(width,height,width,height)))
                                self.fullscreen_presentation = {"button_bounds": [x1,y1,x2,y2],
                                    "original_bounds": list(original), "tag_bounds": [tx1,ty1,tx2,ty2],
                                    "gap": gap}
                                break
                        rect = (round((x1-left)*65535/width), round((y1-top)*65535/height),
                                round((x2-left)*65535/width), round((y2-top)*65535/height))
                        break
        self.chrome.set_fullscreen_presentation(mask)
        self.fullscreen_passthrough = bool(rect and screen.kind == "player" and self.native_player_ready
                                          and self.native_player_ready() is True)
        if self.fullscreen_passthrough:
            self.chrome.set_fullscreen_target(rect, passthrough=True)
        else:
            self.chrome.set_fullscreen_target(rect)
        return rect

    def clear_targets(self):
        self.fullscreen_presentation = None
        self.fullscreen_passthrough = False
        self.chrome.set_fullscreen_target(None)
        self.chrome.set_fullscreen_presentation(None)

    def dispatch(self):
        click = self.chrome.take_fullscreen()
        if click:
            self.clear_targets()
            result = (self.playback.observe_fullscreen_click() if click == "observed"
                      else self.playback.request_landscape())
            if not isinstance(result, dict) or type(result.get("ok")) is not bool:
                raise OperationError("未能确认横屏窗口准备结果。")
            return result
        packet = self.chrome.take_wheel()
        if packet:
            if self.playback.screen.kind == "player" or getattr(self.playback, "landscape_preparing", False) is True:
                self.playback.manual_episode_change(time.monotonic())
            result = self.playback.bridge.wheel(*packet)
            self.playback.fast_until = time.monotonic()+.8
            return result
        if self.chrome.take_page_change() is True:
            return {"ok": True, "method": "page_refresh"}
        return None
