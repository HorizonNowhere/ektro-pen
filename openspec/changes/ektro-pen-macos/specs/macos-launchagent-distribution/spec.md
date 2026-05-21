## ADDED Requirements

### Requirement: .pkg 安装时自动部署 LaunchAgent

ektro-pen-macos .pkg installer 的 post-install 脚本 SHALL 复用 ektro-pen 主仓 `packaging/macos/` 目录的 plist 模板与安装脚本,把 `com.ektro.sync.plist` 渲染并装入 `~/Library/LaunchAgents/` 后调用 `launchctl load`。

LaunchAgent 配置 MUST 包含:
- RunAtLoad: true(登录自动起)
- KeepAlive: {SuccessfulExit: false, Crashed: true}(仅崩溃拉起,主动退出不拉起 — 链接失效时 daemon 自己退,不无脑重启)
- ThrottleInterval: ≥30(防 fast-cycle)
- 日志路径: `~/Library/Logs/Ektro/sync.{out,err}.log`

#### Scenario: 安装后 sync daemon 自动起

- **WHEN** 用户安装 ektro-pen-macos.pkg 后立即查 `launchctl list | grep ektro.sync`
- **THEN** 看到 `<PID>  0  com.ektro.sync` 表示 daemon 已运行

#### Scenario: daemon 因链接失效退出后不重启

- **WHEN** 服务端解绑设备,daemon 收到 403 device_revoked 后 `sys.exit(0)`(SuccessfulExit)
- **THEN** launchctl 看到 SuccessfulExit=false 配置后不拉起;`launchctl list` 显示 PID 为 `-`(未运行);用户需重新链接才能恢复

### Requirement: 卸载脚本保留本地用户数据

ektro-pen-macos 卸载脚本 MUST 仅卸载 .app + LaunchAgent + 系统输入源注册,**不删除** 用户本地数据:
- `~/Library/Application Support/Ektro/ektro.db`(commit_log / word_freq / phrase_pair / device_link / sync_cursor / backfill_state)
- macOS Keychain 中的 ektro-pen Service 凭证
- `~/Library/Logs/Ektro/`(用户可主动查 / 删)

彻底解绑 SHALL 通过单独的 `python3 -m auth unlink --confirm` 命令完成,用户主动决定。

#### Scenario: 卸载后重新安装能恢复链接状态

- **WHEN** 已链接用户卸载 ektro-pen-macos.pkg,然后重新安装
- **THEN** preference pane 显示原有链接状态(device_id / linked_user_id 未动);sync daemon 可继续同步,无需重新链接

#### Scenario: 用户彻底重置需主动跑 unlink

- **WHEN** 用户卸载 .pkg 后想彻底清理(包括账号绑定)
- **THEN** 卸载脚本输出明文提示需额外执行 `python3 -m auth unlink --confirm` 才清除 Keychain + LinkStore.linked_user_id;否则数据保留
