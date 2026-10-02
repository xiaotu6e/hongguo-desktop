# Agent 操作说明

## 工作范围

这是 Windows 11 x64 上的 WSA 工具，用独立无边框小窗边看短剧边办公。操作应在最终使用者的 Windows 本机进行。先读 README.md、docs/agent-prompts.md 和 docs/installation.md；修改 WSA 前读 docs/compatibility.md。安装请求应实际执行，并报告结果。

## 安装

1. 读取 `manifests/release.json`，优先选择 Release 成品模式，省去 Python / 编译环境；用户明确选择源码时才使用 Source。先运行 `scripts/install.ps1 -Mode Release -Version <清单版本> -DryRun`。
2. 已有 WSA：按清单版本运行 `scripts/install.ps1 -Mode Release -Version <清单版本> -Launch`。以上脚本通过 `powershell -NoProfile -ExecutionPolicy Bypass -File` 执行；不要把预览当成实际安装。源码模式需要 Python 3.11+。
3. 缺少 WSA：在管理员 PowerShell 中添加 `-InstallWsa`。虚拟化功能未开启时使用 `-EnableVirtualization`，按脚本提示重启后重跑。
4. 用户需要完整兼容复现，或指定版本的红果无法启动/切集解码异常时，按兼容文档准备 WSL 工具，彻底关闭 WSA，再使用 `-RepairWsa`。
5. APK 使用用户提供的本地文件：连接和授权完成后运行安装命令并添加 `-ApkPath '完整路径.apk'`，或者让用户拖入助手。

下载只使用 manifests/dependencies.json 的 HTTPS 地址和 SHA-256。不要把哈希校验失败解释为成功，也不要自动替换成浮动的 latest 下载。

## 更新

先读 `CHANGELOG.md`、`docs/updating.md`、`manifests/updates.json` 和成品清单。获取当前安装记录后，核对真正的快捷方式目标、运行路径和 EXE 版本 / 源码提交，再运行 `scripts/update-plan.ps1 -Json`。没有记录的旧安装先核实版本，再传 `-CurrentVersion`；不要猜成最新版。

更新前向用户明确提示：当前版本 → 目标版本、具体功能变化、哪些组件需要更新、哪些组件无需更新、保留什么数据。随后完成已要求的普通更新，不再另行要求重复确认。

正常退出旧助手，按匹配清单安装完整新版本并更新原有快捷方式；不只替换单个 EXE。源码目录有用户修改时使用独立目录，不强制 reset。只拉取仓库不能证明旧成品已更新。

v0.1.0 → v0.1.1 只更新助手，检查旧的自动全屏偏好；WSA、APK、Java / C++ 组件和依赖版本无需升级。旧设置会保留，用户希望手动控制时备份设置，仅关闭 `auto_fullscreen`，保留其他键值。仅文档 / Agent 脚本更新、成品版本不变时，不重复重装助手或安卓环境。

v0.1.1 → v0.1.2 更新完整助手及其中的 C++ 窗口组件，以获得四边 / 四角等比例缩放和本次窗口尺寸记忆。WSA、APK、Java 与依赖无需升级；保留助手设置和应用数据。更新后在横屏 / 竖屏各拖动一条边和一个角，核对 16:9 / 9:16 比例及尺寸保持。

## 每次修改的更新记录

每次提交都维护根目录 `CHANGELOG.md`，说明修改内容与用户需要更新的地方。程序版本变更同时维护 `manifests/updates.json` 的组件清单；成品清单仅在对应成品实际构建并发布后更新。文档 / Agent 脚本修改写明日期和成品版本不变，不改写已发布标签和 ZIP。

## 系统提示与数据

用户已经要求安装时，直接完成该授权范围内的操作。管理员提升、BIOS 虚拟化、重启、WSA 开发人员模式、ADB 授权和账号登录需要相应系统/用户步骤时，准确说明当前缺少什么，再继续可独立完成的工作。

保留现有应用数据和 `%LOCALAPPDATA%/HongguoDesktopHelper` 的观看记录。兼容修复仅接受记录的原始镜像；不覆盖未知 WSA，不卸载已有 WSA，也不自动清除 APK 数据。需要恢复时使用 `scripts/repair-wsa.ps1 -Restore` 和经过校验的原始备份。

## 验证与结果报告

运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/doctor.ps1 -Json`。如 WSA 尚未唤醒或需要授权，处理后再次检查。报告安装模式、版本、检查结果与人工步骤。助手窗口、ADB 连接和 APK 安装成功不能证明真实播放通过；要求确认画面、声音、横竖屏与切集。

开发修改使用 `.venv/Scripts/python.exe -m unittest discover -s tests -v`；PowerShell 脚本同时需要在 Windows PowerShell 5.1 下检查。测试不连接真实 WSA，不修改系统镜像或个人配置。日志与排查证据保存在本地，不上传原始个人数据。
