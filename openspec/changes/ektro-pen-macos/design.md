## Context

### 背景

ektro-pen v0.4 客户端的 Python sync 层 (memory / auth / sync) 已经做成完全跨平台 stdlib + macOS Keychain 集成。**唯一缺口是 macOS IME 主体** — 用户实际打字、拼音转中文、上屏的部分。

macOS 的输入法生态:
- **系统拼音** (Apple): 闭源,无 hook 点
- **搜狗输入法 for Mac**: 闭源,无法 fork
- **鼠须管 / Squirrel** (rime/squirrel): 开源 (GPL-3), librime 客户端, macOS IMK 集成完整, 用户群成熟, 持续维护 (近期 commit < 1 月)
- **小狼毫** (Weasel): Windows-only, ektro-pen 当前 fork 的就是它

Squirrel 是**唯一**符合"开源 + 成熟 + 维护中 + librime-based"四条件的选项。

### Squirrel 现状 (调研)

- 仓库: https://github.com/rime/squirrel
- 语言: Objective-C++ + Swift (近期模块化重写)
- License: GPL-3
- 依赖: librime (静态链接) + macOS IMK Framework
- 分发: Developer ID 签名 + Notarization, `.pkg` installer
- 词库: ~/Library/Rime/ (用户级, 与万象/RIME 词库兼容)
- 主入口: `SquirrelInputController` (IMKInputController 子类)

### 与 ektro-pen Windows 共享

- Python sync 层 100% 复用 (stdlib + macOS keyring 已实现)
- 4 份服务端对接规范 (`docs/`) 是真理源
- EktroMemoryStore SQLite schema 跨平台一致 (v2 含 device_link/sync_cursor/backfill_state)

### 利益相关者

- **Patient Zero**: 翌捷 (项目主理人, macOS 主力开发机) — 第一个用户
- **Squirrel 上游**: 不分裂社区, 我们 fork 仅为加 ektro 钩子, 应保留 upstream merge 通道
- **Apple**: Developer Program 签名审查 (低风险, Squirrel 已成功多年)
- **ektroai.com 服务端**: 零影响, 平台无关

## Goals / Non-Goals

**Goals**:
- 让 macOS 用户能装 ektro-pen-macos IME 并完整使用三戒之①+②+③
- Squirrel fork 改动最小化, 保留 upstream merge 通道
- 复用 ektro-pen Python sync 层 100%, 不重复造轮子
- 用户在 IMK preference pane 一键链接 ektroai.com (走 OAuth Loopback)
- 签名 + 公证, 通过 Developer ID 直接分发 (.pkg)

**Non-Goals**:
- 不上 Mac App Store (审核地雷 + 沙盒限制)
- 不做 iOS 版 (独立提案)
- 不替换 librime / 词库
- 不实现 Windows-only 的 GRU rerank / Qwen3 淡灰预测 (留作 v1.1)
- 不引入新外部 Python 依赖 (stdlib-only 铁律继续)
- 不做新 schema 设计 (复用 ektro-pen v2)

## Decisions

### D1. Fork Squirrel 而非从零实现

**选定**: fork rime/squirrel, 仅修改 commit handler + preference pane.

**替代方案**: 从零写 macOS IMK 实现.

**理由**: Squirrel 已解决 IMK 集成 / 候选窗 / 焦点切换 / 多 app 兼容 / 签名分发 等所有平台细节. 从零写 3-6 月不为产品价值贡献任何独特性. 三戒之②"记忆属于你"才是 ektro-pen 的灵魂 — 输入法本身可借用巨人肩膀.

### D2. Python sync 层用 subprocess 而非 PyObjC 同进程

**选定**: ektro-pen-macos IMK app 通过 `NSTask` (subprocess) 启动并管理 `python3 -m sync daemon`. IPC 通过 sqlite (共享 `~/Library/Application Support/Ektro/ektro.db`).

**替代方案**: PyObjC 同进程嵌入 Python 解释器.

**理由**:
- subprocess 隔离崩溃 — Python 进程死了 IMK 主进程不受影响 (三戒之① 打字永不中断)
- PyObjC 嵌入需要绑定 IMK app 到 Python 版本,签名时引入复杂依赖
- sqlite IPC 简单可靠 (commit_log 表本来就是共享真相源)
- launchctl plist 已经做了 — 复用相同启动方式

### D3. Commit hook 用 SquirrelInputController commit_text 回调

**选定**: 在 `SquirrelInputController.commitComposition:` 调用后, 异步写本地 SQLite (`commit_log`) + 通过文件 sentinel 唤醒 daemon.

**替代方案**: librime 层接 hook (rime_set_notification_handler).

**理由**:
- IMK 层在 Squirrel 已有清晰回调点, 改动小
- librime hook 太底层, 会被 weasel/squirrel 两边的 ektro-pen fork 各自实现 (违反 DRY)
- 写 SQLite 已经在 EktroMemoryStore (Python) 实现, 等价 ObjC 移植或 subprocess CLI 调用 — 用后者简单

### D4. 链接流程: IMK preference pane 触发 Safari + OAuth

**选定**: ektro-pen-macos preference pane 有 "链接 ektroai.com" 按钮, 点击调用 `python3 -m auth link` 子进程, 子进程自己启动 loopback server + 调 `webbrowser.open`. preference pane 通过 stdout 观察子进程状态.

**替代方案**: IMK app 内嵌 WKWebView 走完整 OAuth.

**理由**:
- 已有 Python OAuth 流程, 0 行额外代码
- WKWebView 嵌入需要处理 cookie / session / TLS / 安全模型 — 复杂
- Safari 弹出体验对用户更可信 (URL 栏可见 https://ektroai.com)

### D5. 词库: Squirrel 默认词库 + 万象拼音叠加 (用户可选)

**选定**: 安装时不强制下载 ektro 自定义词库, 默认走 Squirrel 自带 + 用户自己装万象 (rime/squirrel-data) 走同样 ~/Library/Rime/ 路径.

**替代方案**: 强制带万象词库一起打包.

**理由**:
- ektro-pen 的核心价值不在词库 (那是 librime 解决的), 在三戒②
- 词库捆绑增加 .pkg 体积 + 法律风险 (万象的 license 不同)
- 用户已有偏好词库时, 我们覆盖会让他们恼火

### D6. 分发: Developer ID + Notarization, 不上 App Store

**选定**: Apple Developer Program ($99/年), `codesign + notarytool` 流程, `.pkg` installer 走自家网站分发.

**替代方案**:
- Mac App Store 上架 — 沙盒限制 IME 接收事件 / 词库读写 / Keychain 访问 等场景, 几乎不可能通过审核
- 不签名分发 — 用户体验差 (Gatekeeper 警告), 也不安全 (没有公证 = 易被恶意篡改)

**理由**: Squirrel 也走同样路径, 已验证 macOS 13+ 用户体验流畅.

### D7. 版本号策略

**选定**: ektro-pen-macos 用独立版本号 (v1.0.0 起步), 不绑定 ektro-pen Windows 的 v0.4. Python sync 层版本作为依赖标注.

**理由**: macOS 路径有自己的 release cycle, 与 Windows 解耦.

## Risks / Trade-offs

**R1. Squirrel upstream 大改动后我们的 fork 难合并**
→ 缓解: 改动点严格控制在 EktroMemoryStore 钩子 + preference pane 一行链接按钮, 不动核心 IME 逻辑. 每月 rebase upstream.

**R2. Apple Developer 审核拒绝签名**
→ 缓解: Squirrel 已成功多年, 流程成熟. 与 Apple 沟通时表明这是开源拼音 IME (与 Squirrel 同性质), 不大可能被拒.

**R3. Python 进程 (sync daemon) 与 IMK 主进程的 SQLite 并发写**
→ 缓解: schema 设计已用 WAL 模式 + 单 writer lock. EktroMemoryStore 早就处理过, 不需要新机制.

**R4. 用户体验割裂 (打字在 IMK app, 链接管理在 Terminal)**
→ 缓解: preference pane 有 "链接 ektroai.com" 按钮 (D4); 用户基本不用碰 Terminal 也能完成链接.

**R5. macOS 用户已有 Squirrel 装在 ~/Library/Rime/, 我们的 fork 冲突**
→ 缓解: ektro-pen-macos 用独立 plist Bundle ID (`org.ektroai.input.pen` 而非 `im.rime.inputmethod.Squirrel`), 词库目录独立 (`~/Library/EktroPen/`). 用户可两个同时装.

**R6. 拓展词库 vs 简洁安装**
→ 留 v1.1 决定 — v1.0 默认无自带词库.

**R7. Python 版本依赖**
→ 缓解: stdlib only + Python 3.10+ macOS 默认已有. plist 显式指定 `/usr/bin/python3` 而非 Homebrew 路径.

**R8. signing 证书过期**
→ 缓解: 项目级运维 calendar 提醒 + Apple Developer 自动续费.

## Migration Plan

### Phase 1 · Fork + 编译 (1 周)
- fork rime/squirrel → ektroai/ektro-pen-macos
- 本地编译 + 签名 + 装系统 IME 验证基本拼音功能
- 确认 librime / 词库链路畅通

### Phase 2 · EktroMemoryStore 钩子 (1 周)
- `SquirrelInputController.commitComposition:` 注入异步写 SQLite
- 写 ObjC ↔ Python 桥 (subprocess CLI 调用 `python3 -m memory log-commit ...` 或直接 sqlite3 直写)
- 单元测试 commit 真落库

### Phase 3 · 链接 UI (3 天)
- IMK preference pane 加 "链接 ektroai.com" 按钮
- 点击启动 Python OAuth 流程 (subprocess)
- preference pane 显示链接状态 (从 LinkStore 读)

### Phase 4 · 打包 + 签名 + 分发 (1 周)
- `pkgbuild` + `productbuild` 生成 .pkg
- Developer ID 签名 + notarytool 公证
- 在 ektroai.com/download 提供下载链接 + 安装指南

### Phase 5 · Patient Zero 内测 (1 周)
- 翌捷自用 7 天, 验证打字流畅 + sync 正常 + 链接体验
- 邀请 3-5 个 macOS 公民内测

### Phase 6 · 公测 (开放)
- 公开 GitHub release
- README 加 macOS 安装段

总计预估: **6-8 周**, 一个开发者全职.

### 回滚

- ektro-pen-macos 是独立仓库, 出问题不影响 Windows 端
- 用户卸载 .pkg 即移除, 不动 ~/Library/Application Support/Ektro/ 本地数据 (除非用户主动 `python -m auth unlink`)
- 服务端零侵入 — 真没法用就让用户卡在"无 macOS IME"状态, 但 ektro-pen Windows 仍可用

## Open Questions

1. **Squirrel 模块化重写还在进行中** — 我们 fork 现在还是等稳定后 fork? 倾向: 立即 fork 当前 main, 改动小可跟上.
2. **词库下载是否提供 ektroai.com mirror** — 大陆用户访问 GitHub 慢, 自家 mirror 是否值得? 倾向: v1.0 不做, v1.1 评估.
3. **preference pane 用 Swift 还是 ObjC** — Squirrel 部分新代码已 Swift, preference pane 用 Swift 与之一致. 倾向: Swift.
4. **subprocess 还是嵌入式 sqlite3 直写 commit_log** — D3 说 subprocess CLI, 但更轻量是 ObjC 端直接 sqlite3 写表 (绕过 Python). 倾向: 后者, 但需要在 ObjC 端复刻 EktroMemoryStore 的 privacy 拦截 (危险). 倾向回到 subprocess CLI.
5. **Apple Developer 账号谁注册** — 项目级账号 ($99/年) 还是翌捷个人? 倾向: 项目级 (避免单人离开锁定).
