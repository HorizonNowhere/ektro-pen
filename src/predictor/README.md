# EKTRO Predictor Module

> 下一句预测：用户停顿后浮现淡灰续写，按 Tab 接受。
> 后端：llama-server (Qwen3-0.6B Q4_K_M) HTTP /completion endpoint。

---

## 快速上手

```python
from predictor.client import PredictorClient, PredictorConfig
from predictor.trigger import AsyncTrigger, TriggerConfig

# 1. 同步客户端（一次请求一次响应）
client = PredictorClient(PredictorConfig(
    server_url="http://127.0.0.1:8088",
    timeout_ms=200,
    max_context_chars=50,
))
result = client.predict(prefix="我早上喝了", context="昨天写了咖啡日记")
# result.text → "一杯咖啡" (predicted)
# result.prefill_ms / result.total_ms / result.cache_hit / result.error

# 2. 异步包装器（生产场景）
trigger = AsyncTrigger(
    client,
    on_result=lambda r: render_gray_inline(r.text),
    config=TriggerConfig(pause_ms=300),
)
trigger.start()

# IME 主线程每次按键调用：
trigger.on_keystroke(prefix=current_prefix, context=recent_context)

# 用户接受候选时
trigger.cancel()
```

---

## 架构

```
   IME 主线程                AsyncTrigger              PredictorClient        llama-server
       │                          │                           │                    │
       │ on_keystroke ─→          │                           │                    │
       │   (prefix, context)      │ 记录 pending_task          │                    │
       │                          │ 标记 last_keystroke=now    │                    │
       │                          │                           │                    │
       │                          │ ←── 后台 worker loop:      │                    │
       │                          │     检测停顿 ≥ 300ms       │                    │
       │                          │     → predict() ─────────→ │                    │
       │                          │                           │ POST /completion ─→│
       │                          │                           │ ←─ JSON (≤200ms)  │
       │                          │ ←──── result ─────────────│                    │
       │ ←── on_result(text) ──── │                           │                    │
       │                          │                           │                    │
       │ 渲染淡灰 inline           │                           │                    │
```

---

## 设计决策（来自 decisions log）

### D-004: Server 模式 + 短上下文

**实测**: llama-server 持久进程下：
- ~10 tokens 上下文: P50 56ms, P95 76ms ✅
- ~35 tokens 上下文: P50 142ms, P95 413ms 🟡
- ~124 tokens 上下文: P50 507ms, P95 760ms 🔴

→ `max_context_chars=50` 是边界 SLO 区间的安全选择。

### D-006: 异步非阻塞

**绝不让 LLM 调用阻塞 IME 主路径**。任何 timeout/error 静默放弃。
PredictorClient 的所有异常都封装到 `PredictionResult.error`，调用方不需要 try/except。

### D-007: 复用 context_builder.py

`build_predictor_prompt()` 与 reranker 的 `build_context()` 共用基础设施。
Predictor 不另写一套上下文逻辑。

### LRU 缓存（capacity 128）

同上下文 + 同前缀的请求**只算一次**。常见场景（用户按 Tab 退回，重新停顿）缓存命中率高。

---

## 接口契约

```python
@dataclass
class PredictionResult:
    text: str                        # 预测文本（成功时）
    prompt_tokens: Optional[int]     # server 端 tokenization 长度
    prefill_ms: Optional[float]      # server 端 prefill 时间
    total_ms: float                  # 客户端 wall time（含 HTTP）
    cache_hit: bool = False
    error: Optional[str] = None      # 失败时填充

class PredictorClient:
    def health() -> bool: ...
    def predict(prefix: str, context: str = "") -> PredictionResult: ...
    def clear_cache() -> None: ...
    @property
    def stats: PredictorStats: ...   # 累积运行统计
```

未来 ONNX / 其他后端实现同接口 → 调用方零改动。

---

## 实测性能（Day 1 Spike + 集成 demo）

| Context | Server prefill | Client total | 备注 |
|---------|---------------|--------------|------|
| ~10 tokens (short) | 56ms P50 | ~70ms wall | ✅ 流畅 |
| ~35 tokens (medium) | 142ms P50 | ~160ms wall | ✅ 边界 |
| ~50 chars / ~35 tokens | ~70ms (demo 实测) | ~300ms wall | ✅ |
| Cache hit | — | 0.01 ms | ✅ 极快 |

> Wall 比 server prefill 多 ~100-200ms 主要因为 HTTP roundtrip + JSON 解析。生产中可优化为 long-poll 或 streaming。

---

## 故障降级

```
   场景                          降级行为              用户感知
   ─────────────────────────────────────────────────────────
   llama-server 未启动           健康检查失败          无淡灰续写（功能性退化）
   server 进程崩溃                请求 timeout         超时静默，无报错
   prompt 引起 OOM                500 错误            error 字段填充，UI 不显示
   网络延迟 > timeout_ms          urllib timeout       静默放弃
   用户继续打字                    AsyncTrigger 取消    不浪费 CPU
```

**所有降级路径都不会把异常抛到 IME 主线程**。

---

## 已知问题（留 Cycle 2 优化）

1. **`!!!!!!!!` 退化模式**: 短 prompt + temperature=0 + top_k=1 时，Qwen3 raw `/completion` 偶尔输出标点重复
   - 缓解：Cycle 2 加 system prefix 或换 `/v1/chat/completions`
   - 或：Cycle 2 自训小专用模型（避免 chat-tuned 噪声）
2. **不支持流式输出**: 一次完整请求后返回。可升级到 SSE 流式逐 token 显示。
3. **缓存键含完整 prompt 字符串**: 内存效率 OK（最多 50 chars × 128 = 6.4KB），未来可考虑 hash

---

## C++ 移植规约（Cycle 2 时）

C++ 实现 EktroPredictor 时需对照本模块：

1. **HTTP client**: 用 `cpp-httplib` 或 WinHTTP 调 /completion
2. **AsyncTrigger**: `std::thread` + `std::condition_variable` 实现停顿检测
3. **缓存**: STL `unordered_map` + 双向链表 = LRU
4. **回调线程模型**: 必须把结果 marshal 回 IME 主线程（TSF 调用 ITfRange::SetText 必须在 EditSession 中）
5. **跑等价的 unit test** cross-check 行为一致

---

## 测试

```bash
cd E:\CLAUDE\EKTRO输入法
set PYTHONPATH=src

# 单元测试（用 mock server，无需 llama-server）
python -m unittest tests.predictor.test_client -v
# 期望: 15 tests pass in ~10s

# 真实 server 端到端 demo（需要 llama-server 在 8088）
python tests/integration/demo_full_pipeline.py
```

---

## 相关

- [memory/README.md](../memory/README.md) — 提供历史输入
- [rerank/README.md](../rerank/README.md) — 共用 context_builder
- [docs/decisions.md](../../docs/decisions.md) D-004 / D-006 / D-007 / D-008
- [docs/benchmarks/qwen3-firsttoken.md](../../docs/benchmarks/qwen3-firsttoken.md) — server 模式实测数据
