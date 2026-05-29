## 0. Phase 0 · C++ SDK 跨平台基础 ✅ 已完成 (2026-05-22)

- [x] 0.1 cmake -DEKTRO_PLATFORM=macos configure + build 通过 (Apple Silicon)
- [x] 0.2 现有 33 GoogleTests 在 macOS 全过 — 证明 src-cpp 跨平台无障碍
- [x] 0.3 C++ schema 升 v2 (schema.h/cpp) — device_link / sync_cursor / backfill_state
       与 Python schema.py 一字不差, v1→v2 migration + seed 单行
- [x] 0.4 test_schema_v2.cpp 10 GoogleTests 全过 (C++ 测试总数 43)
- 结论: macOS fork 的 commit capture = 直接 link `ektro_sdk.h` C ABI, 零业务逻辑复刻

## 1. Phase 1 · Fork + 编译 ✅ 已完成 (2026-05-28, 工程层)

- [ ] 1.1 复用现有 Apple Developer 公司账号 (Organization, 已付费有效): **翌捷亲做**
       - 确认操作人角色为 Account Holder / Admin (Developer ID 证书需此权限)
       - 在 Certificates 创建 `Developer ID Application` + `Developer ID Installer` 两张证书
       - 注: 走 Developer ID 分发, 不上 Mac App Store, 与账号之前已上架的 App 互不冲突
       - 无需新账号 / 无需额外 $99
- [ ] 1.2 在 ektroai 组织下 fork [rime/squirrel](https://github.com/rime/squirrel) → ektro-pen-macos: **翌捷亲做** (需 ektroai org 权限);本地工作树已就绪 (`<playground>/ektro-pen-macos/squirrel/`)
- [x] 1.3 本地 Xcode 26.4 编译通过 — 走 `action-install.sh` 快路径拉 rime 1.16.1 + Sparkle 2.6.2 预编译,跳过 30+ min Boost 编译,产物 `ektro-pen.app` (32MB, arm64)
- [ ] 1.4 系统设置 → 键盘 → 输入源添加 ektro-pen-macos,真打字验证拼音→中文流程: **翌捷亲做** (需 sudo cp + GUI 点)
- [x] 1.5 Info.plist Bundle ID `im.rime.inputmethod.Squirrel` → `org.ektroai.input.pen`,PRODUCT_NAME=`ektro-pen`,模块名=`ektro_pen`,TISInputSourceID/InputMethodConnectionName/ControllerClass 全部同步,与 Squirrel 共存
- [x] 1.6 主仓 README 留待 Phase 7 一并改 (现仍是 Windows-only 文案);新仓 ektro-pen-macos 的 README 待 Phase 7 翌捷亲拟

## 2. Phase 2 · commit capture 接 C ABI ✅ 已完成 (2026-05-28)

- [x] 2.1 libektro.a (1.1MB, arm64) + ektro_sdk.h 复制到 `squirrel/ektro/{lib,include}/`;project.pbxproj 加 LIBRARY_SEARCH_PATHS / HEADER_SEARCH_PATHS / `OTHER_LDFLAGS="-lrime.1 -lektro -lc++ -lsqlite3"` (sqlite3 走系统自带)
- [x] 2.2 **Swift 桥(不是 ObjC++,因 upstream Squirrel 已迁 Swift)**: 新 `sources/EktroBridge.swift` 单例,bridging header 加 `#import "ektro/ektro_sdk.h"`;`applicationWillFinishLaunching` 调 `EktroBridge.shared.start()` (创建 ctx + 自动跑 schema v2 init_db),`applicationWillTerminate` 调 `stop()`;db 路径 `~/Library/Application Support/Ektro/ektro.db`
- [x] 2.3 在 `SquirrelInputController.rimeConsumeCommittedText()` 注入:捕获 `rimeAPI.get_input(session)` 拼音 + `commitText.text` 中文 → `EktroBridge.shared.logCommit(raw, output, isPassword)`;串行 DispatchQueue 异步,绝不阻塞 IME 主线程
- [x] 2.4 密码框检测用 Carbon `IsSecureEventInputEnabled()` (返回 DarwinBoolean);每次 logCommit 都传当前态
- [ ] 2.5 真打字 100 字测试,验证 commit_log 行数对齐 + 密码框打字不入库 + ektro_last_error 为空: **翌捷亲做** (需先 1.1+1.4 装机)

## 3. Phase 3 · 链接 UI ✅ 已完成 (2026-05-28, 代码层)

- [x] 3.1 preference pane (`EktroPreferencesWindow`, 纯 AppKit, 无 SwiftUI 依赖) 含 "链接 ektroai.com" + 状态文字 + "查看我的记忆 (跳 ektroai.com)" + "导出本地记忆" + "清空本地记忆" (二次确认) + "在 Finder 中显示"
- [x] 3.2 链接按钮调 `Process()` 跑 `/usr/bin/env python3 -m auth link --label=$HOSTNAME`;PYTHONPATH 指向 app bundle `Contents/Resources/python` (Phase 4 打包时填实际 python sync 源码)
- [x] 3.3 读 `~/Library/Application Support/Ektro/link_state.json` 显示当前链接状态 (handle / linked_at)
- [x] 3.4 "解绑" 按钮 NSAlert 二次确认后跑 `python3 -m auth unlink --confirm`,本地 SQLite 不动 (戒②)
- [x] 3.5 `refreshStatus()` 自动同步 sync daemon 状态:已链接 → `EktroLaunchAgent.install()`,未链接 → `uninstall()`;`SquirrelInputController.menu()` 顶部加"ektro-pen 偏好设置…"项
- [ ] 3.6 真链接到生产 ektroai.com 验证 OAuth 完整闭环: **翌捷亲做** (需 ektroai 账号 + 浏览器同意)

## 4. Phase 4 · Sync daemon 集成 ✅ 已完成 (2026-05-28, 代码层)

- [x] 4.1 `EktroLaunchAgent.install()`:动态 render plist 到 `~/Library/LaunchAgents/com.ektro.sync.plist` + `launchctl bootstrap gui/<uid>` 加载;Python 路径自动嗅探 `/opt/homebrew/bin/python3` → `/usr/local/bin/python3` → `/usr/bin/python3` → `env` 兜底;日志走 `~/Library/Logs/Ektro/`;`KeepAlive`={Crashed=true, SuccessfulExit=false} + `ThrottleInterval=30s`
- [x] 4.2 `EktroLaunchAgent.uninstall()`:`launchctl bootout gui/<uid>/com.ektro.sync` + 删 plist;**本地 SQLite 保留 — 戒②**;Phase 5 .pkg 卸载脚本会调此 + 卸载 .app
- [x] 4.3 IME app 退出时不停 sync daemon — `EktroBridge.stop()` 只关 C ABI ctx,launchctl 由 OS 独立管理 daemon 生命周期 (登录起、崩溃重启 30s 间隔)
- [x] 4.4 `applicationWillFinishLaunching` 检测 link_state.json 存在 → 自动 install daemon (幂等);`isRunning()` 用 `launchctl print gui/<uid>/com.ektro.sync` 探活

## 5. Phase 5 · 打包 + 签名 + 公证 (1 周)

- [ ] 5.1 用 Developer ID Application 证书签 ektro-pen-macos.app
- [ ] 5.2 `pkgbuild` 打 .pkg installer (含 .app + LaunchAgent plist + Python bundling)
- [ ] 5.3 `productbuild` 加 distribution.xml 美化安装界面
- [ ] 5.4 `notarytool submit` 走 Apple 公证 (~30 min)
- [ ] 5.5 `xcrun stapler staple` 装订公证票据到 .pkg
- [ ] 5.6 测试: 干净 macOS 13 机器双击 .pkg 一路安装 → IME 立即可用 + 链接 + sync

## 6. Phase 6 · Patient Zero + Beta (1 周内测)

- [ ] 6.1 翌捷自用 7 天,记录每个 bug / 体验断点 / 性能问题
- [ ] 6.2 邀请 3-5 个 macOS 公民内测,收集 commit log + 链接成功率
- [ ] 6.3 抽样验证 ektroai.com 服务端 `ime_signals` 真有 macOS commit 数据 + Twin extractor 真在跑
- [ ] 6.4 Bug fix 迭代

## 7. Phase 7 · 公测发布

- [ ] 7.1 ektro-pen-macos GitHub repo 发布 v1.0.0
- [ ] 7.2 ektroai.com/download 加 macOS 下载入口 + 安装指南
- [ ] 7.3 README 主仓 (ektro-pen) 加 macOS 章节,标 Platform: Windows 11 x64 + macOS 13+
- [ ] 7.4 公开 changelog 公告 (Bluesky + ektroai.com banner)
- [ ] 7.5 30 天复盘: macOS 公民数量 / 每日 sync 字数 / Bug 率

## 8. 风险跟踪

- [ ] 8.1 Squirrel upstream 重大改动监控 — 每周 rebase
- [ ] 8.2 Apple Developer 证书 expiry calendar 提醒
- [ ] 8.3 macOS 主版本升级 (14→15→...) 兼容性测试 (每年一次)

## 9. 不在本 change 范围

- iOS IME (独立 OpenSpec change)
- Windows 端 ektro-pen-macos 端 commit 时间线合并 UI (独立 feature)
- 词库 mirror (留 v1.1 评估)
- Mac App Store 上架 (审核地雷, 不做)
- ektro-pen Windows GRU rerank / Qwen3 淡灰预测移植到 macOS (留 v1.1)

总计预估 6-8 周,一个全职开发者。
