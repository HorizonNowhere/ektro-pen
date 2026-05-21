# EKTRO 本地记忆 Schema 规范

> 这是 ektro-pen 本地 SQLite 的字段契约。所有客户端实现（Python 参考层 / C++ 实施层 / 未来 macOS 版）都以本文件为准。
> 字段一经冻结，不向后兼容修改。
>
> 关联文档：
> - [ektro-link-protocol.md](./ektro-link-protocol.md) — 设备链接握手协议
> - [ime-ingest-contract.md](./ime-ingest-contract.md) — 上传 API 契约
> - [privacy-boundary.md](./privacy-boundary.md) — 隐私边界铁律

---

## 0. 立场

**记忆属于你。**

ektro-pen 一经安装即提供完整本地记忆能力 — 离线、明文、可视、可导出、可删除。
登入 ektro 是**可选升级路径**，不是必经环节。本文件描述的本地存储在不联网状态下完整工作。

---

## 1. 存储位置

| 平台 | DB 路径 | Keyring 后端 |
|------|---------|--------------|
| Windows | `%APPDATA%\Rime\ektro\ektro.db` | Windows Credential Manager |
| macOS（规划中） | `~/Library/Application Support/Ektro/ektro.db` | Keychain |

**SQLite 模式**：明文（无加密）+ WAL journal。用户可用任何 SQLite 客户端直接打开。

**唯一例外**：ektro 颁发的 JWT / refresh_token **不存 SQLite**，存 OS keyring（性质是凭证不是用户数据，明文承诺不包含它）。

---

## 2. v1 现有表（已实施 · 字段冻结）

### 2.1 `commit_log` — 每次上屏一行

| 字段 | 类型 | 本地必存 | 可上传 | 语义 |
|------|------|----------|--------|------|
| `id` | INTEGER PK | ✓ | ✗ | 本地自增，仅作同步 cursor，云端不接受客户端 id |
| `timestamp` | INTEGER | ✓ | ✓ | unix ms，上屏时刻 |
| `input_raw` | TEXT | ✓ | ✓ | 原始拼音（"nihaoshijie"） |
| `output` | TEXT | ✓ | ✓ | 上屏中文（"你好世界"） |
| `app_name` | TEXT | ✓ | **可选** | 焦点应用名；默认**不上传**，用户主动开启才上传 |
| `context_id` | INTEGER | ✓ | ✗ | 本地会话分组，仅本地 rerank 用 |
| `user_picked` | INTEGER | ✓ | ✓ | 0/1，是否按 Tab 切换过候选（强信号） |
| `duration_ms` | INTEGER | ✓ | ✓ | 首字到 commit 的耗时（节奏信号） |

**索引**：`(timestamp DESC)`、`(app_name, timestamp DESC)`。

**写入路径**：`EktroMemoryStore.log_commit()`（已实现于 [src/memory/store.py](../src/memory/store.py)）。

---

### 2.2 `word_freq` — 字粒度词频

| 字段 | 类型 | 本地必存 | 可上传 | 语义 |
|------|------|----------|--------|------|
| `word` | TEXT PK | ✓ | ✓（仅 aggregate 模式） | 单字符（注：当前实现按字而非词拆分） |
| `count` | INTEGER | ✓ | ✓ | 累计出现次数 |
| `last_used` | INTEGER | ✓ | ✓ | unix ms |

**上传策略**：
- `backfill.mode='full'`：**不上传**（云端从 commit_log 重建）
- `backfill.mode='aggregate'`：上传（隐私优先模式，不传原始 commit_log）
- 增量同步阶段：**不上传**（云端持续从 ingest 重建）

---

### 2.3 `phrase_pair` — 字粒度二元组

| 字段 | 类型 | 本地必存 | 可上传 | 语义 |
|------|------|----------|--------|------|
| `prev` | TEXT | ✓ | ✓（同 word_freq） | 前字 |
| `curr` | TEXT | ✓ | ✓ | 后字 |
| `count` | INTEGER | ✓ | ✓ | 出现次数 |

PK：`(prev, curr)`。索引：`(curr, count DESC)`。上传策略同 word_freq。

---

### 2.4 `privacy_exclude` — 应用黑名单

| 字段 | 类型 | 本地必存 | 可上传 | 语义 |
|------|------|----------|--------|------|
| `pattern` | TEXT PK | ✓ | ✗ | app_name 或特殊 scope 如 `PASSWORD` |
| `reason` | TEXT | ✓ | ✗ | 用户备注 |
| `created_at` | INTEGER | ✓ | ✗ | unix ms |

**永不上传**：用户黑名单是隐私配置，云端不应感知。

---

### 2.5 `config` — KV 配置

| 字段 | 类型 | 本地必存 | 可上传 | 语义 |
|------|------|----------|--------|------|
| `key` | TEXT PK | ✓ | ✗ | 配置键 |
| `value` | TEXT | ✓ | ✗ | 配置值（字符串） |

**永不上传**：本地客户端偏好，与 Twin 无关。

默认 6 项（CLAUDE.md ≤6 个开关铁律）：
`enable_rerank` / `enable_predictor` / `predictor_delay_ms` / `learning_enabled` / `excluded_apps` / `theme`。

---

## 3. v2 新增表（ime-twin-link 提案 · 待实施）

> v2 新增 3 张表，承载"链接 ektro / 同步进度 / 首次回填"。不动 v1 任何表结构。
> Migration：`PRAGMA user_version` 从 1 → 2，仅 ADD TABLE。

### 3.1 `device_link` — 设备 + 链接状态（单行）

```sql
CREATE TABLE IF NOT EXISTS device_link (
    id              INTEGER PRIMARY KEY CHECK (id = 1),   -- 强制单行
    device_id       TEXT    NOT NULL UNIQUE,              -- UUIDv4，首启生成，永不变
    device_label    TEXT,                                 -- 用户给本机起的名（"我的台式机"），可空
    created_at      INTEGER NOT NULL,                     -- device_id 生成时间
    -- 链接状态（NULL = 未链接）
    linked_user_id     TEXT,                              -- ektro 用户 UUID
    linked_user_handle TEXT,                              -- 显示用 handle（"@yijie"）
    linked_at          INTEGER,
    revoked_at         INTEGER,                           -- 解绑时间（软痕迹）
    ektro_endpoint     TEXT NOT NULL DEFAULT 'https://ektroai.com'
);
```

| 字段 | 本地必存 | 可上传 | 备注 |
|------|----------|--------|------|
| `device_id` | ✓ | ✓（仅鉴权头） | 永不变，跨链接保留 |
| `device_label` | ✓ | ✓ | 用户可改 |
| `linked_user_id` | NULL/✓ | — | 是判断"是否已链接"的唯一权威 |
| `linked_at` | — | ✓ | 服务端审计 |
| `revoked_at` | — | ✗ | 仅本地记录 |

**JWT/refresh_token**：**不存本表**，存 OS keyring（key: `ektro-pen://${device_id}`）。

**单行约束**：`CHECK (id = 1)` — 一台机器只能绑一个 ektro 账号。换账号必须先解绑。

---

### 3.2 `sync_cursor` — 增量同步位点（单行）

```sql
CREATE TABLE IF NOT EXISTS sync_cursor (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    last_synced_commit_id   INTEGER NOT NULL DEFAULT 0,   -- commit_log.id 已上传的最大值
    last_sync_at            INTEGER,                       -- 上次成功上传 unix ms
    last_attempt_at         INTEGER,                       -- 最后一次尝试（含失败）
    last_error              TEXT,                          -- 最后失败原因（可空）
    pending_count           INTEGER NOT NULL DEFAULT 0,    -- 待上传 commit 数（UI 显示用）
    total_uploaded          INTEGER NOT NULL DEFAULT 0     -- 累计上传 commit 数
);
```

**语义**：增量同步 = `SELECT * FROM commit_log WHERE id > last_synced_commit_id ORDER BY id LIMIT batch_size`。

**幂等保证**：上传成功后再原子更新 cursor；失败不动。重试 = 重新捞同一段，云端按 `content_hash` 去重。

**永不上传**：本地进度，与 Twin 无关。

---

### 3.3 `backfill_state` — 首次回填进度（单行）

```sql
CREATE TABLE IF NOT EXISTS backfill_state (
    id                      INTEGER PRIMARY KEY CHECK (id = 1),
    mode                    TEXT,                          -- 'full' | 'aggregate' | 'none' | NULL
    started_at              INTEGER,
    completed_at            INTEGER,
    last_uploaded_commit_id INTEGER,
    total_to_upload         INTEGER,                       -- 起始时快照的 commit 总数
    total_uploaded          INTEGER NOT NULL DEFAULT 0,
    error                   TEXT
);
```

**语义**：链接 ektro 后用户选择"全量 / 仅画像 / 不迁移"三选一，进度持久化，崩了能续。

`mode`：
- `'full'` — 上传完整 `commit_log` 历史
- `'aggregate'` — 仅上传 `word_freq` + `phrase_pair`（隐私优先）
- `'none'` — 从链接时刻起开始增量喂养
- `NULL` — 未开始（刚链接完，用户还没选）

完成（`completed_at IS NOT NULL`）后进入纯增量阶段，本表仅作历史记录。

---

## 4. 隐私分级总表

```
                              本地必存   可上传(默认)   可上传(用户开启)   永不上传
commit_log
  id                            ✓                                          ✓
  timestamp                     ✓          ✓
  input_raw                     ✓          ✓
  output                        ✓          ✓
  app_name                      ✓                          ✓
  context_id                    ✓                                          ✓
  user_picked                   ✓          ✓
  duration_ms                   ✓          ✓

word_freq / phrase_pair         ✓         （仅 aggregate backfill 模式上传）

privacy_exclude                 ✓                                          ✓
config                          ✓                                          ✓

device_link
  device_id                     ✓        （仅作鉴权头）
  device_label                  ✓          ✓
  linked_at                                ✓
  ektro_endpoint                ✓                                          ✓ (本地配置)

sync_cursor / backfill_state    ✓                                          ✓
```

**禁存清单**（既不进 SQLite 也不上传，三层拦截已实现）：

| 拦截层 | 信号 | 实现位置 |
|--------|------|----------|
| L1（权威） | `is_password_field=True`（来自 TSF IS_PASSWORD scope） | [store.py:187](../src/memory/store.py#L187) |
| L2（启发） | `input_raw` 命中银行卡 16-19 位 / 中国身份证 18 位 / email 正则 | [store.py:133](../src/memory/store.py#L133) |
| L3（用户） | `app_name` 在 `privacy_exclude` 表 | [store.py:154](../src/memory/store.py#L154) |

**任一命中 → `LogResult.SKIPPED_*`，不写本地 SQLite，自然也不会上传**。

---

## 5. 用户主权契约（不可妥协）

**主从关系**：本地 SQLite 是 source of truth（"记忆属于你"），云端是用户授权的 Twin 训练副本。

| 能力 | 实现 | 本地完成 | 云端联动 |
|------|------|----------|----------|
| 看 | 用任意 SQLite 客户端打开 `ektro.db` | ✓ | — |
| 导出 | `EktroMemoryStore.export_all()` → JSON | ✓ | 已链接时附带云端 `/me/inputs/export` |
| 删除（区间） | `delete_range(start_ms, end_ms)` | ✓ | **强制级联**：先调云端 DELETE，成功后再删本地 |
| 删除（全部） | `clear_all(confirm=True)` + VACUUM | ✓ | **强制级联**：先调云端 clear all |
| 解绑 | 清空 `device_link.linked_*` + keyring drop | ✓ | 同时调云端 `/me/devices/:id/revoke` |
| 跨机迁移 | 链接同账号 → 云端拉历史回灌本机 commit_log | — | 必须已链接 |

**删除定向语义**（取代之前的"双向删除铁律"）：

| 触发方 | 本地 | 云端 |
|--------|------|------|
| IME 删除（本地→云端） | ✓ | **强制级联** |
| Web 删除（云端单边） | 不动 | ✓ |

理由：用户在两端的删除是**两个不同的决定**，工具不应替用户做并集。"我在 IME 删某段" = 这段我连自己都不想留 → 必须删云端；"我在 Web 删某段" = 我不想让 Twin 用这段训练，但本地还要用 → 不波及本地。

详见 [privacy-boundary.md §5](./privacy-boundary.md) 与 [ime-ingest-contract.md §0.1](./ime-ingest-contract.md)。

---

## 6. v1 → v2 Migration

```python
# src/memory/schema.py 升级路径
CURRENT_SCHEMA_VERSION = 2

MIGRATION_V1_TO_V2 = """
CREATE TABLE IF NOT EXISTS device_link (...);
CREATE TABLE IF NOT EXISTS sync_cursor (...);
CREATE TABLE IF NOT EXISTS backfill_state (...);

-- device_id 首启生成
INSERT OR IGNORE INTO device_link (id, device_id, created_at)
VALUES (1, lower(hex(randomblob(16))), strftime('%s','now')*1000);

-- 单行表 seed
INSERT OR IGNORE INTO sync_cursor (id) VALUES (1);
INSERT OR IGNORE INTO backfill_state (id) VALUES (1);
"""

def migrate_v1_to_v2(conn) -> None:
    conn.executescript(MIGRATION_V1_TO_V2)
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
```

**回滚**：v2 → v1 不支持（一旦链接过就不允许降级）。备份原则：migration 前自动 `ektro.db` → `ektro.db.v1.bak`。

---

## 7. 字段冻结清单（破坏性改动需修宪）

以下字段一经实施，**改名 / 删除 / 改类型 = 破坏向后兼容**，需走 OpenSpec change 修订流程：

```
commit_log:    id / timestamp / input_raw / output
word_freq:     word / count
phrase_pair:   prev / curr / count
device_link:   device_id / linked_user_id
sync_cursor:   last_synced_commit_id
```

允许向后兼容的操作：
- ADD COLUMN（必须有 DEFAULT，不能 NOT NULL 无默认）
- ADD TABLE
- ADD INDEX

不允许：DROP COLUMN / RENAME COLUMN / ALTER TYPE / DROP TABLE。

---

## 8. 实施清单（给 ektro-pen 仓库后续 PR）

- [ ] `src/memory/schema.py`：`CURRENT_SCHEMA_VERSION = 2` + `migrate_v1_to_v2()` + v2 三表 DDL
- [ ] `src/memory/store.py`：新增 `get_device_id()` / `get_link_state()` / `set_link_state()` / `clear_link()` / `get_sync_cursor()` / `advance_cursor(commit_id)` / `set_backfill_mode(mode)`
- [ ] `src-cpp/include/ektro/memory_store.h`：同名 C++ API
- [ ] OS keyring 集成：Python 用 `keyring` 库，C++ Windows 用 `wincred.h`，macOS 用 `Security.framework`
- [ ] 单元测试：v1→v2 migration、device_id 唯一性、单行表 CHECK、cursor 幂等更新
- [ ] 文档更新：`README.md` 三戒之② 改为"记忆属于你"（见 [open issue]）

---

**本文档定稿后**，下一份是 [ektro-link-protocol.md](./ektro-link-protocol.md)（设备链接握手 OAuth-like 流程）。
