# 隐私边界铁律

> ektro-pen + ektroai.com 的隐私契约。**冻结清单**——任何破坏性修改需走宪法第九条修宪程序。
> 服务条款 / 同意页 / 隐私政策 / 公开文档使用的语言以本文件 §6 为准，禁止自由演绎。
>
> 关联文档：
> - [local-memory-schema.md](./local-memory-schema.md) — 字段分级源
> - [ektro-link-protocol.md](./ektro-link-protocol.md) — 同意页文案
> - [ime-ingest-contract.md](./ime-ingest-contract.md) — 字段白名单源
>
> Ektro 宪法依据：[CONSTITUTION.md](../../ektro/CONSTITUTION.md)（第三条 · 公民六权 / 第五条 · 五条禁令 / 第六条 · 时间资产 / 第八条 · 提案合宪性检查）

---

## 0. 三条铁律（用户可看见的版本）

```
   ┌──────────────────────────────────────────────────────┐
   │                                                      │
   │   ① 记忆属于你                                       │
   │      本地 SQLite 是 source of truth，明文、可视、     │
   │      可导出、可删除。卸载即随机器一并消失。           │
   │                                                      │
   │   ② 上传是选择，不是默认                              │
   │      不登入 ektro，ektro-pen 永远不联网传一个字。     │
   │      链接 ektro 之后，每条上传都是为你的 Twin。       │
   │                                                      │
   │   ③ 你的字只喂养你的 Twin                            │
   │      不被出售。不进入公共训练集。不与第三方共享。      │
   │      违反此条等于违反 Ektro 宪法第五条禁令②。        │
   │                                                      │
   └──────────────────────────────────────────────────────┘
```

---

## 1. 三层拦截（永不进 SQLite 的内容）

| 层 | 信号 | 客户端实现 | 冻结状态 |
|----|------|-----------|---------|
| L1 · 权威 | `is_password_field=True`（来自 Windows TSF `IS_PASSWORD` scope / macOS `kSecAttrIsInvisible` 等价 scope） | [src/memory/store.py:187](../src/memory/store.py#L187) | ✓ 已实施 |
| L2 · 启发 | `input_raw` 命中正则：银行卡 16-19 位 / 中国身份证 18 位 / email 地址 | [src/memory/store.py:133](../src/memory/store.py#L133) | ✓ 已实施 |
| L3 · 用户 | `app_name` 在 `privacy_exclude` 表 | [src/memory/store.py:154](../src/memory/store.py#L154) | ✓ 已实施 |

**任一命中**：`log_commit` 返回 `LogResult.SKIPPED_*`，**不写本地 SQLite**，因此不会进入上传管道。

**拦截清单的扩展**：
- 添加新规则需更新本文件 + 通过单元测试
- 减少规则需走修宪程序（破坏既有承诺）
- L2 启发正则误伤率应 ≤ 1%（每季度抽样校准）

---

## 2. 上传字段白名单（冻结）

**ektro-pen 客户端只允许上传以下字段**——其他任何字段服务端收到立即 `422 invalid_payload`：

### 2.1 commit kind

| 字段 | 必传 | 默认行为 | 说明 |
|------|------|---------|------|
| `device_id` | ✓ | 上传 | UUIDv4，仅作来源标识 |
| `client_ts` | ✓ | 上传 | unix ms |
| `input_raw` | ✓ | 上传 | 拼音原文 |
| `output` | ✓ | 上传 | 上屏中文 |
| `user_picked` | ✓ | 上传 | 0/1 |
| `duration_ms` | 可选 | 上传 | 节奏信号 |
| `content_hash` | ✓ | 上传 | 去重 |
| `app_name` | 可选 | **默认不上传** | 用户在 IME 设置主动开启才上传 |

### 2.2 aggregate_word / aggregate_phrase kind

仅 backfill `mode='aggregate'` 阶段使用，字段限定见 [ime-ingest-contract.md §2](./ime-ingest-contract.md)。

### 2.3 永不上传清单

```
本地必存                  上传白名单外，服务端拒收
─────────────────────────────────────────────
commit_log.id             (服务端自分配 id)
commit_log.context_id     (本地 rerank 用)
word_freq / phrase_pair   (除 aggregate backfill 外)
privacy_exclude.*         (隐私配置, 本地永远)
config.*                  (本地偏好)
device_link.linked_*      (服务端已有自己的源)
sync_cursor.*             (本地状态)
backfill_state.*          (本地状态)
keyring 中的 token        (凭证, 不是用户数据)
任何未在 §2.1/§2.2 出现的字段
```

---

## 3. 数据使用契约（云端侧）

ektro 接收的 IME 数据**只能用于以下用途**：

| 允许 | 说明 |
|------|------|
| ✓ 喂养该用户本人的 Twin 八维度（仅 linguistic_style / topics / pace / daily_rhythm 子集） | [ime-ingest-contract.md §10.4](./ime-ingest-contract.md) 强制隔离 |
| ✓ 写入该用户本人 Twin 的 archival memory（pgvector） | metadata 标 `source='ime'` 便于隔离查询/删除 |
| ✓ 服务端审计日志（操作来源 / 速率限制 / 安全分析） | 仅元数据，不含 input_raw/output 内容 |
| ✓ 该用户主动调用的导出 API（GDPR / 公民权履行） | 完整数据返还该用户 |

**禁止用途**（违反任一即触犯宪法第五条禁令②）：

| 禁止 | 边界 |
|------|------|
| ❌ 出售给第三方 | 任何形式 — 数据库导出 / API 卖给 LLM 训练公司 / 数据经纪 |
| ❌ 用于训练公共/共享模型 | 包括 Ektro 自家的非个人化模型 |
| ❌ 跨用户聚合分析 | 例如"全平台最高频词" — 即使匿名化也禁止 |
| ❌ 写入他人 Twin | IME 数据严格 scope 到 owner user_id |
| ❌ 写入 Twin 的 core identity 维度（values / personality / name） | 见 [ime-ingest-contract.md §10.4](./ime-ingest-contract.md) |
| ❌ 跨账号关联（即便同 device_id） | device_link 单行约束 + 解绑后旧账号数据封存 |

---

## 4. 用户控制权清单（公民六权落地）

| 权利 | 客户端入口 | 云端入口 | 实现位置 |
|------|-----------|----------|---------|
| 看 | 任意 SQLite 客户端打开 `ektro.db` | `ektroai.com/me/inputs` 看板 | 本地永远；云端待 UI |
| 导出 | IME 设置"导出全部" | `GET /api/v1/me/inputs/export` | [store.py:383](../src/memory/store.py#L383)；云端 API 待实施 |
| 删除（区间） | IME 设置"删除时间范围" | `DELETE /api/v1/me/inputs?from=&to=` | [store.py:447](../src/memory/store.py#L447)；云端 API 待实施 |
| 删除（全部） | IME 设置"清空全部" | `DELETE /api/v1/me/inputs/all` | [store.py:425](../src/memory/store.py#L425)；云端 API 待实施 |
| 解绑设备 | IME 设置"解绑" | `ektroai.com/me/devices` 列表行操作 | 待实施 |
| 跨机迁移 | 新机链接同账号 → 自动拉回灌 | — | `GET /api/v1/me/inputs/since` |
| 退籍带走 | 见 ektro 主仓 `/me/citizenship/export` | 同左 | 主仓现有功能 |

**响应时间 SLO**：
- 本地操作 < 1 秒
- 云端删除（包括 Twin 重建）< 30 秒
- 导出（< 10 万条）< 10 秒；更大数据集 → ndjson 流式

---

## 5. 删除的级联语义（修正版）

**本地是 source of truth；云端是 Twin 训练副本**。删除是定向的，不是对称的：

```
   操作                  本地       云端       Twin
   ─────────────────────────────────────────────
   IME 端删区间          删 ✓      级联删 ✓   重建 ✓
   IME 端 clear_all      删 ✓      级联删 ✓   重置 ✓
   Web 端删区间          不动       删 ✓       重建 ✓
   Web 端 clear all      不动       删 ✓       重置 ✓
   解绑设备              保留       保留       停止增量喂养
   退籍                  保留       全删       Twin 一并删除
   IME 卸载              随系统卸载 保留       继续存在但不再喂养
```

**理由**：
- 本地是用户的私人记忆主权，云端没有权力反向删除
- 云端是用户授权 Twin 学习的训练样本，用户随时可单独清理而不波及本地
- 两边删除是**用户的两个不同决定**，工具不应替用户做并集

**唯一双向强制**：解绑设备时清 keyring（凭证），其他数据各归各管。

---

## 6. 服务条款语言模板（中英对照，禁止演绎）

### 6.1 简明同意页（链接流程展示）

**中文**：

> EKTRO 输入法（设备：`{device_label}`）请求链接到你的 ektro 账号。
>
> 链接后会发生：
> - 你每天打的字将异步上传到 ektroai.com，**仅用于喂养你的 Twin**
> - 你随时可在 ektroai.com 或 IME 中查看、导出、删除、解绑
>
> 链接后**不会发生**：
> - 你的数据不会被出售
> - 你的数据不会用于训练任何公共/共享模型
> - 你的数据不会被任何其他用户访问
>
> 你也可以选择不链接 —— ektro-pen 离线状态下功能完整。

**English**：

> EKTRO IME (device: `{device_label}`) requests to link to your ektro account.
>
> After linking:
> - Every character you type will be uploaded asynchronously to ektroai.com, **solely to feed your own Twin**.
> - You may view, export, delete, or unlink at any time in either ektroai.com or the IME.
>
> After linking, the following will **not** happen:
> - Your data will not be sold.
> - Your data will not train any shared or public model.
> - Your data will not be accessible to any other user.
>
> You may also choose not to link — ektro-pen works fully offline.

### 6.2 IME 启动横幅（首次安装后展示一次）

**中文**：
> 欢迎使用 EKTRO 输入法。
> 你打的每个字都被本机记下来——明文、可导出、可删除。
> 数据离开本机仅在你主动登入 ektro 之后；卸载 IME = 这份记忆随机器消失。

**English**：
> Welcome to EKTRO.
> Every character you type is recorded on this machine — plaintext, exportable, deletable.
> Data leaves your machine only after you choose to link an ektro account; uninstalling removes everything.

### 6.3 隐私政策段落（嵌入 ektroai.com 隐私页）

> **关于 EKTRO 输入法（ektro-pen）数据**
>
> 当你链接 ektro-pen 到 ektro 账号后：
> 1. 你的本地输入数据会以异步、可中断的方式上传到 ektroai.com，**唯一目的是喂养你本人的数字分身（Twin）**。
> 2. 我们承诺**永不**：将该数据出售、与第三方共享、用于训练任何非你本人的模型、跨用户聚合或对外提供。
> 3. 你可以随时在 `ektroai.com/me/inputs` 查看完整云端数据、按时间范围或全部删除、导出为 JSON。
> 4. 你可以随时在 IME 设置或 `ektroai.com/me/devices` 解绑设备。解绑后停止上传，但已上传数据仍可由你单独管理。
> 5. 卸载 IME 不会自动删除已上传到云端的数据 —— 那需要你主动调用清空 API 或退籍。
>
> 我们将本承诺写入 Ektro 宪法第五条禁令②，违反等同违宪，承担公开复盘责任。

---

## 7. 内部安全要求（实施侧，非用户可见）

| 项 | 要求 |
|----|------|
| 传输加密 | 所有客户端↔云端通信强制 TLS 1.2+，HSTS preload |
| 静态加密 | `external_signals.payload` 含原文 → Postgres 静态加密（Supabase 默认 AES-256） |
| 访问控制 | RLS：`external_signals` 行级安全策略，`agent_slug` 必须对应当前 session 的 user_id 才可读 |
| 审计 | 所有 IME 上传/删除/导出操作进 `audit_log`，含 actor / device_id / endpoint / timestamp |
| 内部访问 | 工程师查询生产数据库的任何含 `source_type='ime'` 行需走 break-glass 流程（审批 + 留痕） |
| 备份 | 每日全量备份；用户删除请求必须级联 invalidate 受影响时间窗的备份索引 |
| 数据驻留 | 默认 US-East（Supabase）；CN 用户走 ICP 备案的国内副本（Phase 2 规划） |
| Token 存储 | refresh_token 服务端只存 sha256(token)，明文用完即弃 |
| 日志脱敏 | 应用日志中 access_token / refresh_token / input_raw / output 字段强制 `***redacted***` |

---

## 8. 第三方依赖隐私评估

| 依赖 | 用途 | 数据接触 | 风险等级 |
|------|------|---------|----------|
| Supabase（Postgres + Auth） | 主存储 + 用户会话 | 全部用户数据 | 已签 DPA，US East 1 |
| Vercel | API 路由托管 | 请求路径 + headers，**不含 body 持久化** | 低 |
| Inngest | 异步 worker | payload 在执行期短暂可见 | 中（自托管选项规划中） |
| OpenRouter / Anthropic / OpenAI | Twin 八维度抽取 LLM 调用 | input/output 内容会送 LLM | 高 — 需禁止 retention，定期审计 |
| Sentry | 错误监控 | 错误堆栈（受 SDK 配置约束） | 低 — IME 字段全部加入 PII scrubbing 列表 |

**LLM 厂商红线**：所有送 LLM 的 prompt 必须使用 `headers: {"X-No-Retention": "true"}` 或厂商等效设置；定期（每季度）从厂商账户面板验证 retention 状态。

---

## 9. 违规处置

宪法第五条禁令②（不出售 AI 公民的数据）违反 → 强制公开复盘 + 受影响用户单独通知 + 立法层修订（修宪程序）。

本文件 §3 任一"禁止用途"被发现执行 → 视同违反禁令②。

**未来引入新数据用途**：必须修订本文件 §3，且至少在影响生效前 30 天通过 ektroai.com 用户面板告知，给用户解绑/退籍窗口。

---

## 10. 文档审计

| 字段 / 规则 | 引用方 |
|------------|--------|
| §1 三层拦截 | local-memory-schema.md §4；store.py 单元测试 |
| §2 上传白名单 | ime-ingest-contract.md §2；服务端 payload 校验 |
| §3 数据使用契约 | ime-ingest-contract.md §10.4；Inngest worker `ime-extract` |
| §5 删除级联语义 | ime-ingest-contract.md §6；schema doc §5（待回头同步修订） |
| §6 同意页文案 | ektro-link-protocol.md §3 step 6 |

**任一引用方与本文件描述不一致即视为 bug**，以本文件为准。本文件修改必须同步更新所有引用方。

---

**5 份规范文档至此完成**。下一步在 ektro 仓库走 `openspec-propose` 开 change `ime-twin-link`，把以上 4 份文档作为 design 资产引用，生成 tasks。
