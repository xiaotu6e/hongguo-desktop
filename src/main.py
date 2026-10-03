from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import queue
import re
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import flet as ft
import flet_dropzone as ftd

from desktop_manager.android import AndroidManager, OperationError, SCREEN_TIMEOUTS, open_wsa_settings
from desktop_manager.config import HONGGUO, SettingsStore, data_directory
from desktop_manager.playback_window import WsaPlayerWindow, PlayerLayout, player_layout
from desktop_manager.playback_assistant import PlaybackAssistant, short_id
from desktop_manager.window_chrome import WindowChrome
from desktop_manager.window_input import WindowInput
from desktop_manager.runtime_diagnostics import RuntimeDiagnostics, process_state
from desktop_manager.single_instance import SingleInstance
from desktop_manager.guardian import GuardianLink

BG = "#11151C"
PANEL = "#1B222C"
MUTED = "#9BA9BA"
ACCENT = "#FF886D"
LINE = "#303A48"
GREEN = "#82D9B6"


def visible_app_pid(output: str, package: str = HONGGUO) -> str | None:
    """Use the drawn app window's session, excluding same-name workers."""
    for section in re.split(r"(?m)^  Window #", output):
        header = section.partition("\n")[0]
        if not re.search(r"\bu\d+\s+" + re.escape(package) + r"/[^\s}]+", header):
            continue
        if not re.search(r"\bisVisible=true\b", section) or "mDrawState=HAS_DRAWN" not in section:
            continue
        session = re.search(r"\bmSession=Session\{[^\s{}]+\s+(\d+):", section)
        if session:
            return session[1]
    return None


class DesktopApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.store = SettingsStore()
        self.settings = self.store.load()
        self.events: queue.Queue[str] = queue.Queue()
        self.manager = AndroidManager(self.settings, self.events.put)
        self.playback = PlaybackAssistant(self.manager, self.events.put,
                                          prepare_landscape=self.prepare_landscape_window,
                                          activate_fullscreen=self.activate_fullscreen_control)
        atexit.register(self.playback.close)
        self.player_window = WsaPlayerWindow()
        self.window_chrome = WindowChrome(self.player_window)
        atexit.register(self.window_chrome.close)
        self.window_input = WindowInput(self.playback, self.window_chrome, self.native_player_fullscreen_ready)
        self.chrome_retry_after = 0.0
        self.last_splash_check = 0.0
        self.last_window_layout = None
        self.last_landscape_window = None
        self.landscape_initialized = None
        self.landscape_checked_at = 0.0
        self.landscape_candidate = None
        self.pending_window_layout = None
        self.performance = {}
        self.lock = asyncio.Lock()
        self.player_observe_event = asyncio.Event()
        self.busy = False
        self.connected = False
        self.connection_recovering = False
        self.wsa_found = False
        self.view = "home"
        self.apps: list[dict] = []
        self.apps_loading = False
        self.apps_refresh_task = None
        self.history: list[str] = []
        self.error_text = ""
        self.stopping = False
        self.background_tasks = []
        self.status = ft.Text("正在检测环境", size=12, color=MUTED)
        self.status_dot = ft.Container(width=7, height=7, bgcolor=MUTED, border_radius=8)
        self.progress = ft.ProgressBar(color=ACCENT, bgcolor=LINE, visible=False)
        self.task_text = ft.Text("准备就绪后，点击“一键看红果”", size=12, color=MUTED)
        self.content = ft.Container(expand=True)
        self.install_box: ft.Container | None = None
        self.activity_list: ft.ListView | None = None
        self.log = logging.getLogger("hongguo-desktop")
        if not self.log.handlers:
            handler = RotatingFileHandler(data_directory() / "activity.log", maxBytes=1024 * 1024, backupCount=1, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self.log.addHandler(handler)
            self.log.setLevel(logging.INFO)
        self.runtime = RuntimeDiagnostics(data_directory(), self.log, self.events.put)
        self.guardian = GuardianLink(self.log)
        self.restore_recovery_layout()
        atexit.register(self.guardian.close)
        self.runtime.install_exception_hooks(asyncio.get_running_loop())

    def restore_recovery_layout(self):
        if not self.guardian.recovery:
            return
        try:
            previous = json.loads((data_directory() / "last-unexpected-exit.json").read_text(encoding="utf-8"))
            cache = previous.get("landscape_cache")
            hwnd = self.player_window.find()
            if isinstance(cache, list) and len(cache) == 2 and cache[0] == hwnd and cache[1]:
                self.landscape_initialized = (hwnd, str(cache[1]))
                self.landscape_checked_at = 0.0  # Validate the live Android PID on the first observer tick.
                self.last_window_layout = (hwnd, self.settings.window_mode,
                                           bool(previous.get("screen", {}).get("landscape")))
        except (OSError, ValueError, TypeError, AttributeError):
            self.log.warning("恢复前的窗口状态无法读取，将按当前播放页面重新检测。")

    def text(self, value: str, size: int = 14, color: str = "#ECF1F7", **kwargs):
        return ft.Text(value, size=size, color=color, **kwargs)

    def button(self, title, action, icon=None, primary=False, **kwargs):
        return ft.Button(title, icon=icon, on_click=action, disabled=self.busy,
                         color=BG if primary else "#E9EEF6", bgcolor=ACCENT if primary else "#293340",
                         height=44, style=ft.ButtonStyle(shape=ft.RoundedRectangleBorder(radius=10), elevation=0), **kwargs)

    def card(self, controls, **kwargs):
        kwargs.setdefault("width", float("inf"))
        return ft.Container(content=ft.Column(controls, spacing=16), bgcolor=PANEL,
                            border=ft.Border.all(1, LINE), border_radius=16, padding=22, **kwargs)

    def setup(self):
        page = self.page
        page.title = "红果桌面助手"
        page.bgcolor = BG
        page.padding = 0
        page.theme_mode = ft.ThemeMode.DARK
        page.theme = ft.Theme(color_scheme_seed=ACCENT, font_family="Microsoft YaHei")
        page.window.width = 1160
        page.window.height = 820
        page.window.min_width = 960
        page.window.min_height = 680
        page.window.visible = True
        page.window.skip_task_bar = False
        page.window.prevent_close = True
        if self.guardian.recovery:
            page.window.minimized = True
        page.window.on_event = self.on_window_event
        page.on_error = self.on_page_error
        page.on_disconnect = self.on_page_disconnect
        page.on_connect = self.on_page_connect
        page.on_keyboard_event = self.on_keyboard_event
        sidebar = ft.Container(width=206, bgcolor="#151B23", padding=ft.Padding(20, 28, 20, 24), content=ft.Column([
            ft.Row([ft.Container(ft.Icon(ft.Icons.PLAY_ARROW_ROUNDED, color=BG, size=25), bgcolor=ACCENT,
                                 border_radius=12, padding=7), self.text("红果桌面助手", 16, weight=ft.FontWeight.W_600)], spacing=10),
            self.text("让 Android 操作简单一点", 11, MUTED),
            ft.Container(height=24),
            self.nav("home", "开始使用", ft.Icons.HOME_ROUNDED),
            self.nav("apps", "我的应用", ft.Icons.APPS_ROUNDED),
            self.nav("settings", "常用设置", ft.Icons.TUNE_ROUNDED),
            self.nav("activity", "操作记录", ft.Icons.HISTORY_ROUNDED),
            ft.Container(expand=True),
            ft.Divider(color=LINE),
            ft.Row([self.status_dot, self.status], spacing=8),
            self.text("Windows Android 子系统 · WSA", 11, MUTED),
            self.text("关闭窗口会最小化，播放辅助继续运行", 10, MUTED),
            self.text("退出工具会同时关闭红果播放窗口", 10, MUTED),
            ft.TextButton("退出工具", icon=ft.Icons.EXIT_TO_APP_ROUNDED,
                          on_click=self.exit_tool, style=ft.ButtonStyle(color=MUTED)),
        ], spacing=10, expand=True))
        right = ft.Container(expand=True, padding=ft.Padding(30, 28, 30, 18), content=ft.Column([
            self.content, ft.Container(height=8), self.progress, self.task_text,
        ], expand=True, spacing=8))
        page.add(ft.Row([sidebar, right], expand=True, spacing=0, vertical_alignment=ft.CrossAxisAlignment.STRETCH))
        self.render()
        self.start_background("pump_events", self.pump_events)
        self.start_background("initialize", self.initialize, restart=False)
        self.start_background("watch_player_window", self.watch_player_window)
        self.start_background("watch_window_input", self.watch_window_input)
        self.start_background("watch_runtime", self.watch_runtime)

    def start_background(self, name, handler, *, restart=True):
        self.background_tasks.append(self.page.run_task(
            self.runtime.supervise, name, handler, lambda: self.stopping, restart=restart))

    async def on_window_event(self, event):
        if event.type == ft.WindowEventType.CLOSE and not self.stopping:
            self.page.window.minimized = True
            self.page.window.visible = True
            self.page.update()
            self.runtime.lifecycle("minimized")
            self.events.put("工具已最小化到任务栏，播放辅助继续运行；点击任务栏图标可找回。")
        elif event.type == ft.WindowEventType.MINIMIZE:
            self.page.window.minimized = True
            self.runtime.lifecycle("minimized")
        elif event.type in {ft.WindowEventType.RESTORE, ft.WindowEventType.SHOW}:
            self.page.window.minimized = False
            self.runtime.lifecycle("running")
        elif event.type == ft.WindowEventType.HIDE:
            self.runtime.lifecycle("hidden")

    def on_page_disconnect(self, _):
        self.runtime.lifecycle("ui_disconnected")

    def on_page_connect(self, _):
        self.runtime.lifecycle("running")

    async def on_keyboard_event(self, event):
        if event.ctrl and event.shift and event.key.lower() == "q":
            await self.exit_tool(event, source="keyboard")

    async def exit_tool(self, _=None, *, source="button"):
        if self.stopping:
            return
        self.stopping = True
        reason = "快捷键退出工具 (Ctrl+Shift+Q)" if source == "keyboard" else "点击退出工具"
        self.runtime.update(exit_requested=True, exit_source=source)
        self.runtime.lifecycle("exiting", reason)
        if getattr(self, "guardian", None):
            self.guardian.request_stop()
        if getattr(self, "player_observe_event", None):
            self.player_observe_event.set()
        if getattr(self, "apps_refresh_task", None):
            self.apps_refresh_task.cancel()
        try:
            await asyncio.to_thread(self.clear_window_input_targets)
        except Exception as error:
            self.runtime.error("停止输入", error, exc_info=True)
        async with self.lock:
            for name, cleanup in [("鼠标恢复", self.player_window.finish_tap_control),
                                  ("关闭红果播放窗口", self.window_chrome.close_player),
                                  ("窗口辅助退出", self.window_chrome.close),
                                  ("播放辅助退出", self.playback.close)]:
                try:
                    await asyncio.to_thread(cleanup)
                except Exception as error:
                    self.runtime.error(name, error, exc_info=True)
        # The packaged Flutter host may tear down its embedded interpreter
        # without running Python atexit hooks. Persist the completed cleanup
        # before destroying the native helper window.
        bridge = self.playback.bridge
        self.runtime.update(
            bridge=process_state(bridge.process),
            native=process_state(self.window_chrome.process),
            bridge_diagnostics={
                "last_exit_code": bridge.last_exit_code,
                "last_failure": bridge.last_failure,
                "stderr_tail": bridge.stderr_tail,
                "recoveries": bridge.recoveries,
                "last_shutdown_forced": bridge.last_shutdown_forced,
            },
            player_hwnd=self.window_chrome.hwnd,
        )
        if hasattr(self.window_chrome, "input_status"):
            self.runtime.update(input=await asyncio.to_thread(self.window_chrome.input_status))
        await asyncio.to_thread(self.runtime.process_exit)
        await self.page.window.destroy()

    def on_page_error(self, event):
        self.runtime.error("UI", RuntimeError(getattr(event, "data", str(event))))

    def nav(self, target, label, icon):
        async def click(_):
            self.view = target
            self.render()
        return ft.TextButton(label, icon=icon, on_click=click, width=168,
                             style=ft.ButtonStyle(color="#D9E2EC", padding=ft.Padding(12, 13, 12, 13),
                                                  alignment=ft.Alignment.CENTER_LEFT))

    def render(self):
        builders = {"home": self.home, "apps": self.apps_view, "settings": self.settings_view, "activity": self.activity_view}
        self.content.content = builders[self.view]()
        self.progress.visible = self.busy
        recovering = getattr(self, "connection_recovering", False)
        self.status.value = "播放辅助 · 正在恢复连接" if recovering else "Android 已连接" if self.connected else "环境就绪 · 待启动" if self.wsa_found else "正在检测运行环境"
        self.status_dot.bgcolor = GREEN if self.connected and not recovering else ACCENT
        self.page.update()

    def heading(self, title, subtitle):
        return ft.Column([self.text(title, 26, weight=ft.FontWeight.W_600), self.text(subtitle, 13, MUTED)], spacing=6)

    def error_banner(self):
        return ft.Container(bgcolor="#382A28", border_radius=12, padding=16, visible=bool(self.error_text),
                            content=ft.Column([self.text(self.error_text, 13, "#FFCAB9"),
                            ft.Row([self.button("重试连接", self.connect, ft.Icons.REFRESH_ROUNDED)], wrap=True)], spacing=12))

    def home(self):
        favorite_name = self.settings.app_names.get(self.settings.favorite, self.settings.favorite)
        installed = any(app["package"] == self.settings.favorite for app in self.apps)
        self.install_box = ft.Container(
            width=float("inf"),
            border=ft.Border.all(1.5, "#63748A"), border_radius=15, bgcolor="#18222E", padding=26,
            content=ft.Column([
                ft.Icon(ft.Icons.DOWNLOAD_FOR_OFFLINE_OUTLINED, size=38, color=GREEN),
                self.text("把 APK 安装包拖到这里", 19, weight=ft.FontWeight.W_500),
                self.text("自动连接并安装，支持一次拖入多个 APK", 12, MUTED),
                self.button("选择安装包", self.pick_apks, ft.Icons.FOLDER_OPEN_ROUNDED),
            ], spacing=10, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
        )
        dropzone = ftd.Dropzone(content=self.install_box, allowed_file_types=["apk"], on_dropped=self.dropped,
                               on_entered=self.drag_entered, on_exited=self.drag_exited)
        return ft.Column([
            self.heading("你的 Android 桌面入口", "环境启动、应用安装、日常操作，都在一个窗口里。"),
            self.error_banner(),
            ft.Container(bgcolor="#28333E", border=ft.Border.all(1, "#425162"), border_radius=18, padding=26,
                content=ft.Row([
                    ft.Column([
                        self.text("你的日常入口", 12, "#B4C4D5"),
                        self.text(favorite_name, 25, weight=ft.FontWeight.W_600),
                        self.text("已安装到 Android" if installed else "拖入 APK 即可安装" if self.connected else "连接后检查应用状态", 12, MUTED),
                        ft.Container(height=3),
                        self.button("一键看红果" if self.settings.favorite == HONGGUO else "一键打开应用",
                                    self.launch_favorite, ft.Icons.PLAY_ARROW_ROUNDED, primary=True),
                    ], spacing=9, expand=True),
                    ft.Container(ft.Icon(ft.Icons.SMART_DISPLAY_ROUNDED, size=66, color=ACCENT),
                                 padding=24, border_radius=24, bgcolor="#1C2631"),
                ])),
            ft.Row([self.button("启动 / 连接 Android", self.connect, ft.Icons.POWER_SETTINGS_NEW_ROUNDED),
                    self.button("重新检测", self.refresh, ft.Icons.REFRESH_ROUNDED),
                    self.button("重启 Android", lambda _: self.confirm("重启 Android", "正在运行的 Android 应用会关闭，然后重新启动环境。", self.manager.restart), ft.Icons.RESTART_ALT_ROUNDED)], wrap=True),
            self.window_controls(),
            dropzone,
            self.text("在红果里挑选喜欢的剧并进入播放；起播、全屏和清晰度偏好在“常用设置”中调整。", 12, MUTED),
        ], spacing=19, scroll=ft.ScrollMode.AUTO, expand=True)

    def apps_view(self):
        rows = []
        for app in self.apps:
            package = app["package"]
            async def launch(_, pkg=package):
                await self.perform("打开应用", lambda: self.manager.launch(pkg))
            async def stop(_, pkg=package):
                await self.perform("关闭应用", lambda: self.manager.stop_app(pkg))
            async def favorite(_, pkg=package):
                if self.busy:
                    return
                self.settings.favorite = pkg
                self.store.save(self.settings)
                self.toast("已设为一键启动的默认应用")
                self.render()
            async def uninstall(_, pkg=package, name=app["name"]):
                self.confirm("卸载 " + name, "卸载会删除此应用在 Android 中的数据，是否继续？",
                             lambda: self.manager.uninstall(pkg))
            rows.append(self.card([
                ft.Row([ft.Icon(ft.Icons.SMARTPHONE_ROUNDED, color=ACCENT),
                        ft.Column([self.text(app["name"], 17), self.text(package, 11, MUTED)], expand=True),
                        self.text("默认启动" if package == self.settings.favorite else "", 11, GREEN)], spacing=14),
                ft.Row([self.button("打开", launch, ft.Icons.PLAY_ARROW, primary=True), self.button("关闭", stop),
                        self.button("设为默认", favorite), self.button("卸载", uninstall)], wrap=True),
            ]))
        if not rows and self.apps_loading:
            rows = [self.card([self.text("正在读取应用列表…", 18)])]
        elif not rows:
            rows = [self.card([ft.Icon(ft.Icons.APPS_ROUNDED, size=38, color=MUTED), self.text("暂时没有读取到应用", 18),
                               self.text("先连接 Android，或在首页拖入 APK 安装。", 13, MUTED),
                               self.button("连接并刷新", self.connect, ft.Icons.REFRESH_ROUNDED)])]
        return ft.Column([self.heading("我的应用", "打开、关闭或设置默认应用，无需输入命令。"),
                          self.error_banner(), *rows], spacing=18, scroll=ft.ScrollMode.AUTO, expand=True)

    def settings_view(self):
        auto_open = ft.Switch(label="启动工具后，自动打开默认应用", value=self.settings.auto_open, disabled=self.busy)
        after_install = ft.Switch(label="安装成功后，自动打开应用", value=self.settings.open_after_install, disabled=self.busy)
        timeout = ft.TextField(label="启动等待时间（秒）", value=str(self.settings.startup_timeout), width=230, disabled=self.busy)
        port = ft.TextField(label="WSA 连接端口", value=str(self.settings.adb_port), width=230, disabled=self.busy)
        start_rule = ft.Dropdown(label="从推荐进入后的起播规则", value=self.settings.start_rule, width=330, disabled=self.busy,
            options=[ft.DropdownOption("smart", "智能从第 1 集开始"), ft.DropdownOption("keep", "保留红果当前集数")])
        protect = ft.Switch(label="保护本机已记录的观看进度", value=self.settings.protect_history, disabled=self.busy)
        fullscreen = ft.Switch(label="每部剧首次进入时自动全屏 / 清屏（可选）", value=self.settings.auto_fullscreen, disabled=self.busy)
        quality = ft.Dropdown(label="进入剧集时的清晰度", value=self.settings.preferred_quality, width=260, disabled=self.busy,
            options=[ft.DropdownOption("1080", "优先 1080P（默认）"), ft.DropdownOption("720", "优先 720P"), ft.DropdownOption("keep", "保持红果设置")])
        direct = ft.Switch(label="启动 / 连接时保持 WSA 直连", value=self.settings.keep_wsa_direct, disabled=self.busy)
        async def save(_):
            try:
                from dataclasses import replace
                updated = replace(self.settings, auto_open=bool(auto_open.value), open_after_install=bool(after_install.value),
                                  startup_timeout=int(timeout.value), adb_port=int(port.value),
                                  start_rule=start_rule.value, protect_history=bool(protect.value),
                                  auto_fullscreen=bool(fullscreen.value), preferred_quality=quality.value,
                                  keep_wsa_direct=bool(direct.value))
                async with self.lock:
                    self.store.save(updated)
                    if updated.adb_port != self.settings.adb_port:
                        await asyncio.to_thread(self.playback.reset)
                    self.settings = updated
                    self.manager.settings = updated
                    self.playback.settings_changed()
                    if self.connected and updated.keep_wsa_direct:
                        await asyncio.to_thread(self.manager.configure_direct_network)
                self.toast("设置已保存并生效；起播规则从下次手动进入剧集时应用")
                self.events.put("播放偏好已保存：" + ("智能起播" if updated.start_rule == "smart" else "保留集数") +
                    "，" + (updated.preferred_quality + "P" if updated.preferred_quality != "keep" else "保持清晰度"))
            except (ValueError, TypeError, OSError, OperationError) as error:
                self.toast(str(error))
        async def apply_direct(_):
            await self.perform("设置 WSA 直连", self.manager.configure_direct_network)
        font = ft.Dropdown(label="Android 字体大小", value="1.0", width=230, disabled=self.busy,
                           options=[ft.DropdownOption(k, t) for k, t in [("0.85", "小"), ("1.0", "标准"), ("1.15", "大"), ("1.3", "更大")]])
        screen = ft.Dropdown(label="Android 屏幕休眠", value="15000", width=230, disabled=self.busy,
                             options=[ft.DropdownOption(k, t) for k, t in [("15000", "15 秒"), ("30000", "30 秒"),
                                 ("60000", "1 分钟"), ("300000", "5 分钟"), ("600000", "10 分钟"),
                                 ("1800000", "30 分钟"), ("2147483647", "长时间不休眠")]])
        async def read_display(_):
            async def update():
                values = await asyncio.to_thread(self.manager.read_display_settings)
                if values.get("font_scale") in {"0.85", "1.0", "1.15", "1.3"}:
                    font.value = values["font_scale"]
                if values.get("screen_off_timeout") in SCREEN_TIMEOUTS:
                    screen.value = values["screen_off_timeout"]
                self.page.update()
            try:
                await update()
                self.toast("已读取当前显示设置")
            except OperationError as error:
                self.toast(str(error))
        async def apply_display(_):
            await self.perform("应用显示设置", lambda: self.manager.apply_display_settings(font.value, screen.value))
        async def system_settings(_):
            await self.perform("打开 Android 设置", self.manager.open_system_settings)
        return ft.Column([
            self.heading("常用设置", "调整启动、显示和系统选项，保存后下次启动继续使用。"),
            self.error_banner(),
            self.card([self.text("追剧偏好", 17, weight=ft.FontWeight.W_600), start_rule,
                       self.text("你自己从推荐页进入。推荐第 3 集，进入后仍是第 3 集 → 从第 1 集看；进入后是其他集数 → 保留进度。", 12, MUTED),
                       protect, self.text("仅记住本工具运行期间进入过的剧；不读取红果账号的云端历史。", 11, MUTED),
                       fullscreen, self.text("横屏剧进入全屏观看，竖屏剧进入清屏播放；窗口比例跟随下方窗口选项。", 11, MUTED),
                       self.text("保留完整视频画面。竖屏清屏页的集数、倍速、选集栏仍由红果显示，目前无法单独自动隐藏。", 11, MUTED),
                       quality, self.text("没有目标档位时选择可用的较低档位，不放大伪造画质。切集由红果续播，你的手动选集不会被跳回。", 11, MUTED),
                       self.button("保存设置并应用", save, ft.Icons.SAVE_OUTLINED, primary=True)]),
            self.card([self.text("启动与安装", 17, weight=ft.FontWeight.W_600), auto_open, after_install,
                       ft.Row([port, timeout], wrap=True),
                       self.text("当前只连接 WSA。内存与显卡在下方的 WSA 中文设置中调整。", 11, MUTED),
                       self.button("保存设置", save, ft.Icons.SAVE_OUTLINED, primary=True)]),
            self.card([self.text("安卓网络", 17, weight=ft.FontWeight.W_600), direct,
                       self.text("关闭 WSA 内的显式代理，设置会保留。作用于 WSA 内全部安卓应用，电脑的 v2rayN 等代理保持原设置。", 12, MUTED),
                       self.text("整机 VPN / TUN 仍可能接管流量；关闭此开关仅停止自动设置。直连不保证视频加载更快。", 11, MUTED),
                       ft.Row([self.button("立即设置直连", apply_direct, ft.Icons.WIFI_ROUNDED),
                               self.button("保存网络偏好", save, ft.Icons.SAVE_OUTLINED)], wrap=True)]),
            self.card([self.text("Android 显示", 17, weight=ft.FontWeight.W_600), ft.Row([font, screen], wrap=True),
                       ft.Row([self.button("读取当前设置", read_display, ft.Icons.REFRESH_ROUNDED),
                               self.button("应用显示设置", apply_display, ft.Icons.CHECK_ROUNDED)], wrap=True),
                       self.text("字体和休眠设置会应用到整个 Android 环境。", 11, MUTED)]),
            self.window_controls(),
            self.card([self.text("性能、声音与系统", 17, weight=ft.FontWeight.W_600),
                       self.text("Android 设置可调整语言等系统选项；声音可在 Windows 音量混合器中调整。", 12, MUTED),
                       ft.Row([self.button("WSA 中文设置", lambda _: open_wsa_settings(), ft.Icons.MEMORY_ROUNDED),
                               self.button("Android 设置", system_settings, ft.Icons.PHONE_ANDROID_ROUNDED),
                               self.button("音量混合器", lambda _: os.startfile("ms-settings:apps-volume"), ft.Icons.VOLUME_UP_ROUNDED)], wrap=True),
                       self.button("关闭 Android 环境", lambda _: self.confirm("关闭 Android 环境", "所有 Android 应用都会关闭。确认现在关闭？", self.manager.shutdown), ft.Icons.POWER_SETTINGS_NEW_ROUNDED)]),
        ], spacing=18, scroll=ft.ScrollMode.AUTO, expand=True)

    def window_controls(self):
        names = {"auto": "自动横竖屏", "landscape": "横屏窗口", "portrait": "竖屏窗口"}
        def action(mode):
            async def clicked(_):
                if self.busy:
                    return
                self.settings.window_mode = mode
                self.store.save(self.settings)
                self.last_window_layout = None
                await self.perform("调整播放窗口", lambda: self.fit_player_window(manual=True))
            return clicked
        async def hide_changed(event):
            if self.busy:
                return
            enabled = bool(event.control.value)
            def apply():
                self.window_chrome.sync(self.player_window.find(), enabled)
                self.settings.hide_titlebar = enabled
                self.chrome_retry_after = 0.0
                self.last_window_layout = None
                self.events.put("已开启无边框：顶部中间移动，边缘或角落等比例缩放" if enabled else "已恢复标准标题栏")
            await self.perform("更新播放窗口外观", apply)
        async def close_player(_):
            def close():
                self.playback.reset()
                found = self.window_chrome.close_player()
                self.last_window_layout = None
                self.events.put("已关闭红果播放窗口" if found else "红果播放窗口已经关闭")
            await self.perform("关闭红果窗口", close)
        return self.card([
            self.text("红果播放窗口 · " + names[self.settings.window_mode], 16, weight=ft.FontWeight.W_600),
            ft.Row([self.button(label, action(mode), primary=mode == self.settings.window_mode)
                    for mode, label in names.items()], wrap=True),
            self.text("自动横竖屏只跟随 APP 播放方向调整窗口。默认手动点击全屏；可在追剧偏好中开启每部剧首次进入时自动全屏，退出后不会再次自动进入。", 11, MUTED),
            ft.Switch(label="隐藏白色标题栏", value=self.settings.hide_titlebar,
                      disabled=self.busy, on_change=hide_changed),
            self.text("顶部中间拖动可移动窗口；拖动四条边或四个角可等比例缩放，横屏 16:9、竖屏 9:16，各自记住本次窗口的尺寸。滚轮切视频 / 切集，选集面板滚动列表。退出工具后恢复标题栏。", 12, MUTED),
            self.button("关闭红果窗口", close_player, ft.Icons.CLOSE_ROUNDED),
        ])

    def fit_player_window(self, manual=False, hwnd=None):
        if not manual and self.playback.landscape_preparing:
            # The ordinary page still reports portrait content while its
            # resources update. Keep the width prepared before LandActivity.
            return
        hwnd = hwnd or self.player_window.find()
        if not hwnd:
            self.last_window_layout = None
            if manual:
                self.events.put("窗口偏好已保存，下次打开红果时生效")
            return
        screen = self.playback.screen
        if not manual and (screen.kind not in {"feed", "player"} or not screen.nodes):
            self.pending_window_layout = None
            return  # Transient roots and dialogs never choose a new direction.
        if not manual and screen.kind in {"feed", "player"} and screen.nodes:
            l, t, r, b = screen.nodes[0]["bounds"]
            layout = PlayerLayout(screen.landscape, r-l, b-t)
        elif not manual and screen.kind in {"quality", "episodes"}:
            return
        else:
            output = self.manager.command("shell", "dumpsys", "window", "windows", timeout=6)
            if output.returncode:
                if manual:
                    raise OperationError("暂时无法读取播放窗口，请连接 Android 后重试。")
                return
            layout = player_layout(output.stdout)
        if layout is None:
            return
        mode = self.settings.window_mode
        landscape = layout.landscape if mode == "auto" else mode == "landscape"
        key = (hwnd, mode, landscape)
        if not manual and key == self.last_window_layout:
            self.pending_window_layout = None
            return
        if not manual:
            last = getattr(self.playback, "last_landscape", None) or {}
            confirmed = (screen.landscape and layout.width > layout.height
                         and last.get("reason") == "landscape_entered" and last.get("ok"))
            if not confirmed:
                now = time.monotonic()
                pending = getattr(self, "pending_window_layout", None)
                if not pending or pending[0] != key:
                    self.pending_window_layout = (key, now)
                    return
                if now-pending[1] < .18:
                    return
        if self.player_window.resize(hwnd, layout, landscape, manual=manual):
            self.last_window_layout = key
            self.pending_window_layout = None
            if manual:
                self.events.put("已切换为横屏窗口" if landscape else "已切换为竖屏窗口")

    def prepare_landscape_window(self, screen):
        """Size before first LandActivity loads its static episode panel width."""
        state = self.last_landscape_window = {
            "mode": self.settings.window_mode,
            "root_bounds": screen.nodes[0]["bounds"] if screen.nodes else None,
        }
        if self.stopping or self.busy:
            state["reason"] = "stopping_or_busy"
            return False
        hwnd = self.player_window.find()
        state["hwnd"] = hwnd
        if not hwnd or not screen.nodes:
            state["reason"] = "no_window_or_root"
            return False
        state["iconic"] = bool(self.player_window.user.IsIconic(hwnd))
        state["zoomed"] = bool(self.player_window.user.IsZoomed(hwnd))
        if state["iconic"]:
            state["reason"] = "minimized"
            return False
        if self.settings.window_mode == "portrait":
            state["reason"] = "fixed_portrait"
            return None
        left, top, right, bottom = screen.nodes[0]["bounds"]
        width, height = right-left, bottom-top
        if width <= 0 or height <= 0:
            state["reason"] = "invalid_root"
            return False
        pid_result = self.manager.command("shell", "dumpsys", "window", "windows", timeout=3)
        pid = visible_app_pid(pid_result.stdout) if not pid_result.returncode else None
        state["app_pid"] = pid
        candidate = (hwnd, pid) if pid else None
        self.landscape_checked_at = time.monotonic()
        self.landscape_candidate = candidate
        if candidate and candidate == getattr(self, "landscape_initialized", None):
            state["reason"] = "cached"
            return "cached"
        self.landscape_initialized = None
        if state["zoomed"]:
            # A wide maximized window only needs the snapshot/config settle;
            # never restore or resize a maximization the user chose.
            state["reason"] = "maximized_landscape" if width > height else "maximized_portrait"
            return True if width > height else None
        if self.settings.hide_titlebar:
            # WSA's F11 presentation preserves the Android display ratio and
            # letterboxes a resized outer window. Restore normal composition
            # until Android has received the new width and opened LandActivity.
            state["reason"] = "caption_restoring"
            if not self.window_chrome.begin_geometry_change():
                state["reason"] = "caption_restore_timeout"
                return False
        state["reason"] = "resize_sending"
        resized = self.player_window.resize(hwnd, PlayerLayout(True, width, height), True)
        state["reason"] = "resized" if resized else "resize_rejected"
        if resized:
            self.last_window_layout = (hwnd, self.settings.window_mode, True)
            return True
        return False

    def native_player_fullscreen_ready(self):
        key = getattr(self, "landscape_initialized", None)
        return bool(isinstance(key, tuple) and key[0] == self.window_chrome.hwnd
                    and time.monotonic()-getattr(self, "landscape_checked_at", 0) < 6)

    def refresh_landscape_process(self, hwnd):
        """Expire initialized geometry when the Android process changes."""
        key = getattr(self, "landscape_initialized", None)
        if not isinstance(key, tuple):
            return
        if key[0] != hwnd:
            self.landscape_initialized = None
            return
        now = time.monotonic()
        if now-getattr(self, "landscape_checked_at", 0) < 5:
            return
        result = self.manager.command("shell", "pidof", HONGGUO, timeout=3)
        self.landscape_checked_at = time.monotonic()
        if result.returncode or key[1] not in result.stdout.split():
            self.landscape_initialized = None

    def activate_fullscreen_control(self, screen, node):
        self.last_fullscreen_activation = {"reason": "checking"}
        if self.stopping or self.busy or not screen.nodes:
            self.last_fullscreen_activation["reason"] = "stopping_busy_or_no_root"
            return False
        hwnd = self.player_window.find()
        if not hwnd:
            self.last_fullscreen_activation["reason"] = "no_native_window"
            return False
        if not self.playback.bridge.node_action("validate", node):
            self.last_fullscreen_activation["reason"] = "node_changed"
            return False
        clicked = self.player_window.tap_control(hwnd, screen.nodes[0]["bounds"], node["bounds"], defer_restore=True)
        self.last_fullscreen_activation["reason"] = "sent" if clicked else "pointer_focus_or_window_changed"
        if (clicked and getattr(self.playback, "landscape_cached", False) is True
                and self.settings.window_mode != "portrait" and not self.player_window.user.IsZoomed(hwnd)):
            # A checked fullscreen action already owns this transition. Fit a
            # previously initialized window immediately after releasing the
            # button, instead of leaving the rotated surface in a tall window
            # until Android publishes its new accessibility geometry.
            left, top, right, bottom = screen.nodes[0]["bounds"]
            if self.player_window.resize(hwnd, PlayerLayout(True, right-left, bottom-top), True):
                self.last_window_layout = (hwnd, self.settings.window_mode, True)
        return clicked

    def clear_window_input_targets(self):
        window_input = getattr(self, "window_input", None)
        clear = getattr(window_input, "clear_targets", None)
        if clear:
            clear()

    def record_timing(self, name, started):
        # Sample existing calls on the event loop; bounded local diagnostics
        # never introduce another Android or native request.
        if not hasattr(self, "performance"):
            self.performance = {}
        samples = self.performance.setdefault(name, [])
        samples.append(round((time.perf_counter()-started)*1000, 3))
        del samples[:-40]

    def finish_window_geometry(self):
        if self.stopping or self.playback.landscape_preparing:
            return
        self.player_window.finish_tap_control()
        candidate = getattr(self, "landscape_candidate", None)
        result = getattr(self.playback, "last_landscape", None) or {}
        screen = getattr(self.playback, "screen", None)
        if candidate and result.get("reason") == "landscape_entered" and result.get("ok") and screen and screen.landscape and screen.nodes:
            left, top, right, bottom = screen.nodes[0]["bounds"]
            if right-left > bottom-top:
                self.landscape_initialized = candidate
        self.landscape_candidate = None
        if getattr(self.window_chrome, "geometry_holding", False) is True:
            self.window_chrome.end_geometry_change(self.settings.hide_titlebar)

    async def wait_player_observer(self, interval):
        event = getattr(self, "player_observe_event", None)
        if event is None:
            await asyncio.sleep(interval)
            return
        try:
            await asyncio.wait_for(event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
        finally:
            event.clear()

    def notify_player_change(self):
        self.playback.fast_until = max(getattr(self.playback, "fast_until", 0.0), time.monotonic()+1.2)
        event = getattr(self, "player_observe_event", None)
        if event:
            event.set()

    async def watch_player_window(self):
        last_fallback_fit = 0.0
        retry_after = 0.0
        failure_reported = False
        failures = 0
        while not self.stopping:
            self.runtime.beat("watch_player_window")
            interval = self.playback.poll_interval
            if getattr(self, "pending_window_layout", None):
                interval = min(interval, .20)
            await self.wait_player_observer(interval)
            if self.stopping:
                return
            if self.busy:
                # Keep the native player presentation working while Android
                # starts or a foreground management command is waiting.
                hwnd = self.player_window.find()
                if hwnd and not self.stopping:
                    try:
                        if time.monotonic() >= self.chrome_retry_after:
                            await asyncio.to_thread(self.window_chrome.sync, hwnd, self.settings.hide_titlebar)
                    except (OperationError, OSError) as error:
                        self.chrome_retry_after = time.monotonic()+15
                        self.events.put("窗口外观辅助将自动重试：" + str(error))
                continue
            if self.lock.locked():
                continue
            async with self.lock:
                if self.stopping:
                    return
                try:
                    now = time.monotonic()
                    hwnd = self.player_window.find()
                    # Native window presentation remains usable even when the
                    # Android UI bridge is disconnected or reconnecting.
                    if now >= self.chrome_retry_after or not hwnd:
                        try:
                            await asyncio.to_thread(self.window_chrome.sync, hwnd, self.settings.hide_titlebar)
                        except (OperationError, OSError) as error:
                            self.chrome_retry_after = now+15
                            self.events.put("窗口外观辅助将自动重试：" + str(error))
                    if self.stopping:
                        return
                    if not hwnd or self.player_window.user.IsIconic(hwnd):
                        await asyncio.to_thread(self.clear_window_input_targets)
                        self.landscape_candidate = None
                        self.pending_window_layout = None
                        if not hwnd:
                            if getattr(self, "connection_recovering", False):
                                self.connection_recovering = False
                                self.render()
                            self.landscape_initialized = None
                            self.last_window_layout = None
                        if self.playback.bridge.process:
                            await asyncio.to_thread(self.playback.reset)
                        await asyncio.to_thread(self.finish_window_geometry)
                        continue
                    if now >= retry_after:
                        tick_started = time.perf_counter()
                        try:
                            await asyncio.to_thread(self.playback.tick)
                        finally:
                            self.record_timing("observer_tick_ms", tick_started)
                        if self.stopping:
                            return
                        await asyncio.to_thread(self.refresh_landscape_process, hwnd)
                        refresh = getattr(self.window_input, "refresh_targets", None)
                        if refresh:
                            await asyncio.to_thread(refresh)
                        if self.stopping:
                            return
                        failure_reported = False
                        failures = 0
                        if self.manager.connection_state == "device" and (not self.connected or getattr(self, "connection_recovering", False)):
                            self.connected = True
                            self.connection_recovering = False
                            self.error_text = ""
                            self.render()
                        if self.playback.screen.kind in {"feed", "player"} and now-getattr(self, "last_splash_check", 0) >= 5:
                            await asyncio.to_thread(self.player_window.hide_loaded_splashes, hwnd)
                            self.last_splash_check = now
                        if (not self.playback.landscape_preparing
                                and getattr(self.window_chrome, "geometry_holding", False) is True):
                            # Caption restoration changed the native frame;
                            # fit from the final fresh Android geometry once.
                            self.last_window_layout = None
                        if self.playback.screen.kind in {"feed", "player"}:
                            # Uses the snapshot already read above and resizes
                            # only on direction changes; no extra ADB command.
                            await asyncio.to_thread(self.fit_player_window, hwnd=hwnd)
                        elif now-last_fallback_fit >= 3:
                            await asyncio.to_thread(self.fit_player_window, hwnd=hwnd)
                            last_fallback_fit = now
                        await asyncio.to_thread(self.finish_window_geometry)
                except (OperationError, OSError, ValueError) as error:
                    # An app closing during a poll is normal. No popups or
                    # environment activation from this background observer.
                    self.last_window_layout = None
                    await asyncio.to_thread(self.clear_window_input_targets)
                    self.landscape_initialized = self.landscape_candidate = None
                    self.pending_window_layout = None
                    failures += 1
                    retry_after = time.monotonic()+min(2**(failures-1), 15)
                    self.connection_recovering = True
                    self.connected = self.manager.connection_state == "device"
                    self.render()
                    await asyncio.to_thread(self.playback.reset)
                    await asyncio.to_thread(self.finish_window_geometry)
                    if not failure_reported:
                        self.events.put("播放辅助暂不可用，将自动重连：" + str(error))
                        failure_reported = True

    async def watch_window_input(self):
        """Wake on native mouse input instead of waiting for the page observer."""
        while not self.stopping:
            self.runtime.beat("watch_window_input")
            connected = self.playback.bridge.process is not None and self.playback.bridge.process.poll() is None
            if self.busy or not connected:
                await asyncio.to_thread(self.clear_window_input_targets)
                if self.stopping:
                    return
            signaled = await asyncio.to_thread(self.window_chrome.wait_input, connected and not self.busy)
            if self.stopping:
                return
            if not signaled:
                continue
            signaled_at = time.perf_counter()
            async with self.lock:
                self.record_timing("input_lock_wait_ms", signaled_at)
                if self.stopping:
                    return
                connected = self.playback.bridge.process is not None and self.playback.bridge.process.poll() is None
                if self.busy or not connected:
                    await asyncio.to_thread(self.clear_window_input_targets)
                    await asyncio.to_thread(self.window_chrome.take_wheel)
                    continue
                try:
                    dispatch_started = time.perf_counter()
                    try:
                        result = await asyncio.to_thread(self.window_input.dispatch)
                    finally:
                        self.record_timing("input_dispatch_ms", dispatch_started)
                    if self.stopping:
                        return
                    if result is not None:
                        self.notify_player_change()
                    if result is not None and result.get("method") == "page_refresh":
                        self.runtime.update(last_page_input={"at": self.runtime.timestamp(), "method": "page_refresh"})
                    elif result is not None:
                        self.runtime.update(last_wheel={
                            "at": self.runtime.timestamp(), "page": self.playback.screen.kind,
                            "ok": result.get("ok"), "edge": result.get("edge"),
                            "method": result.get("method"), "target": result.get("target"),
                        })
                except (OperationError, OSError, ValueError) as error:
                    self.runtime.error("窗口输入", error, exc_info=True)
                    self.events.put("窗口操作未完成：" + str(error))

    async def watch_runtime(self):
        while not self.stopping:
            self.guardian.pulse()
            self.runtime.beat("watch_runtime")
            screen = self.playback.screen
            self.runtime.update(
                guardian={"enabled": self.guardian.enabled, "alive": self.guardian.alive(),
                          "recovery": self.guardian.recovery},
                bridge=process_state(self.playback.bridge.process),
                bridge_diagnostics={
                    "last_exit_code": self.playback.bridge.last_exit_code,
                    "last_failure": self.playback.bridge.last_failure,
                    "stderr_tail": self.playback.bridge.stderr_tail,
                    "recoveries": self.playback.bridge.recoveries,
                    "last_shutdown_forced": self.playback.bridge.last_shutdown_forced,
                    "last_snapshot": getattr(self.playback.bridge, "last_snapshot", None),
                    "recent_snapshot_ms": getattr(self.playback.bridge, "recent_snapshot_ms", []),
                },
                native=process_state(self.window_chrome.process),
                fullscreen_presentation=getattr(self.window_input, "fullscreen_presentation", None),
                fullscreen_passthrough=getattr(self.window_input, "fullscreen_passthrough", False),
                fullscreen_target=getattr(self.window_chrome, "_fullscreen_target_key", None),
                fullscreen_activation=getattr(self, "last_fullscreen_activation", None),
                last_landscape={**(getattr(self.playback, "last_landscape", None) or {}),
                                "window": getattr(self, "last_landscape_window", None)},
                landscape_cache=getattr(self, "landscape_initialized", None),
                performance={name: samples[:] for name, samples in getattr(self, "performance", {}).items()},
                screen={"kind": screen.kind, "title": screen.title,
                        "episode": screen.episode, "landscape": screen.landscape,
                        "stage": self.playback.stage,
                        "landscape_preparing": self.playback.landscape_preparing,
                        "root_bounds": screen.nodes[0]["bounds"] if screen.nodes else None,
                        "episode_cells": [{"episode": n.get("text"), "bounds": n.get("bounds")}
                                          for n in screen.nodes if short_id(n) == "jk6" and n.get("text", "").isdigit()][:40],
                        "controls": [{"id": short_id(n), "text": n.get("text"), "bounds": n.get("bounds")}
                                     for n in screen.nodes if short_id(n) in {"arc", "hwv", "f", "eft", "kea"} or n.get("text") == "全屏观看"][:12]},
                busy=self.busy, player_hwnd=self.window_chrome.hwnd,
                connection={"state": self.manager.connection_state,
                            "last_command": self.manager.last_command,
                            "last_error": self.manager.last_connection_error},
                apps_loading=self.apps_loading,
                connection_recovering=getattr(self, "connection_recovering", False),
                hide_titlebar=self.settings.hide_titlebar,
                helper_window={"minimized": self.page.window.minimized,
                               "visible": self.page.window.visible},
            )
            if hasattr(self.window_chrome, "input_status"):
                input_state = await asyncio.to_thread(self.window_chrome.input_status)
                if self.stopping:
                    return
                self.runtime.update(input=input_state)
            if self.stopping:
                return
            await asyncio.to_thread(self.runtime.write)
            await asyncio.sleep(3)

    def activity_view(self):
        self.activity_list = ft.ListView([self.text(line, 12, MUTED, selectable=True) for line in self.history],
                                         spacing=9, expand=True, auto_scroll=True)
        return ft.Column([self.heading("操作记录", "查看启动、安装和连接过程。记录只保存在本机。"),
                          self.button("打开日志文件夹", lambda _: os.startfile(str(data_directory())), ft.Icons.FOLDER_OPEN_ROUNDED),
                          ft.Container(self.activity_list, bgcolor=PANEL, border_radius=14, padding=20, expand=True)], expand=True, spacing=20)

    def toast(self, message):
        self.page.show_dialog(ft.SnackBar(self.text(message, 13), bgcolor="#354150", duration=4000))

    def confirm(self, title, description, operation):
        if self.busy:
            return
        async def accepted(_):
            self.page.pop_dialog()
            await self.perform(title, operation)
        self.page.show_dialog(ft.AlertDialog(title=self.text(title, 20), content=self.text(description, 14),
            actions=[ft.TextButton("取消", on_click=lambda _: self.page.pop_dialog()),
                     ft.TextButton("确认", on_click=accepted, style=ft.ButtonStyle(color=ACCENT))]))

    async def perform(self, title, operation):
        if self.busy:
            self.toast("正在处理当前操作，请稍候。")
            return None
        async with self.lock:
            self.busy = True
            await asyncio.to_thread(self.clear_window_input_targets)
            self.error_text = ""
            self.events.put(title)
            self.render()
            result = None
            try:
                result = await asyncio.to_thread(operation)
                await asyncio.to_thread(self.store.save, self.settings)
            except Exception as error:
                self.error_text = str(error)
                self.events.put("未完成：" + str(error))
                self.log.exception(title)
            finally:
                # Commands already know their connection result. Do not keep
                # the entire UI and player input locked while fetching optional
                # app metadata after a successful launch.
                self.connected = self.manager.connection_state == "device"
                self.wsa_found = self.manager.installation is not None
                self.busy = False
                self.render()
                if self.connected and not self.stopping:
                    self.schedule_apps_refresh()
            return result

    def schedule_apps_refresh(self):
        if self.apps_refresh_task is None or self.apps_refresh_task.done():
            self.apps_refresh_task = asyncio.create_task(self.refresh_apps())

    async def refresh_apps(self):
        self.apps_loading = True
        try:
            apps = await asyncio.to_thread(self.manager.list_apps)
            if not self.stopping:
                self.apps = apps
        except OperationError as error:
            # An application-list timeout does not undo a ready player or
            # report a successful connection/launch as failed.
            self.log.warning("应用列表稍后刷新：%s", error)
        finally:
            self.apps_loading = False
            if not self.stopping:
                self.render()

    async def initialize(self):
        if self.store.load_warning:
            self.events.put(self.store.load_warning)
        await self.refresh(None)
        if getattr(getattr(self, "guardian", None), "recovery", False):
            self.events.put("后台辅助已自动恢复，已接回现有播放窗口。")
            return
        if self.settings.auto_open and self.wsa_found:
            await self.launch_favorite(None)

    async def refresh(self, _):
        await self.perform("正在检测 Android 环境…", self.manager.detect)

    async def connect(self, _):
        await self.perform("启动 / 连接 Android", self.manager.ensure_ready)

    async def launch_favorite(self, _):
        await self.perform("一键启动默认应用", self.manager.launch)

    async def pick_apks(self, _):
        if self.busy:
            return
        files = await ft.FilePicker().pick_files(dialog_title="选择 Android 安装包", allow_multiple=True,
                                                 file_type=ft.FilePickerFileType.CUSTOM, allowed_extensions=["apk"])
        if files:
            await self.install_paths([file.path for file in files if file.path])

    async def dropped(self, event: ftd.DropzoneEvent):
        self.drag_exited(None)
        await self.install_paths([file.path for file in event.files if file.path])

    def drag_entered(self, _):
        if self.install_box:
            self.install_box.bgcolor = "#263C38"
            self.install_box.border = ft.Border.all(2, GREEN)
            self.install_box.update()

    def drag_exited(self, _):
        if self.install_box:
            self.install_box.bgcolor = "#18222E"
            self.install_box.border = ft.Border.all(1.5, "#63748A")
            self.install_box.update()

    async def install_paths(self, paths):
        paths = list(dict.fromkeys(paths))
        if len(paths) > 10:
            self.toast("一次最多安装 10 个 APK，请分批拖入。")
            return
        def install_all():
            completed = []
            launch_warnings = []
            for filename in paths:
                info = self.manager.install(filename)
                self.store.save(self.settings)
                completed.append(info.name)
                if self.settings.open_after_install:
                    try:
                        self.manager.launch(info.package)
                    except OperationError as error:
                        launch_warnings.append(info.name + "：" + str(error))
            self.events.put("安装完成：" + "、".join(completed))
            if launch_warnings:
                raise OperationError("安装已成功，但自动打开未完成。" + "\n".join(launch_warnings))
        if paths:
            await self.perform("收到安装包", install_all)

    async def pump_events(self):
        while not self.stopping:
            self.runtime.beat("pump_events")
            changed = False
            while not self.events.empty():
                message = self.events.get_nowait()
                line = datetime.now().strftime("%H:%M:%S") + "  " + message
                self.history.append(line)
                self.history = self.history[-250:]
                self.log.info(message)
                self.task_text.value = message[:160]
                changed = True
            if changed:
                if self.manager.connection_state == "device" and not self.connected:
                    self.connected = True
                    self.render()
                if self.view == "activity" and self.activity_list:
                    self.activity_list.controls = [self.text(line, 12, MUTED, selectable=True) for line in self.history]
                self.page.update()
            await asyncio.sleep(.2)


async def main(page: ft.Page):
    DesktopApp(page).setup()


def run():
    # Flet's embedded Python and development entry both execute this once.
    # Another desktop shortcut click restores the current helper instead of
    # creating a second UiAutomation connection.
    guard = None
    try:
        guard = SingleInstance()
        guard.trace("entry")
        if not guard.acquire():
            restored = guard.restore_existing(timeout=5)
            guard.trace("duplicate_exit", restored=restored)
            return
        atexit.register(guard.close)
        ft.run(main)
    except BaseException:
        logging.getLogger("hongguo-desktop").exception("工具宿主运行异常")
        try:
            import traceback
            with (data_directory() / "startup-error.log").open("a", encoding="utf-8") as stream:
                stream.write(datetime.now().astimezone().isoformat()+"\n"+traceback.format_exc()+"\n")
        except OSError:
            pass
        raise
    finally:
        if guard:
            guard.close()


if __name__ == "__main__":
    run()
