# 开发、构建与发布

## 源码运行

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\prepare-source.ps1
.\.venv\Scripts\python.exe .\src\main.py
```

源码主入口为 `src/main.py`，资产位于 `src/assets`。页面读取 Java 组件和窗口 C++ 组件的预编译成品随源码提供，也可以重新构建。

安装脚本使用 requirements.lock.txt 锁定本次 Windows Python 3.11 环境的完整 Python 依赖版本；requirements.txt 列出直接依赖。升级依赖后重新验证并生成锁定文件。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Java 离线测试读取 `JAVA_HOME` 或 PATH 中的 javac。未安装 JDK 时会跳过 Java 测试；完整验证应安装 JDK 21。测试 fixtures 已将剧名和非必要内容替换为示例文本。

## 构建桌面成品

准备 JDK 21、Visual Studio 2022 C++ 桌面构建工具及 Windows SDK。将 JDK 路径放入 `JAVA_HOME`，Flutter SDK 可放入 PATH；Flet 构建器也支持获取其所需 SDK。

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1
```

依次准备环境、运行单元测试、编译 C++/Java 组件、执行 Flet Windows 构建，最后生成 `dist` 下的 ZIP、SHA256SUMS.txt 和 release.json。

仅测试和准备、不编译成品：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\build.ps1 -PrepareOnly
```

为已有成品制作发布包：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\package-release.ps1 -AppDirectory 'C:\path\to\app' -Version v0.1.2
```

打包 ZIP 时间戳会影响哈希，因此每次打包后的 `manifests/release.json` 必须与上传的 ZIP 对应。不要用一次构建的清单校验另一次构建的 ZIP。

## GitHub 发布

公开源码前只提交当前仓库文件；`.venv`、app、build、dist、APK、VHDX、个人配置和观看记录均被忽略。

安装 GitHub CLI 并登录后，首次新建公开仓库：

```powershell
git init -b main
git add .
git commit -m "Prepare open-source desktop helper and installer"
gh auth login
gh repo create hongguo-desktop --public --source . --remote origin --push
```

若目标仓库已存在，添加其 remote 并按实际历史合并后推送，避免强制覆盖已有提交。

发布成品前提交当前 `manifests/release.json`，再创建对应标签并上传：

```powershell
git add manifests/release.json
git commit -m "Record v0.1.2 release checksum"
git tag v0.1.2
git push origin main v0.1.2
gh release create v0.1.2 .\dist\hongguo-desktop-v0.1.2-windows-x64.zip .\dist\SHA256SUMS.txt .\dist\release.json --title "红果桌面助手 v0.1.2" --notes-file .\docs\release-notes.md
```

附带 GitHub Actions CI，运行离线测试和 PowerShell 解析检查。发布成品先人工核对版本、实际播放与第三方 NOTICE。更新发行版时同时更新对应校验清单和兼容性文档。

## 更新记录与组件提示

每次修改同步 `CHANGELOG.md`，让用户看懂具体行为变化和需要更新的地方。程序版本更新同时维护 `manifests/updates.json`，记录助手、设置、WSA、APK、Java / C++ 组件及依赖是否需要处理；`scripts/update-plan.ps1` 会对当前版本到目标版本的变化生成只读计划供 Agent 报告。

文档或 Agent 脚本修改单独记录日期，不更改未重新构建的助手成品版本。已发布标签、ZIP 和校验值不重写；新的程序版本使用新标签、新成品与匹配清单。执行流程见 [更新操作说明](updating.md)。
