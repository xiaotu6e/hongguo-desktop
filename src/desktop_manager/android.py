from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .apk_info import ApkInfo, inspect_apk
from .config import HONGGUO, PACKAGE_RE, Settings

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SCREEN_TIMEOUTS = {"15000", "30000", "60000", "300000", "600000", "1800000", "2147483647"}
PROXY_KEYS = ("http_proxy", "global_http_proxy_host", "global_http_proxy_port",
              "global_http_proxy_exclusion_list", "global_proxy_pac_url")
NO_PROXY_VALUES = {key: {"", "null", "0"} if key == "global_http_proxy_port" else {"", "null"}
                   for key in PROXY_KEYS[1:]}


class OperationError(RuntimeError):
    pass


class CommandTimeout(OperationError):
    """A timed out local command, with enough context to diagnose its stage."""

    def __init__(self, args, timeout):
        self.args_run = list(args)
        self.timeout = timeout
        if any("ShutdownAndroidButton" in str(arg) for arg in args):
            stage = "通过 WSA 设置关闭 Android"
        elif any("Get-AppxPackage" in str(arg) for arg in args):
            stage = "检测 Windows 中的 WSA 安装"
        elif "start-server" in args:
            stage = "启动本机 Android 连接服务"
        elif "connect" in args or "get-state" in args:
            stage = "连接本机 Android"
        elif "dumpsys" in args:
            stage = "读取 Android 窗口状态"
        elif "push" in args:
            stage = "准备播放辅助组件"
        elif "shell" in args:
            stage = "读取或设置 Android 状态"
        else:
            stage = "运行本机组件"
        super().__init__(f"{stage}等待超过 {timeout:g} 秒。")


@dataclass(frozen=True)
class WsaInstallation:
    location: Path
    family: str

    @property
    def client(self) -> Path:
        return self.location / "WsaClient" / "WsaClient.exe"


def run_process(args: list[str], timeout: float = 20) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL, timeout=timeout, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired as error:
        raise CommandTimeout(args, timeout) from error
    except OSError as error:
        raise OperationError(f"无法运行所需组件：{error}") from error


def find_wsa(timeout=25) -> WsaInstallation:
    script = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; Get-AppxPackage -Name MicrosoftCorporationII.WindowsSubsystemForAndroid | Select-Object -First 1 InstallLocation,PackageFamilyName | ConvertTo-Json -Compress"
    powershell = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")
    result = run_process([powershell, "-NoProfile", "-NonInteractive", "-Command", script], timeout=timeout)
    try:
        data = json.loads(result.stdout.strip().lstrip("\ufeff"))
        installation = WsaInstallation(Path(data["InstallLocation"]), data["PackageFamilyName"])
        if installation.client.is_file():
            return installation
    except (ValueError, KeyError, TypeError):
        pass
    raise OperationError("没有找到已注册的 WSA。请先安装 WSA，再点击“重新检测”。")


def adb_binary() -> Path:
    # Flet build extracts assets next to main.py; development uses the same layout.
    candidate = Path(__file__).resolve().parents[1] / "assets/platform-tools/adb.exe"
    if candidate.is_file():
        return candidate
    assets = os.environ.get("FLET_ASSETS_DIR")
    if assets and (Path(assets) / "platform-tools/adb.exe").is_file():
        return Path(assets) / "platform-tools/adb.exe"
    raise OperationError("连接组件缺失，请保留工具文件夹的完整内容。")


def install_failure(output: str) -> str:
    errors = {
        "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "这个 APK 与已安装应用的签名不同，不能直接覆盖。原应用和数据已保留。",
        "INSTALL_FAILED_VERSION_DOWNGRADE": "安装包版本比已安装版本旧。请使用更新的安装包，原应用和数据已保留。",
        "INSTALL_FAILED_NO_MATCHING_ABIS": "这个安装包的处理器架构与当前 WSA 不兼容。",
        "INSTALL_FAILED_INSUFFICIENT_STORAGE": "Android 存储空间不足，请先清理空间。",
        "INSTALL_PARSE_FAILED": "安装包解析失败，请重新下载完整的 APK。",
        "unauthorized": "WSA 尚未授权连接，请在 WSA 的授权窗口中选择允许，再重试。",
    }
    return next((message for key, message in errors.items() if key in output), "安装未成功：" + output.strip()[-600:])


class AndroidManager:
    def __init__(self, settings: Settings, notify: Callable[[str], None] = lambda text: None,
                 runner: Callable = run_process, adb: Path | None = None):
        self.settings = settings
        self.settings.validate()
        self.notify = notify
        self.runner = runner
        self.adb = adb or adb_binary()
        self.installation: WsaInstallation | None = None
        self.connection_state = "unknown"
        self.last_command = None
        self.last_connection_error = None
        self._server_started = False
        self._server_lock = threading.Lock()
        self.log = logging.getLogger("hongguo-desktop")

    def command(self, *args: str, timeout: float = 20, device: bool = True) -> subprocess.CompletedProcess:
        base = [str(self.adb), "-P", str(self.settings.server_port)]
        if device:
            base.extend(["-s", self.settings.endpoint])
        started = time.monotonic()
        record = {"operation": " ".join(args[:4]), "timeout_s": timeout}
        try:
            result = self.runner(base + list(args), timeout=timeout)
            record["returncode"] = result.returncode
            if device:
                output = result.stdout + result.stderr
                if "unauthorized" in output:
                    self.connection_state = "unauthorized"
                elif re.search(r"device(?: [^\n]*)? (?:offline|not found)", output) or any(text in output for text in ("no devices/emulators", "cannot connect", "cannot read response")):
                    self.connection_state = "offline"
                    self._server_started = False
                elif result.returncode == 0:
                    self.connection_state = "device"
            return result
        except CommandTimeout as error:
            record["error"] = str(error)
            self.last_connection_error = record.copy()
            self.log.warning("Android 命令超时：%s；限时 %.2fs", record["operation"], timeout)
            raise
        finally:
            record["elapsed_ms"] = round((time.monotonic() - started) * 1000, 1)
            self.last_command = record
            if record["elapsed_ms"] >= 1000:
                self.log.info("Android 慢命令：%s；%.1fms", record["operation"], record["elapsed_ms"])

    def start_server(self, timeout=15, *, force=False):
        # ADB's first daemon startup can take over five seconds. Give that
        # startup its own budget instead of killing a short connect command.
        with self._server_lock:
            if self._server_started and not force:
                return
            result = self.command("start-server", device=False, timeout=timeout)
            if result.returncode:
                raise OperationError("本机 Android 连接服务启动失败：" + result.stderr.strip()[-300:])
            self._server_started = True

    def reconnect(self, timeout=3, budget=21) -> str:
        """Repair the local transport without waking apps or restarting WSA."""
        try:
            deadline = time.monotonic() + budget
            # A previously healthy daemon may have disappeared since the
            # cached check. An explicit repair must bootstrap before connect,
            # even when this manager previously started the server.
            self.start_server(timeout=min(15, budget), force=True)
            self.command("connect", self.settings.endpoint, device=False, timeout=min(timeout, max(.1, deadline-time.monotonic())))
            return self.state(timeout=min(timeout, max(.1, deadline-time.monotonic())))
        except CommandTimeout:
            self.connection_state = "offline"
            self._server_started = False
            return "offline"

    def state(self, timeout=3) -> str:
        try:
            result = self.command("get-state", timeout=timeout)
        except CommandTimeout:
            self.connection_state = "offline"
            self._server_started = False
            return "offline"
        combined = result.stdout + result.stderr
        if "unauthorized" in combined:
            self.connection_state = "unauthorized"
        else:
            self.connection_state = "device" if result.returncode == 0 and result.stdout.strip() == "device" else "offline"
        return self.connection_state

    def detect(self) -> dict:
        self.installation = self.installation or find_wsa()
        connected = self.reconnect() == "device"
        self.notify("检测完成，Android 已连接" if connected else "已找到 WSA，点击启动即可连接")
        return {"installed": True, "connected": connected, "location": str(self.installation.location)}

    def ensure_ready(self, wake_package: str | None = None) -> bool:
        """Connect WSA; return whether its target app URI was used to wake it."""
        wake_package = wake_package or self.settings.favorite
        self.validate_package(wake_package)
        deadline = time.monotonic() + self.settings.startup_timeout
        if self.installation is None:
            self.notify("正在识别 Android 环境…")
            self.installation = find_wsa(timeout=min(25, max(.1, deadline-time.monotonic())))
        self.notify("正在连接 Android…")
        activated = False
        next_wake = 0.0
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            try:
                self.start_server(timeout=min(15, remaining))
                if self.connection_state != "device":
                    self.command("connect", self.settings.endpoint, device=False, timeout=min(3, max(.1, deadline-time.monotonic())))
                state = self.state(timeout=min(3, max(.1, deadline-time.monotonic())))
            except CommandTimeout:
                # Short probes can time out while Android is booting. They
                # must not abort the entire user-configured startup budget.
                self.connection_state = state = "offline"
                self._server_started = False
            if state == "unauthorized":
                raise OperationError("请在 WSA 弹出的连接授权窗口选择“允许”，然后点“重试”。")
            if state == "device":
                try:
                    boot = self.command("shell", "getprop", "sys.boot_completed", timeout=min(3, max(.1, deadline-time.monotonic())))
                except CommandTimeout:
                    boot = None
                if boot is not None and boot.returncode == 0 and boot.stdout.strip() == "1":
                    if self.settings.keep_wsa_direct:
                        try:
                            self.configure_direct_network(timeout=2)
                        except OperationError as error:
                            self.notify("安卓直连设置未完成：" + str(error) + " 应用将继续启动。")
                    self.notify("Android 已就绪")
                    return activated
            elif time.monotonic() >= next_wake:
                # Windows can drop an activation while a previous WSA instance
                # is still shutting down. Retry within the user's timeout.
                os.startfile("wsa://" + wake_package)
                if not activated:
                    self.notify("正在唤醒 WSA，首次启动可能需要一点时间…")
                activated = True
                next_wake = time.monotonic() + 15
            time.sleep(min(.5, max(0, deadline-time.monotonic())))
        raise OperationError("WSA 已尝试启动，但连接未就绪。请打开“WSA 设置 → 高级设置”，确认开发人员模式开启，地址为 " + self.settings.endpoint + "。若 WSA 正在启动，稍等后重试。")

    def read_network_proxy(self, timeout=8) -> dict[str, str]:
        result = self.command("shell", "settings", "list", "global", timeout=timeout)
        if result.returncode or "SecurityException" in result.stdout + result.stderr:
            raise OperationError("无法读取安卓代理配置，请确认 WSA 已连接。")
        values = dict(line.partition("=")[::2] for line in result.stdout.splitlines() if "=" in line)
        return {key: values.get(key, "") for key in PROXY_KEYS}

    def configure_direct_network(self, timeout=8) -> str:
        """Persist no explicit Android proxy; host VPN/TUN routing is untouched."""
        deadline = time.monotonic() + timeout
        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise CommandTimeout(["shell", "settings", "global"], timeout)
            return value
        before = self.read_network_proxy(timeout=remaining())
        # Android persists these Global settings across WSA restarts. Do not
        # rewrite an already correct configuration or change Windows proxies.
        changes = [("put", "global", "http_proxy", ":0")] if before["http_proxy"] != ":0" else []
        changes += [("delete", "global", key) for key in PROXY_KEYS[1:]
                    if before[key] not in NO_PROXY_VALUES[key]]
        for args in changes:
            result = self.command("shell", "settings", *args, timeout=remaining())
            if result.returncode or re.search(r"SecurityException|Permission denial|Error", result.stdout + result.stderr):
                raise OperationError("安卓未接受直连设置，请检查连接权限。")
        after = self.read_network_proxy(timeout=remaining()) if changes else before
        if after["http_proxy"] != ":0" or any(after[key] not in NO_PROXY_VALUES[key] for key in PROXY_KEYS[1:]):
            raise OperationError("安卓代理仍有残留，直连设置未确认成功。")
        message = "WSA 显式代理已关闭，电脑代理设置保持原样。"
        self.notify(message)
        return message



    def list_apps(self) -> list[dict]:
        result = self.command("shell", "cmd", "package", "list", "packages", "-3", timeout=4)
        if result.returncode:
            raise OperationError("无法读取应用列表，请先连接 Android。")
        packages = sorted({line.partition(":")[2].strip() for line in result.stdout.splitlines()
                           if line.startswith("package:") and PACKAGE_RE.fullmatch(line.partition(":")[2].strip())})
        return [{"package": package, "name": self.settings.app_names.get(package, package)} for package in packages]

    def is_installed(self, package: str) -> bool:
        self.validate_package(package)
        result = self.command("shell", "pm", "path", package)
        return result.returncode == 0 and any(line.startswith("package:") for line in result.stdout.splitlines())

    @staticmethod
    def validate_package(package: str) -> None:
        if not PACKAGE_RE.fullmatch(package):
            raise OperationError("应用包名无效。")

    def install(self, filename: str | Path) -> ApkInfo:
        info = inspect_apk(filename)
        self.notify(f"准备安装 {info.name} · {info.version}")
        self.ensure_ready()
        self.notify(f"正在安装 {info.name}，请稍候…")
        result = self.command("install", "-r", str(info.path), timeout=240)
        output = result.stdout + result.stderr
        if result.returncode or not any(line.strip() == "Success" for line in output.splitlines()):
            raise OperationError(install_failure(output))
        if not self.is_installed(info.package):
            raise OperationError("安装返回成功，但未能确认应用已存在，请刷新应用列表。")
        self.settings.app_names[info.package] = info.name
        self.notify(f"{info.name} 安装完成")
        return info

    def launch(self, package: str | None = None) -> None:
        package = package or self.settings.favorite
        self.validate_package(package)
        activated = self.ensure_ready(package)
        if not self.is_installed(package):
            name = self.settings.app_names.get(package, package)
            raise OperationError(f"{name} 尚未安装。把 APK 拖进窗口，或点击“选择安装包”。")
        self.notify("正在打开 " + self.settings.app_names.get(package, package) + "…")
        # WSA is a packaged Windows application. URI activation reaches its
        # registered app entry; starting its binary directly can silently do nothing.
        if not activated:
            os.startfile("wsa://" + package)
        deadline = time.monotonic() + min(60, self.settings.startup_timeout)
        next_activation = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                windows = self.command("shell", "dumpsys", "window", "windows", timeout=min(3, max(.1, deadline-time.monotonic())))
                if windows.returncode == 0 and app_window_drawn(windows.stdout, package):
                    self.notify("应用界面已打开，请在独立窗口中使用")
                    return
                result = self.command("shell", "pidof", package, timeout=min(2, max(.1, deadline-time.monotonic())))
            except CommandTimeout:
                if self.reconnect(timeout=2, budget=max(.1, deadline-time.monotonic())) == "unauthorized":
                    raise OperationError("Android 连接需要授权，请在 WSA 授权窗口中选择允许。")
                result = None
            if result is not None and result.returncode and time.monotonic() >= next_activation:
                # Android can finish booting before Windows accepts app URI
                # activation. Retry only while no app process has appeared.
                os.startfile("wsa://" + package)
                next_activation = time.monotonic() + 12
            time.sleep(min(.5, max(0, deadline-time.monotonic())))
        raise OperationError("Android 启动流程已结束，但未确认应用窗口就绪。请查看应用是否停在启动画面或首次使用提示；已有窗口可继续使用。")

    def stop_app(self, package: str) -> None:
        self.validate_package(package)
        result = self.command("shell", "am", "force-stop", package)
        if result.returncode:
            raise OperationError("关闭应用失败，请检查 Android 连接。")
        self.notify("应用已关闭")

    def uninstall(self, package: str) -> None:
        self.validate_package(package)
        result = self.command("uninstall", package, timeout=90)
        if result.returncode or result.stdout.strip() != "Success":
            raise OperationError("卸载失败：" + (result.stdout + result.stderr)[-400:])
        self.notify("应用已卸载")

    def read_display_settings(self) -> dict:
        values = {}
        for key in ("font_scale", "screen_off_timeout"):
            result = self.command("shell", "settings", "get", "system", key)
            if result.returncode:
                raise OperationError("读取显示设置失败。")
            values[key] = result.stdout.strip()
        return values

    def apply_display_settings(self, font_scale: str, timeout: str) -> None:
        if font_scale not in {"0.85", "1.0", "1.15", "1.3"} or timeout not in SCREEN_TIMEOUTS:
            raise OperationError("显示设置的值无效。")
        self.ensure_ready()
        for key, value in [("font_scale", font_scale), ("screen_off_timeout", timeout)]:
            result = self.command("shell", "settings", "put", "system", key, value)
            check = self.command("shell", "settings", "get", "system", key)
            if result.returncode or check.returncode or float(check.stdout.strip()) != float(value):
                raise OperationError("部分设置未写入，请重新读取设置检查。")
        self.notify("Android 显示设置已生效")

    def shutdown(self) -> None:
        if self.state() == "device":
            self.notify("正在正常关闭 Android…")
            try:
                # Android's power service flushes application/system state.
                # ADB commonly exits 255 as the VM disconnects during shutdown.
                self.command("shell", "svc", "power", "shutdown", timeout=5)
            except CommandTimeout:
                pass
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if self.state(timeout=2) != "device":
                    self.notify("Android 已关闭")
                    return
                time.sleep(.25)
        self.notify("正在通过 WSA 设置关闭 Android…")
        open_wsa_settings()
        script = Path(__file__).resolve().parents[1] / "assets/wsa-shutdown.ps1"
        powershell = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        result = run_process([powershell, "-NoProfile", "-NonInteractive", "-STA", "-Command", script.read_text(encoding="utf-8")], timeout=30)
        if result.returncode:
            raise OperationError("未能操作 WSA 的关闭按钮，请在已打开的 WSA 设置中点击“系统 → 关闭”。")
        for _ in range(30):
            if self.state() != "device":
                self.notify("Android 已关闭")
                return
            time.sleep(1)
        raise OperationError("已点击 WSA 关闭按钮，但 Android 仍在运行，请查看 WSA 设置。")

    def restart(self) -> None:
        self.shutdown()
        self.ensure_ready()

    def open_system_settings(self) -> None:
        self.ensure_ready()
        open_android_settings()


def app_window_drawn(output: str, package: str) -> bool:
    # A live PID or Android's generic splash window alone is not a ready app.
    for section in re.split(r"(?m)^  Window #", output):
        header = section.partition("\n")[0]
        if " u0 " + package + "/" in header and "mHasSurface=true" in section and "mDrawState=HAS_DRAWN" in section and "isVisible=false" not in section:
            return True
    return False


def open_wsa_settings() -> None:
    os.startfile("wsa-settings:")


def open_android_settings() -> None:
    os.startfile("wsa://com.android.settings")
