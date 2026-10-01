# Agent 操作说明

## 工作范围

这是 Windows 11 x64 上的 WSA 工具。操作应在最终使用者的 Windows 本机进行。先读 README.md 和 docs/installation.md；修改 WSA 前读 docs/compatibility.md。

## 安装

1. 先运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install.ps1 -DryRun`。
2. 已有 WSA：运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/install.ps1 -Launch`。源码模式需要 Python 3.11+；成品发布后可以使用 `-Mode Release`。
3. 缺少 WSA：在管理员 PowerShell 中添加 `-InstallWsa`。虚拟化功能未开启时使用 `-EnableVirtualization`，按脚本提示重启后重跑。
4. 用户需要完整兼容复现，或指定版本的红果无法启动/切集解码异常时，按兼容文档准备 WSL 工具，彻底关闭 WSA，再使用 `-RepairWsa`。
5. APK 使用用户提供的本地文件：连接和授权完成后运行安装命令并添加 `-ApkPath '完整路径.apk'`，或者让用户拖入助手。

下载只使用 manifests/dependencies.json 的 HTTPS 地址和 SHA-256。不要把哈希校验失败解释为成功，也不要自动替换成浮动的 latest 下载。

## 系统提示与数据

用户已经要求安装时，直接完成该授权范围内的操作。管理员提升、BIOS 虚拟化、重启、WSA 开发人员模式、ADB 授权和账号登录需要相应系统/用户步骤时，准确说明当前缺少什么，再继续可独立完成的工作。

保留现有应用数据和 `%LOCALAPPDATA%/HongguoDesktopHelper` 的观看记录。兼容修复仅接受记录的原始镜像；不覆盖未知 WSA，不卸载已有 WSA，也不自动清除 APK 数据。需要恢复时使用 `scripts/repair-wsa.ps1 -Restore` 和经过校验的原始备份。

## 验证与结果报告

运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/doctor.ps1 -Json`。如 WSA 尚未唤醒或需要授权，处理后再次检查。报告安装模式、版本、检查结果与人工步骤。助手窗口、ADB 连接和 APK 安装成功不能证明真实播放通过；要求确认画面、声音、横竖屏与切集。

开发修改使用 `.venv/Scripts/python.exe -m unittest discover -s tests -v`；PowerShell 脚本同时需要在 Windows PowerShell 5.1 下检查。测试不连接真实 WSA，不修改系统镜像或个人配置。日志与排查证据保存在本地，不上传原始个人数据。
