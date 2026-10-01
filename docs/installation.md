# 安装与分享

## 使用前准备

运行平台为 Windows 11 x64。硬件虚拟化需在 BIOS/UEFI 中开启；WSA 需要 Windows 的 VirtualMachinePlatform 功能和 NTFS 安装磁盘。源码方式需要 Python 3.11+；安装 Git 是使用 git clone 的前提，也可以通过 GitHub 的 Code → Download ZIP 下载源码并解压。

安装 Python 时勾选添加到 PATH，或给安装脚本传入 `-PythonPath 'C:\路径\python.exe'`。Python 官方下载页：https://www.python.org/downloads/windows/ 。成品 Release 可省去 Python 安装。

WSA 官方商店渠道已下架。本项目固定使用 `manifests/dependencies.json` 中 WSABuilds 2407.40000.4.0 LTS8 x64 NoGApps NoAmazon 的压缩包，安装时从上游下载；本仓库不发布 WSA 镜像。

## 已有 WSA

在仓库目录执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Launch
```

脚本创建独立 `.venv`、安装固定 Python 依赖、下载并校验 Android Platform Tools，创建桌面和开始菜单快捷方式。保留仓库目录，源码快捷方式依赖该目录。

在 WSA 设置中开启开发人员模式，唤醒安卓，首次连接时允许 ADB 授权。默认地址 `127.0.0.1:58526`，工具使用独立 ADB 服务端口 `5038`。

## 新电脑

在管理员 PowerShell 中运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -InstallWsa -EnableVirtualization -Launch
```

若脚本开启了 VirtualMachinePlatform，先重启 Windows，再运行同一命令。脚本不强制重启。

WSA 压缩包和 7-Zip 独立解压组件均校验 SHA-256。安装流程调用该已校验压缩包自带的 Install.ps1，处理 Appx 依赖与注册；它可能显示权限提示和 WSA 设置窗口。已注册的 WSA 会保留，不会自动卸载或替换。

WSA 文件保存在 `%LOCALAPPDATA%\HongguoDesktopHelper\wsa`；必须保留此目录。

## 复现红果兼容修复

先按 [兼容文档](compatibility.md) 准备 WSL Ubuntu 和 Linux 工具，并通过 WSA 设置彻底关闭安卓。然后执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -InstallWsa -RepairWsa -Launch
```

修复只支持记录的原始镜像。已完成该修复时会跳过，未知版本则停止并说明原因。启动后仍需确认实际播放。

## APK 安装

项目不附带红果 APK。请提供你有权使用、适配当前安卓环境的本地安装包。已唤醒并授权 WSA 后可执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -ApkPath 'C:\Downloads\your-app.apk' -Launch
```

或者将 APK 拖入助手安装区。更新使用 `adb install -r`，保留现有数据；签名不兼容或版本降级时会失败，不清空原应用。

## 成品方式

对应 Release 发布后，使用匹配版本的源码标签及 `manifests/release.json`：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Mode Release -Version v0.1.0 -Launch
```

从默认仓库下载对应成品并校验；可用 `-Repository 'owner/repo'` 指定镜像仓库。预先下载的成品可传 `-BundlePath 'C:\Downloads\hongguo-desktop-v0.1.0-windows-x64.zip'`。

成品按版本放入 `%LOCALAPPDATA%\HongguoDesktopHelper\program`，不覆盖观看记录。已有进程应先从助手的“退出工具”关闭，再启动新版本。

## 检查与恢复执行

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\doctor.ps1 -Json
```

- 退出码 0：静态/连接检查通过，人工播放仍待确认。
- 退出码 2：组件或 APK 缺失，按报告补齐。
- 退出码 3：WSA 未唤醒或连接未授权，处理后再次检查。

安装可以重跑：保留已注册 WSA、已完成的源码环境和已验证缓存，兼容修复依照镜像哈希跳过已完成步骤。下载哈希错误时只删除提示的缓存文件并重试。WSA 解压或成品部署中断产生的临时目录会保留用于诊断；选择新的安装目录，或确认目录确为该失败安装的临时产物后再清理。

`-Offline` 禁止已知下载的网络获取；所需压缩包必须放在 `dist/cache`，源码 Python 依赖也需预先安装。它不表示自动准备离线依赖。

## 如何分享

分享仓库链接供查看代码；分享 Releases 页面供下载成品；将 README 中的 Agent 安装指令一起复制给使用者。安装不要求 GitHub 写入权限。

Agent 必须在使用者的 Windows 本机有终端执行能力。云端代码审查 Agent 无法替该电脑安装 WSA。系统授权、账号登录和真实播放确认仍由使用者完成。
