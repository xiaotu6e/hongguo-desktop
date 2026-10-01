from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+$")
HONGGUO = "com.phoenix.read"


def data_directory() -> Path:
    root = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "HongguoDesktopHelper"
    root.mkdir(parents=True, exist_ok=True)
    return root


@dataclass
class Settings:
    engine: str = "wsa"
    adb_port: int = 58526
    server_port: int = 5038
    startup_timeout: int = 90
    auto_open: bool = True
    open_after_install: bool = True
    window_mode: str = "auto"
    hide_titlebar: bool = True
    start_rule: str = "smart"
    protect_history: bool = True
    auto_fullscreen: bool = True
    preferred_quality: str = "1080"
    keep_wsa_direct: bool = True
    favorite: str = HONGGUO
    app_names: dict[str, str] = field(default_factory=lambda: {HONGGUO: "红果短剧"})

    @property
    def endpoint(self) -> str:
        return f"127.0.0.1:{self.adb_port}"

    def validate(self) -> None:
        if self.engine != "wsa":
            raise ValueError("此工具只管理 WSA，不使用 Android 模拟器。")
        if self.window_mode not in {"auto", "landscape", "portrait"}:
            raise ValueError("播放窗口模式无效。")
        if type(self.hide_titlebar) is not bool:
            raise ValueError("隐藏标题栏选项无效。")
        if self.start_rule not in {"smart", "keep"}:
            raise ValueError("起播规则无效。")
        if self.preferred_quality not in {"1080", "720", "keep"}:
            raise ValueError("清晰度选项无效。")
        if type(self.protect_history) is not bool or type(self.auto_fullscreen) is not bool:
            raise ValueError("播放辅助选项无效。")
        if type(self.keep_wsa_direct) is not bool:
            raise ValueError("安卓直连选项无效。")
        if type(self.adb_port) is not int or not 1024 <= self.adb_port <= 65535:
            raise ValueError("连接端口需要在 1024 到 65535 之间。")
        if type(self.server_port) is not int or not 1024 <= self.server_port <= 65535:
            raise ValueError("本机服务端口无效。")
        if self.adb_port == self.server_port:
            raise ValueError("连接端口与本机服务端口不能相同。")
        if type(self.startup_timeout) is not int or not 20 <= self.startup_timeout <= 300:
            raise ValueError("启动等待时间需要在 20 到 300 秒之间。")
        if not isinstance(self.favorite, str) or not PACKAGE_RE.fullmatch(self.favorite):
            raise ValueError("默认应用的包名无效。")
        if type(self.auto_open) is not bool or type(self.open_after_install) is not bool:
            raise ValueError("启动选项无效。")
        if not isinstance(self.app_names, dict) or not all(
            isinstance(k, str) and PACKAGE_RE.fullmatch(k) and isinstance(v, str)
            for k, v in self.app_names.items()
        ):
            raise ValueError("应用列表格式无效。")


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = path or data_directory() / "settings.json"
        self.load_warning = ""

    def load(self) -> Settings:
        if not self.path.exists():
            return Settings()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if raw.get("engine") == "emulator":
                raw["engine"] = "wsa"
                raw["auto_open"] = False
                self.load_warning = "已停用模拟器启动，恢复 WSA 管理。红果在 WSA 中的启动问题仍在排查。"
            settings = Settings(**{k: v for k, v in raw.items() if k in Settings.__dataclass_fields__})
            settings.validate()
            return settings
        except (OSError, ValueError, TypeError, AttributeError):
            self.load_warning = "原设置文件无法读取，已使用默认设置；保存设置后恢复正常。"
            return Settings()

    def save(self, settings: Settings) -> None:
        settings.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        payload = json.dumps(asdict(settings), ensure_ascii=False, indent=2)
        temporary.write_text(payload, encoding="utf-8")
        try:
            temporary.replace(self.path)
        except OSError as error:
            if getattr(error, "winerror", None) != 17:
                raise
            # Some encrypted/redirected Windows folders reject a same-folder
            # rename as cross-volume. Retain the staged copy and write in place.
            self.path.write_text(payload, encoding="utf-8")
