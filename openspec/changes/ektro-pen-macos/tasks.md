## 1. Phase 1 · Fork + 编译 (1 周)

- [ ] 1.1 Apple Developer Program 注册 ($99/年,项目级账号)
- [ ] 1.2 在 ektroai 组织下 fork [rime/squirrel](https://github.com/rime/squirrel) → ektro-pen-macos
- [ ] 1.3 本地 Xcode 编译 Squirrel 通过 (验证 librime 静态链接 / 签名 / 安装到 ~/Library/Input Methods/)
- [ ] 1.4 系统设置 → 键盘 → 输入源添加 ektro-pen-macos,真打字验证拼音→中文流程
- [ ] 1.5 改 Info.plist Bundle ID `im.rime.inputmethod.Squirrel` → `org.ektroai.input.pen` 与 Squirrel 共存
- [ ] 1.6 README 添加分支说明,标记"fork from rime/squirrel, GPL-3"

## 2. Phase 2 · EktroMemoryStore 钩子 (1 周)

- [ ] 2.1 SQLite C 库直接 link 进 Squirrel app (避免 subprocess 延迟) — 测试 commit 落库吞吐
- [ ] 2.2 ObjC 端复刻 EktroMemoryStore.log_commit 的隐私拦截三层 (密码框检测 / 银行卡/身份证/email 正则 / privacy_exclude 表)
  - **关键**: 单元测试与 Python `tests/memory/test_store.py` 对齐, 同 fixture 同结果
- [ ] 2.3 在 `SquirrelInputController.commitComposition:` 后异步写 commit_log (NSOperationQueue background)
- [ ] 2.4 schema.init_db 在 IME app 首启时跑 (调用 schema_v2 SQL)
- [ ] 2.5 真打字 100 字测试,验证 commit_log 行数对齐 + 隐私拦截生效 (密码框打字不入库)

## 3. Phase 3 · 链接 UI (3 天)

- [ ] 3.1 preference pane (Swift) 加 "链接 ektroai.com" 按钮 + 状态文字
- [ ] 3.2 点击调用 `NSTask` 启动 `python3 -m auth link --label=$HOSTNAME`
- [ ] 3.3 读 LinkStore 显示当前链接状态 (账号 handle / 链接时间)
- [ ] 3.4 "解绑" 按钮调用 `python3 -m auth unlink --confirm`
- [ ] 3.5 链接成功后自动启动 sync daemon (`launchctl load ...com.ektro.sync.plist`)
- [ ] 3.6 真链接到生产 ektroai.com 验证 OAuth 完整闭环

## 4. Phase 4 · Sync daemon 集成 (3 天)

- [ ] 4.1 ektro-pen-macos `.pkg` 安装时自动部署 `~/Library/LaunchAgents/com.ektro.sync.plist` (复用本仓 packaging/macos/)
- [ ] 4.2 卸载脚本 (`uninstall.command`) 包含 `launchctl unload` + 卸载 .app + 保留本地数据
- [ ] 4.3 IME app 退出时不停 sync daemon (daemon 独立生命周期)
- [ ] 4.4 验证 daemon 在登录后自动起 + 崩溃自动重启

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
