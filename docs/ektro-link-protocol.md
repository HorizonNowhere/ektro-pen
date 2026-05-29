# ektro-link 协议规范

> ektro-pen 设备 ↔ ektroai.com 账号绑定的握手协议。
> 标准来源：[RFC 8252（OAuth 2.0 for Native Apps）](https://datatracker.ietf.org/doc/html/rfc8252) + [RFC 7636（PKCE）](https://datatracker.ietf.org/doc/html/rfc7636)。
> Fallback：[RFC 8628（Device Authorization Grant）](https://datatracker.ietf.org/doc/html/rfc8628)（无浏览器/端口阻塞场景）。
>
> 关联文档：
> - [local-memory-schema.md](./local-memory-schema.md) — `device_link` 表是本协议的客户端落地点
> - [ime-ingest-contract.md](./ime-ingest-contract.md) — 链接成功后使用 access_token 的上传 API
> - [privacy-boundary.md](./privacy-boundary.md) — 链接同意页要明示的权限边界

---

## 0. 立场

链接是**用户主动、单次、可撤销**的动作。

- 未链接：IME 完整工作，纯本地记忆
- 链接中：发生在一次浏览器跳转的 60 秒内，超时即作废
- 已链接：旁路异步上传，不阻塞输入；用户随时可在 IME 或 ektroai.com 解绑

ektro-pen 不在后台静默尝试链接、不在安装时自动唤起。**沉默是默认状态**。

---

## 1. 选型与理由

| 方案 | 适用 | Ektro 选择 |
|------|------|-----------|
| **RFC 8252 Loopback + PKCE**（首选） | 桌面客户端有 GUI 浏览器 | ✓ 主路径 |
| RFC 8628 Device Grant | 无浏览器 / 小屏 / CLI | 降级 fallback |
| 隐式流（Implicit Grant） | 历史方案 | ❌ RFC 8252 明文禁止 |
| Custom URI Scheme（`ektro://`） | 移动 App | ❌ 桌面易被劫持 |

**为什么 Loopback 是桌面 IME 的最佳实践**：
- RFC 8252 §7 明文推荐
- 不需要注册自定义 URI scheme（避免被其他应用劫持）
- 不需要用户手动输 6 位 user code（UX 顺滑）
- PKCE 防止本机其他进程拦截 code

**为什么保留 Device Grant 作为降级**：
- 防火墙阻止 localhost 监听（少数企业终端）
- 系统无图形浏览器（远程 SSH 场景，理论可能）
- 端口全部被占（极端情况）

---

## 2. 端点清单（ektroai.com 侧）

| 用途 | 方法 + 路径 | 鉴权 |
|------|------------|------|
| 链接授权页（浏览器渲染） | `GET /me/ime-link` | Supabase Auth session |
| 链接确认（用户点"允许"） | `POST /me/ime-link/approve` | Supabase Auth session |
| Token 颁发 | `POST /api/v1/auth/ime-token` | PKCE + code |
| Token 刷新 | `POST /api/v1/auth/ime-refresh` | refresh_token |
| Device 自查 | `GET /api/v1/me/devices/:device_id` | access_token |
| Device 解绑 | `POST /api/v1/me/devices/:device_id/revoke` | access_token 或 session |
| Device 列表（用户面板） | `GET /api/v1/me/devices` | Supabase Auth session |
| Device Grant 起始（fallback） | `POST /api/v1/auth/ime-device-code` | 无 |
| Device Grant 轮询（fallback） | `POST /api/v1/auth/ime-device-poll` | device_code |

---

## 3. 主路径完整流程（Loopback + PKCE）

```
┌─ ektro-pen (本机) ────────────┐         ┌─ 浏览器 ─┐    ┌─ ektroai.com ─────┐
│ 用户点"链接 ektro 账号"          │         │           │    │                    │
│ ─────────────────             │         │           │    │                    │
│ 1) 生成 PKCE & state:          │         │           │    │                    │
│    code_verifier = rand(64)    │         │           │    │                    │
│    code_challenge =            │         │           │    │                    │
│      base64url(sha256(verifier))│        │           │    │                    │
│    state = rand(32)            │         │           │    │                    │
│                                │         │           │    │                    │
│ 2) bind 127.0.0.1:0            │         │           │    │                    │
│    → OS 分配端口 P             │         │           │    │                    │
│    起临时 HTTP server          │         │           │    │                    │
│                                │         │           │    │                    │
│ 3) 默认浏览器打开 URL ─────────────────────→            │    │                    │
│                                │         │ GET /me/  │    │                    │
│                                │         │ ime-link  │────→ 4) 校验 session    │
│                                │         │ ?...      │    │    渲染同意页       │
│                                │         │           │←─── 5) 同意页 HTML     │
│                                │         │ 用户点    │    │                    │
│                                │         │"允许"     │    │                    │
│                                │         │           │────→ 6) POST approve   │
│                                │         │           │    │    生成 code (60s) │
│                                │         │           │    │    insert ime_device│
│                                │         │           │←─── 7) 302 重定向       │
│                                │         │           │    │ → 127.0.0.1:P/cb   │
│                                │         │           │    │   ?code=<>&state=<>│
│ 8) 本地 server 收到 cb ←─────────────────────         │    │                    │
│    校验 state                  │         │           │    │                    │
│    立即响应 200 "可关闭浏览器"  │         │           │    │                    │
│    关闭 server                  │         │           │    │                    │
│                                │         │           │    │                    │
│ 9) POST ime-token              │         │           │    │                    │
│    {code, code_verifier,       │─────────────────────────→│ 10) 校验 PKCE       │
│     device_id}                 │         │           │    │     生成 tokens     │
│                                │←──────────────────────── │     {access,refresh}│
│ 11) keyring.set(               │         │           │    │                    │
│       service="ektro-pen",     │         │           │    │                    │
│       account=device_id,       │         │           │    │                    │
│       secret=tokens_json)      │         │           │    │                    │
│                                │         │           │    │                    │
│ 12) UPDATE device_link SET     │         │           │    │                    │
│       linked_user_id, ...      │         │           │    │                    │
│                                │         │           │    │                    │
│ 13) IME 设置弹"链接成功"        │         │           │    │                    │
│     选历史迁移模式              │         │           │    │                    │
└────────────────────────────────┘         └───────────┘    └────────────────────┘
```

**60s 内整套握手必须完成**，超时则 code 作废、IME 关闭本地 server 提示"链接已取消"。

---

## 4. 请求 / 响应契约

### 4.1 GET /me/ime-link（浏览器渲染同意页）

**Query 参数**（全部必填）：

| 参数 | 类型 | 约束 |
|------|------|------|
| `response_type` | string | 固定 `"code"` |
| `client_id` | string | 固定 `"ektro-pen"` |
| `redirect_uri` | string | 必须以 `http://127.0.0.1:` 开头 |
| `state` | string | 客户端生成的 CSRF token，长度 ≥16 |
| `code_challenge` | string | base64url(sha256(code_verifier))，43 字符 |
| `code_challenge_method` | string | 固定 `"S256"`（不接受 `"plain"`） |
| `device_id` | string | UUIDv4，来自客户端 `device_link.device_id` |
| `device_label` | string | 设备显示名（≤64 字符），如 `"test-device"` |
| `scope` | string | 固定 `"ime-ingest"`（未来可扩展，但当前仅此一种） |

**校验**：
- 未登录 Supabase Auth → 重定向到 `/auth/login?next=<原 URL>`
- `redirect_uri` 不匹配 loopback 正则 → 400 拒绝
- `code_challenge_method != S256` → 400 拒绝（RFC 7636 §4.3：S256 强制）

### 4.2 POST /me/ime-link/approve（用户点"允许"）

**Body**：

```json
{
  "csrf_token": "<同意页注入的 nonce>",
  "device_id": "<回显>",
  "code_challenge": "<回显>",
  "redirect_uri": "<回显>",
  "state": "<回显>"
}
```

**服务端动作**：
1. 校验 csrf_token + session
2. 生成 `authorization_code`（32 字节 base64url，**60s** 失效，**单次使用**）
3. INSERT `ime_devices`（device_id / user_id / label / status='pending' / created_at）
4. INSERT `ime_auth_codes`（code / device_id / user_id / code_challenge / redirect_uri / expires_at）
5. 302 → `${redirect_uri}?code=<code>&state=<state>`

**用户拒绝**：302 → `${redirect_uri}?error=access_denied&state=<state>`

### 4.3 POST /api/v1/auth/ime-token

**Content-Type**: `application/json`

**Body**：

```json
{
  "grant_type": "authorization_code",
  "code": "<step 7 拿到的>",
  "code_verifier": "<step 1 生成的 verifier>",
  "device_id": "<device_id>"
}
```

**服务端校验顺序**：
1. `code` 存在 + 未过期 + 未使用 → 否则 `400 invalid_grant`
2. `device_id` 匹配 `ime_auth_codes.device_id` → 否则 `400 invalid_grant`
3. `sha256(code_verifier) base64url == code_challenge` → 否则 `400 invalid_grant`
4. 标记 `code` 已使用（防重放）
5. 更新 `ime_devices.status = 'active'`

**成功响应（200）**：

```json
{
  "access_token": "<JWT, EdDSA 签名>",
  "token_type": "Bearer",
  "expires_in": 3600,
  "refresh_token": "<32 字节 opaque, base64url>",
  "scope": "ime-ingest",
  "device_id": "<回显>",
  "user": {
    "id": "<uuid>",
    "handle": "@testuser"
  }
}
```

**Access Token JWT Claims**：

```json
{
  "iss": "https://ektroai.com",
  "sub": "<user_id>",
  "aud": "ime-ingest",
  "device_id": "<device_id>",
  "scope": "ime-ingest",
  "iat": 1747788000,
  "exp": 1747791600
}
```

**错误响应**：

| HTTP | code | 触发 |
|------|------|------|
| 400 | `invalid_request` | 参数缺失/格式错 |
| 400 | `invalid_grant` | code 过期/已用/不匹配/PKCE 失败 |
| 400 | `unsupported_grant_type` | 不是 `authorization_code` |
| 429 | `rate_limit` | 5 min 内 >10 次尝试同 device_id |

### 4.4 POST /api/v1/auth/ime-refresh

**Body**：

```json
{
  "grant_type": "refresh_token",
  "refresh_token": "<existing>",
  "device_id": "<device_id>"
}
```

**响应**：同 4.3，但 **refresh_token 旋转**（新 refresh_token，旧值立即作废 — 防止 refresh_token 泄露后被重放）。

**Refresh token 寿命**：90 天 sliding window（每次使用后续 90 天）。

### 4.5 POST /api/v1/me/devices/:device_id/revoke

**鉴权**：`Authorization: Bearer <access_token>` 或 Supabase Auth session。

**服务端动作**：
1. UPDATE `ime_devices.status = 'revoked'`, `revoked_at = now()`
2. DELETE `ime_refresh_tokens WHERE device_id = :id`
3. 之后任何携带该 device_id 的 access_token 在 JWT 验证后**额外查 device 状态**，若 `revoked` → 401

**响应**：`204 No Content`。

### 4.6 GET /api/v1/me/devices

**鉴权**：Supabase Auth session（用户在 ektroai.com 面板用）。

**响应**：

```json
{
  "devices": [
    {
      "device_id": "<uuid>",
      "label": "test-device",
      "status": "active",
      "linked_at": 1747500000000,
      "last_seen_at": 1747788123000,
      "total_uploaded": 12483
    }
  ]
}
```

---

## 5. PKCE 参数生成（客户端）

**code_verifier**：

```python
import secrets
code_verifier = secrets.token_urlsafe(48)   # 64 字符 base64url, 满足 RFC 7636 §4.1 (43~128)
```

**code_challenge**：

```python
import hashlib, base64
challenge_bytes = hashlib.sha256(code_verifier.encode("ascii")).digest()
code_challenge = base64.urlsafe_b64encode(challenge_bytes).rstrip(b"=").decode("ascii")
```

**state**：

```python
state = secrets.token_urlsafe(24)
```

**强制**：每次链接都生成新的 verifier/state，绝不复用。verifier 在收到 token 后立即丢弃。

---

## 6. 凭证存储（OS Keyring）

**Service / account 命名约定**：

| 平台 | service | account |
|------|---------|---------|
| Windows Credential Manager | `ektro-pen` | `<device_id>` |
| macOS Keychain | `ektro-pen` | `<device_id>` |
| Linux Secret Service | `ektro-pen` | `<device_id>` |

**存储内容**（JSON 字符串）：

```json
{
  "access_token": "<JWT>",
  "refresh_token": "<opaque>",
  "expires_at": 1747791600000,
  "issued_at": 1747788000000
}
```

**实现库**：

| 客户端 | 库 |
|--------|-----|
| Python | [`keyring`](https://pypi.org/project/keyring/) |
| C++ Windows | `wincred.h` → `CredWriteW` / `CredReadW` / `CredDeleteW` |
| C++ macOS | `Security.framework` → `SecKeychainAddGenericPassword` / `SecKeychainFindGenericPassword` |

**Fallback**（keyring 不可用时）：拒绝链接，提示用户"系统密钥库不可用，无法安全保存凭证"。**禁止**写入明文 SQLite。

---

## 7. Token 生命周期

```
   颁发              过期前 5 min                  90 天 sliding
   ────────────────────────────────────────────────────────────
   access_token  →  自动 refresh  →  新 access + 新 refresh
       │                                          │
       │ 1h 有效                                   │ refresh 也旋转
       │                                          ▼
       │                              继续 sliding 直到用户解绑
       │                                  或 90 天无活动
       │
       └─→ 每次上传 API 调用时校验 exp
            过期 → 自动 refresh 一次重试
            refresh 也失败 → 标记需要重新链接, UI 提示
```

**客户端策略**：
- 上传前检查 `expires_at - now() < 5 min` → 先 refresh
- 任何 401 响应 → 尝试 refresh 一次；仍 401 → 进入"链接已失效"状态，清 keyring，**保留** `device_link` 行用于 UI 提示
- 任何 403 `device_revoked` → 立即清 keyring + 清 `device_link.linked_*`

---

## 8. 解绑（双向）

### 8.1 客户端发起（IME 设置点"解绑"）

1. POST `/api/v1/me/devices/:device_id/revoke`（带当前 access_token）
2. 不论服务端响应 200/4xx/网络失败，本地都执行清理：
   - keyring.delete
   - UPDATE `device_link` SET `linked_user_id=NULL, linked_user_handle=NULL, revoked_at=now()`
3. 停止 sync worker

**理由**：用户主动解绑必须立即生效，不能因服务端不可达而阻塞。服务端漏掉的吊销会在下次任何 API 调用时由"令牌 + 设备状态双验"兜底（见 4.5 末段）。

### 8.2 服务端发起（用户在 ektroai.com 面板解绑）

1. 服务端立即吊销
2. IME 下次同步收到 `403 device_revoked`
3. IME 自动清理（同 8.1 步骤 2-3）
4. UI 提示"该设备已在 ektroai.com 解绑，请重新链接"

---

## 9. 降级路径：Device Authorization Grant（RFC 8628）

**触发条件**（任一）：
- 本地 `bind 127.0.0.1:0` 失败（防火墙 / 端口耗尽）
- 默认浏览器无法启动（用户在 SSH 远程会话）
- 用户在 IME 设置主动选"用其他设备链接"

### 9.1 POST /api/v1/auth/ime-device-code

**Body**：`{ "client_id": "ektro-pen", "device_id": "<uuid>", "device_label": "<>", "scope": "ime-ingest" }`

**响应**：

```json
{
  "device_code": "<opaque, 40 char>",
  "user_code": "WDJB-MJHT",
  "verification_uri": "https://ektroai.com/me/ime-link",
  "verification_uri_complete": "https://ektroai.com/me/ime-link?user_code=WDJB-MJHT",
  "expires_in": 600,
  "interval": 5
}
```

IME 显示 `user_code` 给用户，提示在任意浏览器打开 `verification_uri` 输入此码。

### 9.2 POST /api/v1/auth/ime-device-poll

IME 按 `interval` 秒轮询。

**Body**：`{ "grant_type": "urn:ietf:params:oauth:grant-type:device_code", "device_code": "<>", "client_id": "ektro-pen" }`

**响应**：

| 状态 | error | 含义 |
|------|-------|------|
| 200 | — | 用户已批准，返回与 4.3 相同的 token payload |
| 400 | `authorization_pending` | 继续等待 |
| 400 | `slow_down` | 把 interval 加 5s |
| 400 | `expired_token` | device_code 已过期，重新走 9.1 |
| 400 | `access_denied` | 用户拒绝 |

**降级模式不用 PKCE**（用户已经在另一台机器手动确认了 user_code 的物理映射，相当于带外验证）。

---

## 10. 安全考量

| 威胁 | 缓解 |
|------|------|
| 本机其他进程拦截 authorization_code | PKCE — 拦截者拿不到 code_verifier |
| 钓鱼网站伪造 ektroai.com | 同意页 URL 必须显示在浏览器地址栏，用户自检；客户端 `verification_uri` 硬编码 https://ektroai.com |
| 中间人攻击 | 所有端点强制 HTTPS（HSTS preload），本地 loopback 不需要 HTTPS（RFC 8252 §8.3） |
| 重放 authorization_code | 服务端单次使用 + 60s 过期 |
| Refresh token 泄露 | 每次刷新旋转旧 refresh_token 立即作废 |
| Access token 在客户端日志泄露 | 客户端日志统一脱敏 token 字段（`***redacted***`） |
| 设备被偷 / 主机被入侵 | 用户可在 ektroai.com/me/devices 远程解绑；JWT exp 限 1h 限制 blast radius |
| 端口劫持（其他进程在 IME 启动前占用相同端口） | `bind :0` 随机端口 + 校验 redirect_uri 完整匹配 |
| 弱 PKCE（plain 而非 S256） | 服务端拒绝 `code_challenge_method != S256` |
| 跨账号链接污染 | 单行 `device_link` CHECK，一台机器一个账号 |

---

## 11. ektro 端服务端落地（待实施）

新增表：

```sql
CREATE TABLE ime_devices (
  device_id     UUID PRIMARY KEY,
  user_id       UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  label         TEXT NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('pending', 'active', 'revoked')),
  linked_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  revoked_at    TIMESTAMPTZ,
  last_seen_at  TIMESTAMPTZ
);
CREATE INDEX idx_ime_devices_user ON ime_devices(user_id);

CREATE TABLE ime_auth_codes (
  code              TEXT PRIMARY KEY,
  device_id         UUID NOT NULL,
  user_id           UUID NOT NULL,
  code_challenge    TEXT NOT NULL,
  redirect_uri      TEXT NOT NULL,
  expires_at        TIMESTAMPTZ NOT NULL,
  used_at           TIMESTAMPTZ
);
CREATE INDEX idx_ime_auth_codes_exp ON ime_auth_codes(expires_at);

CREATE TABLE ime_refresh_tokens (
  token_hash    TEXT PRIMARY KEY,            -- 存 sha256(token), 不存明文
  device_id     UUID NOT NULL REFERENCES ime_devices(device_id) ON DELETE CASCADE,
  user_id       UUID NOT NULL,
  issued_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at    TIMESTAMPTZ NOT NULL,
  rotated_to    TEXT                         -- 旋转后指向新 token_hash（审计用）
);
CREATE INDEX idx_ime_refresh_device ON ime_refresh_tokens(device_id);

CREATE TABLE ime_device_codes (              -- Fallback 路径
  device_code   TEXT PRIMARY KEY,
  user_code     TEXT NOT NULL UNIQUE,        -- 8 字符 带 dash 显示
  device_id     UUID NOT NULL,
  user_id       UUID,                        -- 用户批准前 NULL
  status        TEXT NOT NULL CHECK (status IN ('pending','approved','denied','expired')),
  expires_at    TIMESTAMPTZ NOT NULL,
  approved_at   TIMESTAMPTZ
);
CREATE INDEX idx_ime_device_codes_exp ON ime_device_codes(expires_at);
```

JWT 签名密钥：独立 EdDSA keypair，**与 Supabase Auth 的 JWT secret 隔离**。私钥进 Vercel env / ECS env，公钥在 `/.well-known/ektro-pen-jwks.json` 公开（便于客户端校验、第三方审计）。

---

## 12. 实施清单

### ektro-pen 客户端

- [ ] `src/auth/link.py`：PKCE 生成、loopback server、浏览器跳转、token 交换
- [ ] `src/auth/keyring_store.py`：跨平台 keyring 抽象
- [ ] `src/auth/refresh.py`：自动 refresh 守护
- [ ] `src/auth/fallback_device_grant.py`：降级路径
- [ ] C++ 对应实现（Phase 2）
- [ ] IME 设置面板"链接 ektro 账号"按钮
- [ ] 单元测试：PKCE 计算、state 校验、token 旋转、错误恢复

### ektro 服务端

- [ ] 4 张新表 migration
- [ ] 9 个 API 路由 + JWT EdDSA 签发验证
- [ ] 同意页 `/me/ime-link`（需 Pencil 设计）
- [ ] 设备管理页 `/me/devices`（需 Pencil 设计）
- [ ] JWKS 端点
- [ ] Rate limiting（5 min 内同 device_id ≤10 次链接尝试）
- [ ] 集成测试：完整握手 / 拒绝路径 / 过期 / 重放 / 旋转

---

**本文档定稿后**，下一份是 [ime-ingest-contract.md](./ime-ingest-contract.md)（链接成功后实际上传的 API 契约：backfill / ingest payload schema / 去重规则 / 速率限制）。
