## Why

ektro-pen 当前 100% Windows-only (基于 weasel/librime fork + Windows TSF)。但项目的 sync 守护层 (Python + 4 份服务端对接规范) 在 v0.4 已经做成跨平台 stdlib + macOS Keychain 集成,**只差 macOS IME 主体**就能完整服务 Mac 用户。

macOS 没有"零成本可改造"的开源 IME 让 ektro-pen 直接接入。重新写一个完整 macOS IMK (Input Method Kit) 项目需要数月。**唯一务实路径是 fork 鼠须管 (Squirrel)** — macOS 上最成熟的 librime 客户端,GPL-3 开源,已经解决了 IMK 集成 / 候选窗 / 词库 / 签名分发 等所有问题。

ektro-pen-macos = fork Squirrel + 接入 EktroMemoryStore + 唤醒 UploaderDaemon 同步。三戒不变,只换平台。

## What Changes

- 新仓库 `ektro-pen-macos` (独立 GitHub repo,fork 自 [rime/squirrel](https://github.com/rime/squirrel))
- 在 Squirrel 的输入提交点 (`commit` event handler) 接入 EktroMemoryStore.log_commit (复用 ektro-pen 现有 schema)
- 启动时唤醒 UploaderDaemon (复用 ektro-pen v0.4 sync 守护进程)
- 同 librime 词库,词库不动 (ektro-pen Windows 用万象/RIME 词库,macOS 沿用)
- 设置面板加 "链接 ektro" 入口 — 调用 `python3 -m auth link` 或 IMK 内嵌 webview
- 三戒之② "记忆属于你" 在 macOS 设置面板明文展示,与 README 一致
- Apple Developer 账号签名,通过 App Store 之外渠道分发 (避免审核流程)

## Capabilities

### New Capabilities

- `macos-ime-shell`: macOS IMK 集成壳 (fork from Squirrel) — IME 注册 / 候选窗 / 焦点切换 / commit 上屏。**继承 Squirrel 现有实现,改动点仅为 EktroMemoryStore 钩子**
- `macos-commit-capture`: 在 Squirrel commit handler 注入 EktroMemoryStore.log_commit + Daemon trigger_sync 钩子。复用 ektro-pen Python 进程 (IPC 或同进程 PyObjC) 调用
- `macos-link-ui`: IMK preference pane 内的 "链接 ektro" 入口 — 触发 OAuth Loopback (复用 ektro-pen `auth.link.link_account`),自动在 Safari 打开同意页
- `macos-launchagent-distribution`: launchctl plist 与 IMK app 打包 (`.pkg` installer) — 用户一次安装,sync daemon 自动随登录起

### Modified Capabilities

无 (新仓库,与 ektro-pen Windows 仓库平级)。

## Impact

**新仓库 ektro-pen-macos** (独立项目):
- fork rime/squirrel 起点,保留 upstream merge 通道
- 集成 ektro-pen Python sync 层作为外部进程 (subprocess + launchctl)
- **Apple Developer 账号: 复用现有公司账号 (Organization,已付费有效)** —
  一个 membership 注册无限 App,与账号已上架的 App 不冲突,无额外开销
- 代码签名 (Developer ID Application/Installer) + 公证 (Notarization) 流程

**ektro-pen 主仓** (本仓):
- Python 模块 (memory/auth/sync) 保持 stdlib-only 不变 — macOS fork 直接复用
- `docs/local-memory-schema.md` / `docs/ektro-link-protocol.md` 等 4 份规范继续作真理源
- README 更新 Platform 标注: `Windows 11 x64` → `Windows 11 x64 + macOS 13+`

**对 ektroai.com 服务端**:
- 零影响 — 服务端 18 endpoints + 4 Inngest workers 与平台无关
- 仅 OAuth 同意页可能需要 macOS-friendly 文案 (现有 Pencil 设计已 OK)

**ektro-pen-android** (已立项):
- 复用同一 sync 层 (Python 移植到 Kotlin 或 PyObjC-Java-bridge)
- 但 Android 路径独立,与本提案并行

## 不做什么 (明确边界)

- **不做从零的 macOS IMK 实现** — fork Squirrel 是务实选择,从零写 3-6 月没意义
- **不做新 librime fork** — 沿用 Squirrel 已有的 librime 编译产物
- **不做新词库** — 万象/RIME 词库与 Squirrel 兼容
- **不改服务端任何代码** — 服务端已生产部署,只是新增 macOS 客户端接入
- **不做 iOS 输入法** — iOS IME 限制极多 (沙盒/全键盘/iCloud Keychain 等),独立提案
- **不在 Mac App Store 上架** — 走 Developer ID 直接分发 (Squirrel 也是这条路),避开 App Store IME 审核地雷

## 当前阶段边界

v1.0 alpha macOS release 含:
- ✅ 拼音/双拼输入 (Squirrel 原生)
- ✅ 候选窗 + 上屏 (Squirrel 原生)
- ✅ EktroMemoryStore commit 落盘 (ektro-pen Python 复用)
- ✅ 链接 ektro + sync 守护 (ektro-pen Python 复用)
- ⏳ Windows-only 特性 (淡灰预测 / 端侧 rerank) 是否移植 — 留作 v1.1
- ⏳ App Store 上架 — 不在 v1.0 范围
