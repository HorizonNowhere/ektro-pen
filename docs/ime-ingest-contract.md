# IME Ingest API 契约

> ektro-pen 链接成功后，上传 / 同步 / 删除 / 导出本地记忆的完整 API 契约。
> 服务端实现在 ektro 仓库，本文件是双仓共享真理源。
>
> 关联文档：
> - [local-memory-schema.md](./local-memory-schema.md) — 上传白名单字段定义在此
> - [ektro-link-protocol.md](./ektro-link-protocol.md) — 鉴权 token 来源
> - [privacy-boundary.md](./privacy-boundary.md) — 上传/不上传字段的最终红线

---

## 0. 立场

### 0.1 主从关系（修正 schema doc §5）

```
   本地 SQLite (ektro-pen)             ektroai.com (云端)
   ─────────────────────             ──────────────────
   source of truth   ──选择性上传──→  Twin 训练副本
   用户主权完整                       受用户主权约束
```

**删除是定向的**：

| 动作 | 本地 | 云端 |
|------|------|------|
| 本地删 commit_log | ✓ | ✓ **强制级联** |
| 云端删 external_signals | ✗ 不影响 | ✓ |
| 本地 clear_all | ✓ | ✓ **强制级联** |
| 云端 clear all inputs | ✗ 不影响 | ✓ |

**理由**：本地代表用户的完整记忆主权（"记忆属于你"）；云端是用户选择性上传给 Twin 训练的副本。
"我在 IME 删某段"= 这段我连自己都不想留 → 必须级联。
"我在 ektroai.com 删某段"= 我不想让 Twin 用这段训练，但我本地还要用 → 不波及本地。

这条主从关系**取代** schema doc §5 中的"双向删除铁律"。schema doc 待回头修订。

### 0.2 上传是 push，不是 pull

服务端不主动拉客户端数据。所有上传由客户端 push 触发。
服务端的唯一"返流"是 deletion notice（告诉客户端"某段你之前上传过的内容服务端删了"），用于客户端 UI 展示一致性 — **不强制本地动作**。

---

## 1. 端点清单

| 用途 | 方法 + 路径 | 鉴权 | 频次 |
|------|------------|------|------|
| 选 backfill 模式 | `POST /api/v1/ime/backfill/start` | Bearer | 一次性 |
| 上传 backfill 分片 | `POST /api/v1/ime/backfill/chunk` | Bearer | 多次 |
| 标记 backfill 完成 | `POST /api/v1/ime/backfill/complete` | Bearer | 一次性 |
| 增量批量上传 | `POST /api/v1/ime/ingest` | Bearer | 持续 |
| 心跳 + 拉删除通告 | `POST /api/v1/ime/heartbeat` | Bearer | ≤ 1/h |
| 删除本人云端数据（范围） | `DELETE /api/v1/me/inputs?from=&to=` | Bearer **或** Session | 用户触发 |
| 删除本人云端数据（全部） | `DELETE /api/v1/me/inputs/all` | Bearer **或** Session | 用户触发 |
| 导出本人云端数据 | `GET /api/v1/me/inputs/export` | Bearer **或** Session | 用户触发 |
| 跨机回灌（拉云端→本地） | `GET /api/v1/me/inputs/since?cursor=` | Bearer | 用户触发 |

**鉴权 header**：`Authorization: Bearer <access_token>`（access_token 来自 ektro-link-protocol.md §4.3）。

---

## 2. Payload 字段白名单（不在白名单的字段服务端忽略）

```
commit:
  device_id           string (UUID)         必填
  client_ts           integer (ms)          必填
  input_raw           string                必填
  output              string                必填
  user_picked         integer (0|1)         必填
  duration_ms         integer | null        可选
  app_name            string | null         可选, 默认 null (用户开启上传才填)
  content_hash        string (hex)          必填, 客户端计算

aggregate_word:
  device_id           string
  word                string (单字符)
  count               integer
  last_used_ts        integer (ms)
  content_hash        string

aggregate_phrase:
  device_id           string
  prev                string (单字符)
  curr                string (单字符)
  count               integer
  content_hash        string
```

**禁止字段**（服务端收到立即拒 422）：
- `id`（服务端自己生成；客户端的 commit_log.id 仅作 cursor）
- `context_id`（本地概念，服务端不接受）
- `password*` / `bankcard*` / `idcard*` / `email*` 等任意前缀字段
- 任何未在白名单内的字段名

**content_hash 计算公式**（客户端必须严格按此实现）：

```python
def content_hash_commit(device_id, client_ts, input_raw, output) -> str:
    payload = f"ime|commit|{device_id}|{client_ts}|{input_raw}|{output}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def content_hash_word(device_id, word) -> str:
    return hashlib.sha256(f"ime|word|{device_id}|{word}".encode("utf-8")).hexdigest()

def content_hash_phrase(device_id, prev, curr) -> str:
    return hashlib.sha256(f"ime|phrase|{device_id}|{prev}|{curr}".encode("utf-8")).hexdigest()
```

`agent_slug` 由服务端从 access_token 中的 `sub`（user_id）解析对应 personal Twin slug 填入，**客户端不传**。

---

## 3. Backfill 流程（首次链接后一次性）

### 3.1 POST /api/v1/ime/backfill/start

**Body**：

```json
{
  "device_id": "<uuid>",
  "mode": "full",                  // "full" | "aggregate" | "none"
  "total_commits": 12483,          // 仅 full 模式必填
  "total_words": 2156,             // 仅 aggregate 模式必填
  "total_phrases": 8432            // 仅 aggregate 模式必填
}
```

**响应**：

```json
{
  "backfill_id": "<uuid>",         // 服务端生成, 后续 chunk 携带
  "chunk_size_recommended": 500,   // 服务端建议每片条数, 客户端可调
  "max_payload_bytes": 1048576     // 单次 chunk 上限 (1 MB)
}
```

**`mode='none'`**：服务端立即标记 backfill 完成，不需要 chunk 调用，直接进入增量阶段。

### 3.2 POST /api/v1/ime/backfill/chunk

**Body**（full 模式）：

```json
{
  "backfill_id": "<uuid>",
  "device_id": "<uuid>",
  "kind": "commits",
  "items": [
    {
      "device_id": "<uuid>",
      "client_ts": 1747500000000,
      "input_raw": "nihao",
      "output": "你好",
      "user_picked": 0,
      "duration_ms": 450,
      "app_name": null,
      "content_hash": "<sha256 hex>"
    },
    ...
  ]
}
```

**Body**（aggregate 模式）：

```json
{
  "backfill_id": "<uuid>",
  "device_id": "<uuid>",
  "kind": "words",                 // 或 "phrases"
  "items": [
    {"device_id": "<>", "word": "好", "count": 1342, "last_used_ts": 1747500000000, "content_hash": "<>"},
    ...
  ]
}
```

**响应**：

```json
{
  "received": 500,
  "deduplicated": 12,              // 服务端按 content_hash 去重的条数
  "inserted": 488,
  "next_cursor_hint": null         // 服务端目前不强制 cursor, 客户端按本地 id 推进
}
```

**幂等**：同一 `content_hash` 多次上传只算一次（UNIQUE 约束自动）。客户端可放心重传失败的 chunk。

### 3.3 POST /api/v1/ime/backfill/complete

**Body**：

```json
{
  "backfill_id": "<uuid>",
  "device_id": "<uuid>",
  "client_total_uploaded": 12483
}
```

**响应**：

```json
{
  "status": "completed",
  "server_total_received": 12471,  // 可能因去重略小于客户端计数, 不阻断
  "completed_at": 1747788123000
}
```

**完成后**：客户端 UPDATE `backfill_state.completed_at`，sync worker 切到增量模式。

---

## 4. 增量 Ingest（持续同步）

### 4.1 POST /api/v1/ime/ingest

**触发**：客户端每 5 min 或累积 100 条新 commit 触发一次（取先到）。

**Body**：

```json
{
  "device_id": "<uuid>",
  "client_seq": 13042,             // 客户端递增计数, 服务端无校验, 仅审计
  "commits": [
    {
      "device_id": "<uuid>",
      "client_ts": 1747788001000,
      "input_raw": "shijie",
      "output": "世界",
      "user_picked": 0,
      "duration_ms": 320,
      "app_name": null,
      "content_hash": "<sha256 hex>"
    },
    ...
  ]
}
```

**约束**：
- `commits.length` ≤ 200
- 总 payload ≤ 512 KB
- 超限 → 413，客户端拆分

**响应**：

```json
{
  "received": 87,
  "deduplicated": 0,
  "inserted": 87,
  "deletion_notices": []           // 见 §5
}
```

**客户端动作**：
- 200 → `UPDATE sync_cursor SET last_synced_commit_id = MAX(本批 commit_log.id), last_sync_at = now()`
- 401 → 触发 refresh；refresh 失败 → 进入"链接失效"状态
- 403 device_revoked → 清 keyring + 清 device_link.linked_*
- 413 → 把 commits 拆半重试
- 429 → 看 `Retry-After` header（秒），暂停后重试
- 5xx → 指数退避：1s / 2s / 4s / 8s / 16s（最多 5 次），全失败放弃本批，下次 sync 重新捞

---

## 5. Heartbeat + 删除通告（弱一致性返流）

### 5.1 POST /api/v1/ime/heartbeat

**触发**：客户端每小时一次（即便无新 commit 也调用，用于 last_seen_at 更新 + 拉删除通告）。

**Body**：

```json
{
  "device_id": "<uuid>",
  "client_state": {
    "pending_count": 12,
    "total_uploaded": 12570,
    "last_sync_at": 1747788120000
  }
}
```

**响应**：

```json
{
  "server_total_received": 12568,
  "deletion_notices": [
    {
      "range_from_ms": 1746000000000,
      "range_to_ms": 1746086400000,
      "deleted_count": 423,
      "deleted_at": 1747700000000,
      "initiated_by": "user_web"     // "user_web" | "admin" | "compliance"
    }
  ],
  "device_status": "active"          // "active" | "revoked"
}
```

**deletion_notices 的客户端语义**：
- **仅展示用**：在 IME 设置面板 → "云端删除日志"显示一条历史
- **不强制本地动作**（见 §0.1 主从关系）
- 用户在 IME 中可选"也在本地删除这段时间"动作，触发本地 `delete_range(from, to)`

每条 notice 服务端只推送一次（客户端 ack 由 heartbeat 触发自动），后续 heartbeat 不再返。

---

## 6. 用户主动删除（双向，本地→云端是强制的）

### 6.1 IME 端用户删除（本地→云端级联）

客户端流程：

```python
def delete_local_range(start_ms, end_ms):
    # 1. 先调云端（必须成功才删本地，避免云端遗留）
    if device_link.linked_user_id is not None:
        resp = http.delete(
            f"{endpoint}/api/v1/me/inputs?from={start_ms}&to={end_ms}",
            headers={"Authorization": f"Bearer {access_token}"}
        )
        if resp.status_code not in (204, 404):  # 404 = 云端无对应记录, 视同成功
            raise DeleteFailed("云端删除失败，本地未删，请重试或检查网络")
    # 2. 再删本地
    store.delete_range(start_ms, end_ms)
```

**clear_all 同理**，先调 `DELETE /api/v1/me/inputs/all`，再 `store.clear_all()`。

**离线场景**：未链接 → 跳过云端调用直接删本地。已链接但网络断 → 报错，本地不删（一致性优先于可用性，避免云端永远有"幽灵副本"）。

### 6.2 DELETE /api/v1/me/inputs?from=&to=

**Query**：`from`、`to`（unix ms，含端点）。

**鉴权**：Bearer access_token（IME 触发）或 Supabase Auth session（Web 触发）。

**服务端动作**：
1. DELETE `external_signals WHERE agent_slug=<from token> AND source_type='ime' AND (payload->>'client_ts')::bigint BETWEEN from AND to`
2. INSERT `ime_deletion_log`（user_id / device_id_initiator / range_from / range_to / deleted_count / deleted_at / initiated_by）
3. **触发** Inngest worker `ime-twin-rebuild`：从 external_signals 重新聚合 Twin 八维度 + 重新生成 archival memory（删除内容不能在 Twin 里残留）
4. 响应 204

**`deleted_count = 0` 也返 204**（幂等）。

### 6.3 DELETE /api/v1/me/inputs/all

**响应同 6.2**，删除范围是该用户所有 `source_type='ime'` 的 external_signals。

**特殊处理**：清空后 Twin 的 archival memory 中所有 `source=ime` 标记的条目级联删除；Twin 八维度回退到 IME-link 之前的值（如有快照）或重新由其他信号源计算。

### 6.4 Web 端用户删除（云端单边，不影响本地）

用户在 `ektroai.com/me/inputs` 点删除区间 → 调 6.2 / 6.3 → 写入 `ime_deletion_log` → 客户端下次 heartbeat 收到 deletion_notice（仅通知，不强制）。

---

## 7. 导出（GDPR / 公民权要求）

### 7.1 GET /api/v1/me/inputs/export

**Query**：
- `format=json`（默认）或 `format=ndjson`（大数据流式）
- `from=&to=`（可选范围）

**鉴权**：Bearer 或 Session。

**响应**（`format=json`）：

```json
{
  "exported_at": 1747788200000,
  "user_id": "<uuid>",
  "agent_slug": "<>",
  "source_type": "ime",
  "total": 12568,
  "range": {"from_ms": null, "to_ms": null},
  "commits": [
    {
      "client_ts": 1747788001000,
      "input_raw": "shijie",
      "output": "世界",
      "user_picked": 0,
      "duration_ms": 320,
      "device_id": "<uuid>",
      "received_at": 1747788001500
    },
    ...
  ]
}
```

**大数据集**：`format=ndjson` 流式响应，每行一个 commit，避免 OOM。

**速率限制**：每用户每天最多 5 次完整导出（防爬虫；用户实际需求每月 1 次足够）。

### 7.2 GET /api/v1/me/inputs/since?cursor=&device_id=

**用途**：跨机迁移（用户在新机器上链接同账号 → 拉云端历史回灌本机 commit_log）。

**Query**：
- `cursor`：服务端 external_signals.arrived_at（ISO 8601）；首次拉取传空
- `device_id`：新机的 device_id（防止把数据回灌到旧机覆盖）
- `limit`：默认 500，max 1000

**响应**：

```json
{
  "items": [
    {
      "client_ts": 1747500000000,
      "input_raw": "nihao",
      "output": "你好",
      "user_picked": 0,
      "duration_ms": 450,
      "origin_device_id": "<旧机 uuid>",
      "received_at": 1746999000000
    }
  ],
  "next_cursor": "2026-05-20T14:32:11.234Z",
  "has_more": true
}
```

客户端按响应顺序 INSERT 到本地 `commit_log`（注意 `id` 由本地新分配，不是云端 id）。回灌完成后正常进入增量模式。

---

## 8. 速率限制

| 端点 | 限额 | 触发响应 |
|------|------|---------|
| `/auth/ime-token` | 10/min/device_id | 429 + Retry-After |
| `/backfill/start` | 5/h/user | 429 |
| `/backfill/chunk` | 60/min/device_id | 429 |
| `/ingest` | 60/min/device_id（默认 5min/次, 留 60× 余量） | 429 |
| `/heartbeat` | 4/h/device_id | 429 |
| `/me/inputs/export` | 5/day/user | 429 |
| `/me/inputs DELETE` | 60/min/user | 429 |

**算法**：sliding window，Redis 计数。

**响应 header**：`Retry-After: <seconds>` + `X-RateLimit-Remaining` + `X-RateLimit-Reset`。

---

## 9. 错误码总表

| HTTP | code | 触发 | 客户端动作 |
|------|------|------|-----------|
| 400 | `invalid_request` | payload schema 错 | 不重试，告 bug |
| 401 | `invalid_token` | token 过期/无效 | refresh 一次；再失败标记失效 |
| 403 | `device_revoked` | 设备已吊销 | 立即清 keyring + device_link |
| 403 | `scope_insufficient` | token scope 非 `ime-ingest` | 不重试，告 bug |
| 404 | `not_found` | 删除范围无对应数据 | 视同成功（幂等） |
| 413 | `payload_too_large` | 单批 > 512KB 或 chunk > 1MB | 拆半重试 |
| 422 | `invalid_payload` | 含禁止字段 / content_hash 不匹配 | 不重试，告 bug |
| 429 | `rate_limit` | 超速 | 按 `Retry-After` 等待 |
| 500 | `server_error` | 服务端故障 | 指数退避，最多 5 次 |
| 503 | `service_unavailable` | 维护中 | 按 `Retry-After` 等待 |

**所有错误响应格式**：

```json
{
  "error": "rate_limit",
  "message": "Too many requests",
  "retry_after": 30,
  "request_id": "req_<>"            // 用于跨仓 debug
}
```

---

## 10. 服务端落地（ektro 仓库）

### 10.1 扩展 `external_signals.source_type`

```sql
-- 类型已是 TEXT, 无需 ALTER. 仅记录新合法值:
-- 'ime'             — commit_log 级别原始数据
-- 'ime_aggregate'   — backfill aggregate 模式的 word/phrase 数据
```

`source_subtype`：`'commit'` / `'word'` / `'phrase'`。

`payload` JSON 结构按 §2 白名单字段存。

### 10.2 新增表

```sql
CREATE TABLE ime_backfills (
  backfill_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id              UUID NOT NULL,
  device_id            UUID NOT NULL,
  mode                 TEXT NOT NULL CHECK (mode IN ('full','aggregate','none')),
  total_expected       INTEGER NOT NULL DEFAULT 0,
  total_received       INTEGER NOT NULL DEFAULT 0,
  started_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at         TIMESTAMPTZ
);
CREATE INDEX idx_ime_backfills_user ON ime_backfills(user_id);

CREATE TABLE ime_deletion_log (
  id                   BIGSERIAL PRIMARY KEY,
  user_id              UUID NOT NULL,
  device_id_initiator  UUID,
  range_from_ms        BIGINT,                -- NULL = 全部
  range_to_ms          BIGINT,                -- NULL = 全部
  deleted_count        INTEGER NOT NULL,
  initiated_by         TEXT NOT NULL CHECK (initiated_by IN ('user_ime','user_web','admin','compliance')),
  deleted_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  acknowledged_devices UUID[] NOT NULL DEFAULT '{}'  -- 哪些 device 已通过 heartbeat 收到通告
);
CREATE INDEX idx_ime_deletion_log_user ON ime_deletion_log(user_id, deleted_at DESC);

CREATE TABLE ime_rate_limit (             -- 可选: Redis 不可用时的兜底
  bucket_key           TEXT PRIMARY KEY,    -- "<endpoint>:<device_or_user_id>"
  window_start         TIMESTAMPTZ NOT NULL,
  count                INTEGER NOT NULL
);
```

### 10.3 Inngest worker

| 函数名 | 触发 | 工作 |
|--------|------|------|
| `ime-extract` | `external_signals` 写入 (source_type='ime') | 增量更新 Twin 八维度 + archival_memory |
| `ime-aggregate-extract` | `external_signals` 写入 (source_type='ime_aggregate') | 仅更新 linguistic_style 维度（无原始上下文） |
| `ime-twin-rebuild` | DELETE inputs 后手动 trigger | 从剩余 external_signals 重新聚合 Twin |
| `ime-deletion-notice-sweep` | 每 5 min | 清理 ack 满 30 天的 deletion_log |

### 10.4 与 Twin core identity 的隔离

**写入边界**（强制条件 2 from 宪法预检）：
- IME 信号只写：`twin_dimensions.linguistic_style` / `.topics` / `.pace` / `.daily_rhythm`
- IME 信号**不写**：`twin_dimensions.values` / `.personality` / `.name` / `.tagline`
- archival_memory 写入时 metadata 标 `source='ime'`，可单独筛除/删除

实现位置：`src/domains/twin/extractors/ime.ts`（待建）。

---

## 11. 实施清单

### ektro-pen 客户端

- [ ] `src/sync/uploader.py`：增量 sync worker（守护进程 / 后台线程）
- [ ] `src/sync/backfill.py`：三模式回填，断点续传
- [ ] `src/sync/hasher.py`：content_hash 三个函数
- [ ] `src/sync/heartbeat.py`：每小时 heartbeat + deletion_notice 处理
- [ ] `src/api/inputs.py`：用户主动删除/导出/跨机回灌
- [ ] C++ 对应实现（Phase 2）
- [ ] 单元测试：去重、断点续传、错误退避、删除级联

### ektro 服务端

- [ ] 9 个 API 路由
- [ ] 3 张新表 migration
- [ ] 4 个 Inngest worker
- [ ] `external_signals` source_type='ime' 写入路径 + content_hash UNIQUE 约束验证
- [ ] Rate limit 中间件（Redis sliding window）
- [ ] 集成测试：完整 backfill / 增量 / 删除级联 / Twin 重建幂等 / 速率限制

---

**本文档定稿后**，下一份是 [privacy-boundary.md](./privacy-boundary.md)（隐私边界铁律：白名单/黑名单的最终冻结清单 + 三层拦截的实施验证 + 服务条款语言模板）。
