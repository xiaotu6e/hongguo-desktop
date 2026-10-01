# WSA 兼容修复与恢复

## 验证过的组合

原开发机使用 Windows 11 x64、WSABuilds 2407.40000.4.0 LTS8 NoGApps NoAmazon（Android 13）以及红果 `com.phoenix.read` 7.3.9.32 ARM64。其他 Windows、WSA、APK 或处理器组合没有得到同等验证。

上游来源：

- https://github.com/MustardChef/WSABuilds/releases/tag/Windows_11_2407.40000.4.0_LTS_8
- https://github.com/Sayuki2123/AutoBridge

原始与已修复镜像哈希保存在 `manifests/wsa-compatibility.json`。新机器只在原始 system/vendor 两个哈希均匹配时执行修复。

## 修复内容

1. 从固定提交的 AutoBridge template 获取 ARM64 NDK Translation 组件，替换原 ARM64 转译路径；ARM32 适配入口保留原 Houdini 委托方式。
2. 调整 native bridge 属性为 `libndk_translation.so`。
3. 从 WSA 的视频能力配置移除 `OMX.android.latte.hevc.decoder` 声明，让已验证的红果版本采用应用内解码路径。

第三项影响整个 WSA 的系统 HEVC 解码能力，可能增加 CPU 使用。音频配置保持原状；其他 APK 的兼容性需单独验证。该修复不修改 APK，不替换 userdata/metadata 镜像，不清空应用数据。

## 准备 WSL

修复工具需要 WSL Ubuntu 22.04/24.04 或具有 Python 3.9+ 的 Ubuntu 环境，及 qemu-img、e2fsck、resize2fs、mount、umount。Windows 助手源码仍需 Python 3.11+。若没有 WSL，在管理员 PowerShell 中执行：

```powershell
wsl --install -d Ubuntu
```

按系统提示完成首次设置和必要重启。已有其他默认发行版时，给修复脚本传 `-Distribution '发行版名称'`。

安装镜像处理工具：

```powershell
wsl -d Ubuntu -u root -- apt-get update
wsl -d Ubuntu -u root -- apt-get install -y python3 qemu-utils e2fsprogs util-linux
```

准备约 20 GB 额外磁盘空间，实际用量随镜像变化。镜像处理在 WSL 内以 root 执行，Windows 安装目录必须可写。

## 应用修复

先从 WSA 设置彻底关闭安卓，等待镜像文件释放；停止助手中的播放操作。预览：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\repair-wsa.ps1 -DryRun
```

只生成候选、不部署：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\repair-wsa.ps1 -PrepareOnly
```

完整生成和部署：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\repair-wsa.ps1
```

自动检测已注册安装目录，备份原始 system/vendor，检查镜像版本和文件锁，离线处理候选，运行 e2fsck 并生成候选哈希。部署前重新确认原始文件未变化，分别复制并校验候选后替换；替换阶段异常时尝试恢复被替换的原文件。若进程被强制终止，保留的 `.previous-*` 文件与原始备份用于手动恢复。

镜像构建时序和 VHDX 容器元数据可能导致新候选哈希与原开发机不同；已生成候选的实际哈希记录在本机 `deployed.json`。修复脚本接受该本机记录作为重跑依据。

## 恢复

备份保存在 `%LOCALAPPDATA%\HongguoDesktopHelper\wsa-backup\2407.40000.4.0-lts8`。再次彻底关闭 WSA 后执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\repair-wsa.ps1 -Restore
```

恢复经过原始哈希校验的 system/vendor，保留应用数据。缺少原始备份、未知当前镜像或文件锁定时会拒绝恢复。原开发机早先手工修复的备份不在新脚本的目录内；脚本不会凭空生成或冒用该备份。

## 验证限制

当前泛化脚本以原开发机的两套修复逻辑为依据；本次已用原始恢复备份完成离线候选镜像生成与文件系统检查。开源整理期间没有再次覆盖正在使用的 WSA，也没有完成第二台全新电脑上的端到端验证。请按 docs/validation.md 区分离线检查、原开发机实测和新机器待验证项目。
