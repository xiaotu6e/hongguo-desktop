# 红果桌面助手 · Hongguo Desktop Helper

**红果短剧 Windows 11 桌面播放助手：通过 WSA（类似安卓模拟器）运行安卓 APP 安装包（APK），支持无标题栏、无边框播放及 APP 提供的 1080P 高清，工具不额外转码或压缩音视频。**

简单理解：把红果短剧安卓 APP 装到 Windows 11 电脑上，直接在电脑里播放。WSA 是让安卓 APP 在 Windows 上运行的环境，使用方式类似模拟器；本助手为红果增加隐藏窗口外框、自动横竖屏、滚轮切集和观看进度保护，让播放窗口更简洁，也更适合鼠标操作。

清晰度取决于 APP 和片源提供的档位。声音由 APP 和 WSA 播放，助手不额外压缩音频；目前没有验证整条播放链路是否达到“音频无损”。助手支持安装其他 APK，但上述播放辅助目前针对红果适配。APK 由用户自行提供。

![助手界面](docs/images/overview.png)

## 给本机 Agent 的安装指令

把仓库链接和下面这段话发给能在 Windows 本机执行终端命令的 Agent：

> 请安装这个项目。先阅读 README.md、AGENTS.md 和 docs/installation.md，检查当前 Windows、Python、WSA 与开发人员模式。执行 scripts/install.ps1 安装助手，再运行 scripts/doctor.ps1。WSA 未安装时使用 -InstallWsa；确需复现文档中的启动/解码修复时使用 -RepairWsa。保留已有应用和观看数据。需要管理员、重启或安卓授权时，说明具体步骤；完成后启动助手，报告检查结果与仍需人工确认的播放项目。

## 快速安装

支持范围：**Windows 11 x64**。源码运行需要 Python 3.11+。微软商店自 2025-03-05 起不再提供 WSA；本项目记录并使用 WSABuilds 的指定社区发行版本。来源见 [第三方组件](docs/third-party.md)。

已安装 WSA，且 Python 可从终端运行：

```powershell
git clone https://github.com/xiaotu6e/hongguo-desktop.git
Set-Location hongguo-desktop
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Launch
```

新电脑完整安装命令（管理员 PowerShell；Python 和 WSL 准备见安装说明）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -InstallWsa -RepairWsa -EnableVirtualization -Launch
```

兼容修复需要 WSL Ubuntu、Linux Python 3.9+ 和镜像工具。首次启用虚拟化需要重启后再次运行相同命令。WSA 开发人员模式与首次 ADB 授权需要在系统界面完成。APK 由用户提供，仓库不包含红果 APK。

先预览安装步骤，不下载、不修改系统、不启动应用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -DryRun -InstallWsa -RepairWsa -Launch
```

已发布 [v0.1.0 成品和源码包](https://github.com/xiaotu6e/hongguo-desktop/releases/tag/v0.1.0)，可跳过 Python 环境安装：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Mode Release -Version v0.1.0 -Launch
```

成品下载和源码入口分别在仓库的 Releases 和 Code 页面；安装脚本依照 `manifests/release.json` 校验成品。已有 WSA/APK 的用户也可将成品 ZIP 解压到固定目录后，双击 hongguo_desktop.exe。

## 安装后检查

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\doctor.ps1
```

检查助手、WSA、ADB、连接和 APK。退出码 0 表示这些检查通过，2 表示缺失组件，3 表示连接需处理；**真实画面、声音、切集和横竖屏仍需确认**。

## 文档

本仓库公开 Python/Flet 源码、Java 页面读取组件、C++ 窗口组件，以及安装、环境检查、WSA 兼容修复和恢复脚本。当前安卓运行环境只支持 WSA。

- [安装、Agent 操作及断点恢复](docs/installation.md)
- [使用方法](docs/usage.md)
- [WSA 兼容修复、版本限制及恢复](docs/compatibility.md)
- [开发、构建和发布](docs/development.md)
- [验证范围](docs/validation.md)
- [第三方来源与许可证](docs/third-party.md)

源码采用 MIT 许可证。个人观看记录、配置、日志、系统镜像和 APK 不提交到本仓库。该项目为独立工具，与红果或微软没有官方关联。
