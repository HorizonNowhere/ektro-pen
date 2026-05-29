## ADDED Requirements

### Requirement: IMK preference pane 提供链接 ektroai.com 入口

ektro-pen-macos preference pane SHALL 包含一个"链接 ektroai.com"区域,显示:
- 当前链接状态(已链接 @handle / 未链接)
- "链接" / "解绑" 按钮(根据状态切换)
- 设备 device_id(可复制)
- 上次 sync 时间(从 LinkStore.sync_cursor 读)

点击"链接"按钮 MUST 通过 `NSTask` 启动 `python3 -m auth link --label=$HOSTNAME` 子进程(复用 ektro-pen Python OAuth Loopback 实现)。

#### Scenario: 用户点链接按钮触发 OAuth

- **WHEN** 未链接用户在 macOS 系统设置→键盘→输入源→ektro-pen→选项 中点击"链接 ektroai.com"
- **THEN** Safari 自动打开 ektroai.com/me/ime-link 同意页;用户登录后点"允许链接"→自动跳回 loopback;preference pane 状态更新为"已链接 @testuser"

#### Scenario: 用户点解绑按钮

- **WHEN** 已链接用户点 preference pane 中的"解绑"按钮 + 确认二次弹窗
- **THEN** preference pane 通过 NSTask 调 `python3 -m auth unlink --confirm`;Keychain 凭证清除;LinkStore.clear_link 执行;界面更新为"未链接"

### Requirement: preference pane 状态从 LinkStore 单向读取

preference pane MUST NOT 自己维护链接状态,SHALL 每次显示时从 `~/Library/Application Support/Ektro/ektro.db` 的 device_link 表读取最新数据。

避免 preference pane 与 Python sync daemon 状态不一致(daemon 在后台标记链接失效时,UI 应立即反映)。

#### Scenario: daemon 后台清除链接后 UI 自动反映

- **WHEN** sync daemon 后台收到 403 device_revoked 后调 token_manager.handle_server_revocation 清除链接
- **THEN** 用户下次打开 preference pane 看到"未链接"状态(从 device_link.linked_user_id IS NULL 推断)
