# 验证记录

开源整理日期：2026-10-01。

首次公开推送的 [GitHub CI](https://github.com/xiaotu6e/hongguo-desktop/actions/runs/36885180598) 已通过，包含依赖安装、离线测试和 PowerShell 解析检查。[v0.1.0](https://github.com/xiaotu6e/hongguo-desktop/releases/tag/v0.1.0) 的源码包、成品包和校验文件已发布；GitHub 返回的文件摘要与本地 SHA-256 一致。

## 本次已经执行

- 在独立项目目录新建 Python 3.11 虚拟环境，安装助手依赖与 Flet Desktop 1.0.1。
- 153 项离线单元/安装边界测试全部通过，包含使用 JDK 21 的 Java 页面根节点测试。
- 在 Windows PowerShell 5.1 中解析全部 PowerShell 脚本；无语法错误。
- Java 桥和 C++ 窗口组件重新编译成功。
- 安装入口的 DryRun 成功；WSA 修复 DryRun 能自动检测实际注册目录。
- 校验并下载固定版本 ADB、7-Zip 独立组件和 AutoBridge 源码；锁定 R8 与 WSA 原始包 SHA-256。
- 固定提交 AutoBridge template 内 90 个文件与原验证时的模板内容一致。
- 在 WSL Ubuntu / Python 3.12 中，使用原始 system/vendor 的只读恢复备份运行新的候选镜像准备脚本。NDK 和视频配置处理、卸载、e2fsck 与 VHDX 生成均完成，候选哈希见下表。
- 源码和成品两种模式在独立安装/数据目录完成安装记录写入及静态 doctor 检查；成品 ZIP 完成校验与解压。重复安装同一包成功，未启动应用或创建实际桌面快捷方式。
- 离线测试拒绝错误缓存哈希、ZIP 路径逃逸和未知原始镜像，并验证 HEVC 修改保留其他解码/编码配置。

| 本次离线候选 | SHA-256 |
| --- | --- |
| system.vhdx | b3cadd2dcb707afd5dc3e9e2bcdfc3f5ec5981c97ae32b6a6fcbd4bc429ca99c |
| vendor.vhdx | 692724cd615b0c7868caa25e6ef4167bf61b7508c9088eb3b9dcbf051ff6c88e |

候选和恢复备份留在本机，被 Git 忽略，不随源码或助手成品发布。新的候选没有部署到正在使用的 WSA。

## 原开发机此前已有的验证

当前助手的原开发记录描述了红果 7.3.9.32 在 WSA 2407.40000.4.0 LTS8 的启动、切集、720P/1080P、横竖屏和窗口操作验证。原始日志、截图与观看数据未迁入公开仓库。该记录不能代替新机器验收。

本次成品以原开发机已有 Flet 桌面应用为基础，替换了本次重新编译的 Java/C++ 组件并保留第三方 NOTICE。没有重新执行完整 Flutter/Flet 应用构建；可复现的构建入口为 scripts/build.ps1。

## 尚未验证

- 第二台全新 Windows 机器的完整安装，包括系统功能、Appx 依赖、WSL 初次设置和 ADB 授权。
- 新泛化脚本在真实注册 WSA 上的部署、故障回退与恢复操作。
- 本次候选部署后的真实画面、声音、长时间播放、其他 APK 与 ARM32 应用。
- Release 在另一台使用者电脑上的完整下载、安装与实际播放验收。

安装或静态检查成功不代表上述项目通过。每次修改 Windows/WSA/APK 版本后重新执行对应验收。
