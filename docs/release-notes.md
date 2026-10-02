# 红果桌面助手 v0.1.1

**在 Windows 11 上安装红果短剧安卓 APP（APK），通过 WSA 安卓子系统直接播放 APP 的原画质、原声音，支持 1080P 高清、无标题栏无边框、自动横竖屏和鼠标滚轮切集。**

安装方式类似安卓模拟器，使用体验像 Windows 桌面播放器：APP 在独立无边框小窗中直接播放，方便边看短剧边办公。助手不额外压缩或转码音视频，提供自动横竖屏、鼠标滚轮切集和观看进度保护。

[查看推荐页、横屏及竖屏的实际播放截图](https://github.com/xiaotu6e/hongguo-desktop#实际播放效果)。

[查看播放小窗与 Python 管理工具同屏实拍](https://github.com/xiaotu6e/hongguo-desktop#小窗播放边看边办公)。APP 的全屏按钮在小窗内展开视频，默认不会铺满电脑屏幕。

- 默认手动进入全屏，可选的自动全屏仅在每部剧首次进入时触发一次。
- 修复退出全屏、返回推荐/选集、重新连接后再次自动进入同一部剧全屏的问题。
- 修改清晰度或在播放中开启自动全屏，不再强制把当前剧重新切入全屏。
- 保留手动全屏按钮、自动横竖屏、1080P 偏好、滚轮切集与观看进度保护。
- 增加 11 项全屏导航回归测试。

**v0.1.0 用户只需更新完整助手并检查旧的自动全屏偏好，WSA 和红果 APP 无需重装；账号、观看记录与其他设置保留。**具体组件变更见 [逐版本更新记录](https://github.com/xiaotu6e/hongguo-desktop/blob/main/CHANGELOG.md)。

安装与更新推荐交给本机 Agent，直接复制 [通用提示词](https://github.com/xiaotu6e/hongguo-desktop/blob/main/docs/agent-prompts.md) 即可。当前助手成品为 v0.1.1；文档与 Agent 操作说明补充不要求已更新用户重装。

适用范围为 Windows 11 x64。源码运行需要 Python 3.11+；兼容修复需要 WSL 与 Linux 镜像工具。WSA/APK 不包含在助手 ZIP 中，来源和准备步骤见仓库文档。

安装步骤、兼容范围和检查记录见[仓库文档](https://github.com/xiaotu6e/hongguo-desktop#文档)。
