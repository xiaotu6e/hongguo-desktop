"""Handle native window input immediately, independently of presentation mode."""
import time
from .android import OperationError


class WindowInput:
    def __init__(self, playback, chrome):
        self.playback, self.chrome = playback, chrome

    def refresh_targets(self):
        """Limit click interception to the current, enabled Fullscreen button."""
        screen = self.playback.screen
        nodes = getattr(screen, "nodes", [])
        rect = None
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
                        rect = (round((x1-left)*65535/width), round((y1-top)*65535/height),
                                round((x2-left)*65535/width), round((y2-top)*65535/height))
                        break
        self.chrome.set_fullscreen_target(rect)
        return rect

    def clear_targets(self):
        self.chrome.set_fullscreen_target(None)

    def dispatch(self):
        if self.chrome.take_fullscreen():
            self.clear_targets()
            result = self.playback.request_landscape()
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
        return None
