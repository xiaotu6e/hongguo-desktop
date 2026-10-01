# 第三方组件与来源

本项目自有 Python、Java 和 C++ 源码使用仓库根目录 MIT 许可证。该许可证不替代下列第三方组件自己的条款。

| 组件 | 用途 | 来源与说明 |
| --- | --- | --- |
| Flet / Flutter | 中文桌面界面 | https://flet.dev/ / https://flutter.dev/ ，依照各自许可证 |
| flet-dropzone | APK 拖拽 | https://pypi.org/project/flet-dropzone/ ，固定版本见 requirements.txt |
| apkutils | APK 元信息读取 | https://pypi.org/project/apkutils/ ，固定版本见 requirements.txt |
| Android Platform Tools | ADB 连接 | https://developer.android.com/tools/releases/platform-tools ，从 Google 下载固定版本；其 NOTICE 随成品保留 |
| R8 | Java → Android DEX | https://r8.googlesource.com/r8/ ，构建时使用 Google Maven 固定版本与哈希 |
| WSABuilds | WSA 社区发行包 | https://github.com/MustardChef/WSABuilds ，原始压缩包只从上游获取，不放进源码仓库 |
| AutoBridge template | 兼容修复输入 | https://github.com/Sayuki2123/AutoBridge ，固定提交 cedb66c1dc359fe89100a099cd439fc45bd2e153；上游 MIT 许可证副本见 AUTOBRIDGE-LICENSE.txt |
| 7-Zip standalone | 解压 WSA 的 7z 文件 | https://www.7-zip.org/ ，下载文件锁定 SHA-256；未来上游文件变化时须维护清单 |

AutoBridge 的源代码和 template 内预编译组件应依照上游分别提供的说明使用。本项目只在用户机器下载处理，不在公开仓库或助手 ZIP 中附带该模板与 WSA 修复镜像。

红果 APK、账号和播放内容均由使用者自行获取和使用。项目不发布 APK、不收集账号、不提供节目内容下载。APK 自己的服务和网络请求由 APK 处理。

打包助手中包含 Python、Flet/Flutter 和 ADB 等运行组件，保留原 NOTICE/LICENSE 文件。根目录 MIT 许可证仅覆盖本项目自有代码。
