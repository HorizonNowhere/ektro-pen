## ADDED Requirements

### Requirement: 上屏后异步写入 commit_log

ektro-pen-macos SHALL 在 `SquirrelInputController.commitComposition:` 被调用后,异步把本次 commit 写入 `~/Library/Application Support/Ektro/ektro.db` 的 commit_log 表,字段对齐 ektro-pen Windows 的 EktroMemoryStore schema v2(commit_log / word_freq / phrase_pair 同表结构)。

异步写 MUST NOT 阻塞 IME 主线程,失败 MUST NOT 影响打字流(三戒之①)。

#### Scenario: 打 100 字后 commit_log 有对应行

- **WHEN** 用户在 Notepad 打 100 个汉字(经 ektro-pen-macos 上屏)
- **THEN** `sqlite3 ~/Library/Application\ Support/Ektro/ektro.db "SELECT count(*) FROM commit_log"` 返回 ≥ 100(精确数因 phrase 合并可能 ≤ 100)

#### Scenario: SQLite 写失败不影响打字

- **WHEN** 模拟 SQLite 写盘失败(磁盘满 / 权限错)
- **THEN** IME 仍正常上屏,后台错误日志记录,用户感知不到中断

### Requirement: 复刻 ektro-pen 隐私拦截三层

ektro-pen-macos 的 commit 写入路径 MUST 在写入 SQLite 前应用与 ektro-pen Windows 相同的三层隐私拦截:

1. **L1 权威**: macOS 等价的 `kSecAttrIsInvisible` / 密码框 scope 检测 → 跳过
2. **L2 启发**: input_raw 命中银行卡 16-19 位 / 中国身份证 18 位 / email 正则 → 跳过
3. **L3 用户**: app_name 在 privacy_exclude 表 → 跳过

任一命中,该次 commit 不写 commit_log,因此也不会出现在后续 sync 上传中。

#### Scenario: 密码框打字不入库

- **WHEN** 用户在 macOS 钥匙串 / 1Password / 浏览器密码字段打字
- **THEN** commit_log 不增加任何行;sync_cursor 也不动

#### Scenario: 银行卡数字被 L2 正则拦截

- **WHEN** 用户在任意应用打"4111111111111111"(16 位 Visa 测试号)
- **THEN** commit_log 不入库,IME 仍正常上屏(本地不留痕)
