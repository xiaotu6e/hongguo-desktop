# 更新操作说明

面向用户的入口是 [Agent 更新提示词](agent-prompts.md#更新提示词)。以下是 Agent 执行更新时的检查顺序。

## 先说明需要更新哪些内容

1. 获取仓库当前说明，读取根目录 `CHANGELOG.md`、`manifests/updates.json` 和 `manifests/release.json`。更新记录包含公开版本的主要功能，成品清单标明当前已发布的助手版本。
2. 读取 `%LOCALAPPDATA%\HongguoDesktopHelper\installation.json`，核对实际快捷方式目标、进程路径和 EXE 版本。旧的直接解压 / 本机构建安装可能没有这个记录；源码安装要核对实际入口目录的 Git 提交和修改状态。
3. 运行只读计划命令，输出当前版本至目标版本之间的累积组件变化：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\update-plan.ps1 -Json
```

没有安装记录时，先核实版本，再传入。例如：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\update-plan.ps1 -CurrentVersion v0.1.0 -Json
```

脚本不下载、不安装、不连接安卓、不修改设置。无法判断当前版本时会明确返回 `unknown`，不能声称用户已是最新版。

4. 向用户说明当前版本、目标版本、功能变化、需要更新和无需更新的组件，再继续已授权的更新。

## 更新成品安装

从助手左侧“退出工具”正常退出；右上角 × 只会最小化。只关闭已确认的本项目助手进程，不关闭其他安卓工具。

在获取了当前清单的仓库目录中，按清单指定版本安装。例如当前成品：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Mode Release -Version v0.1.8 -Launch
```

安装器会验证 ZIP 的 SHA-256，部署到新的版本目录，记录安装信息，并创建指向新版本的桌面 / 开始菜单快捷方式。已有 WSA 和 APP 保留；普通助手更新不添加 `-InstallWsa`、`-RepairWsa` 或 `-ApkPath`。

如果旧用户使用了自定义快捷方式或手动解压目录，先备份其快捷方式目标和旧程序目录，再将原快捷方式改为新目录的 `helper-guardian.exe` 与工作目录，图标使用 `hongguo_desktop.exe`。保留旧版本作为回退，不混用新旧目录，不只替换 EXE。

## 更新源码安装

检查 `git status`；干净仓库可正常快进到当前提交。目录存在用户修改时，使用新的独立目录，不强制 `reset` 或覆盖修改。正常退出旧助手，再运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install.ps1 -Mode Source -Launch
```

该流程准备与源码对应的固定依赖，并更新源码快捷方式。Java / C++ 预编译组件随源码提供；只在更新清单要求时重新构建。

## 更新后核对与结果报告

核对主程序实际路径和版本，以及原快捷方式的守护入口，再运行 `scripts/doctor.ps1 -Json`。检查播放、选集、横竖屏、窗口缩放与后台联动；关闭助手窗口会最小化，选择“退出工具”会同时关闭红果播放窗口。

保留现有设置；用户希望手动控制播放时，先备份设置，仅关闭 `auto_fullscreen`，保留其他键值。结果报告简要说明已运行的版本、更新组件和仍需用户完成的系统步骤。

## 每次发布如何维护

程序变更写入新的版本条目：日期、主要功能、需要更新的组件和数据保留方式；同步 `manifests/updates.json`。生成并上传新的成品后，更新 `manifests/release.json`；已发布的标签和 ZIP 保持不变。

只有文档或 Agent 操作脚本变更时，在更新记录中单独写日期和内容，说明成品版本不变、现有用户是否需要重装。不要为文档修改声称播放器已有一个未构建的新版本。

公开说明按最终功能整理。未发布的中间版本、已解决的小问题和调试过程留在本地，不逐项记录或跨版本重复列出。
