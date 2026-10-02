# 红果桌面助手 v0.1.2 · 无边框等比例缩放

在 Windows 11 上通过 Agent 安装红果安卓 APP，以独立无边框桌面小窗看短剧，边看边办公。画面与声音由 APP 直接播放，助手不额外转码或压缩。

- 拖动四条边或四个角即可放大 / 缩小窗口；横屏始终保持 16:9，竖屏保持 9:16。
- 顶部中间仍用于移动窗口，播放画面内的按钮保持可操作。
- 同一播放窗口分别记住横屏 / 竖屏手动调整的尺寸，方向切回或助手重新连接后沿用；关闭播放窗口后，新窗口从默认尺寸开始。
- 最小尺寸与所在屏幕边界保护，支持不同 DPI 与负坐标副屏。
- 保留 v0.1.1 的手动全屏和退出后不重复自动进入的修复。
- 用真实桌面壁纸重拍小窗与管理工具同屏图；拍摄时临时隐藏图标及其他窗口，截图后恢复。

**v0.1.0 / v0.1.1 用户需要安装完整 v0.1.2 助手目录，新 C++ 窗口组件已随附。WSA、红果 APP、Java 组件与 Python 依赖无需重装；账号、观看记录与助手设置保留。**

[逐版本更新与组件说明](https://github.com/xiaotu6e/hongguo-desktop/blob/main/CHANGELOG.md) · [复制给 Agent 的安装 / 更新提示词](https://github.com/xiaotu6e/hongguo-desktop/blob/main/docs/agent-prompts.md) · [真实桌面小窗截图](https://github.com/xiaotu6e/hongguo-desktop#小窗播放边看边办公)

适用于 Windows 11 x64。Agent 使用发布清单校验并安装成品，无需用户准备 Python 或编译环境。WSA / APK 不包含在助手 ZIP 中，准备流程由仓库安装指引提供。
