# EKTRO Rerank Module

> 候选重排：把"通用最优"调整为"对你最优"。
> 当前实现：统计 baseline（无 ML）。未来路径：ONNX GRU。

---

## 当前实现：BaselineReranker

```python
from memory.store import EktroMemoryStore
from rerank.baseline import EktroBaselineReranker
from rerank.context_builder import build_context

store = EktroMemoryStore("user.db")
reranker = EktroBaselineReranker(store)

# 给定 librime 返回的候选 + 上下文
candidates = ["你好世界", "你好十届", "泥嚎诗节"]
context = build_context(store, cursor_prefix="hello")
ranked = reranker.rerank(candidates, context=context)

# ranked[0].candidate 是最优候选
# ranked[0].features 显示各特征贡献（调试用）
```

---

## 算法（4 个特征加权求和）

```
   score(c) =  w₁ · log(user_freq(c) + 1)        # 用户字频
            + w₂ · log(bigram_count + 1)         # 二元组接续 (context末字 → c首字)
            + w₃ · context_overlap_ratio         # 与最近输入字符共现率
            + w₄ · exp(-base_rank / 5)           # 保留 librime 原始排序

   默认权重: w = (1.0, 2.0, 0.5, 3.0)
```

特征解读:

1. **user_freq**: 用户写过多次的字得高分（个性化的核心）
2. **bigram**: "咖啡"末字"啡"接"豆"概率高，则候选"豆..."加分
3. **context_overlap**: 候选与最近输入重叠多 → 同一主题中
4. **rime_prior**: librime 通用模型已经把高频候选排前，我们保留这个先验

权重经过 sanity-check，可在 `RerankerConfig` 调整。

---

## 性能（实测，i5-12400F）

| 场景 | P50 | P95 | P99 | SLO (3ms) |
|------|-----|-----|-----|-----------|
| 5 候选 + 无上下文 | 0.106 ms | 0.177 ms | 0.446 ms | ✅ |
| 10 候选 + 短上下文 | 0.198 ms | 0.551 ms | 1.282 ms | ✅ |
| 10 候选 + 长上下文 | 0.184 ms | 0.376 ms | 0.660 ms | ✅ |
| 20 候选 + 长上下文 | 0.284 ms | 0.605 ms | 1.015 ms | ✅ |
| 100 候选（压力测试） | 1.093 ms | 1.940 ms | 4.282 ms | ✅（5x 余量）|
| **端到端含 build_context** | **0.148 ms** | **0.333 ms** | **0.551 ms** | **✅** |

**真实场景中 librime 通常给 10-20 候选**，所以实际 P95 ≈ 0.5ms。
留给 GRU forward 的预算还有 29ms。

---

## 与神经 ranker 的接口契约

未来切换到 ONNX GRU 时，调用方零改动。两者实现同一接口：

```python
class BaseReranker(Protocol):
    def rerank(
        self,
        candidates: Sequence[str],
        context: str = "",
        recent_outputs: Optional[List[str]] = None,
    ) -> List[RankedCandidate]: ...
```

未来的 `OnnxGRUReranker` 将：
- 读 ONNX 模型一次（启动时）
- 把 candidates + context 转成 token 序列
- 一次 forward pass 拿到每个候选的 logit
- 包装成同样的 `List[RankedCandidate]` 返回

**调用方代码 100% 复用**。这是为什么 Week 4 先做 baseline 的关键原因：**接口稳定，实现可替换**。

---

## 何时升级到神经 ranker？

启动 ONNX GRU 训练当且仅当 baseline 不够好。判断标准：

| 信号 | 来源 | 阈值 |
|------|------|------|
| 用户 Tab 切换率 > 10% | EktroMemoryStore | `user_picked=true` 比例 |
| 长尾词错排率高 | 离线分析 | 历史 commit 与 rerank top1 不符 |
| 主观体验"它不懂我" | 用户反馈 | 手动信号 |

**未到这些阈值前，不投入 GRU 训练成本**（GPU + 训练数据 + 调参 + 部署）。

---

## ContextBuilder

负责把 MemoryStore 数据拼成 reranker / predictor 可用的字符串。

```python
from rerank.context_builder import build_context, build_predictor_prompt

# rerank 上下文（默认 80 字符）
ctx = build_context(store, cursor_prefix="hello")

# predictor 上下文（D-004 限制 ≤50 字符 / ≈35 tokens）
prompt = build_predictor_prompt(store, cursor_prefix="我今天", max_chars=50)
```

为什么 predictor 上下文更短：参见 [`docs/benchmarks/qwen3-firsttoken.md`](../../docs/benchmarks/qwen3-firsttoken.md) 附录 D：
- 35 tokens：P50 142ms（边界 PASS）
- 124 tokens：P50 507ms（超 SLO）

---

## C++ 移植规约（Week 5 weasel filter 集成时）

EKTRO baseline reranker 移植到 librime filter (C++) 时：

1. **特征实现一一对照本 Python 文件**
2. **权重写死或可配置**（YAML 选项）
3. **接口签名**:
   ```cpp
   class EktroRerankFilter : public Filter {
     void Apply(CandidateList* candidates, const Context& ctx) override;
   };
   ```
4. **跑 Python 测试的等价 C++ 测试**（cross-check 行为一致）
5. **不引入第三方库** —— 全用 STL（map / vector / log）

C++ 实现预估 200-300 行，与 Python 版差距主要在样板（class declaration / build system 集成）。

---

## 测试

```bash
cd E:\CLAUDE\EKTRO输入法
set PYTHONPATH=src
python -m unittest tests.rerank.test_baseline -v
python tests/rerank/bench_baseline.py
```

预期：12 tests pass in <1s；benchmark 全部 ✅ SLO。

---

## 相关

- [memory/README.md](../memory/README.md) — store 接口
- [docs/decisions.md](../../docs/decisions.md) D-007 — 为何先做 baseline
- [design.md](../../openspec/changes/ektro-mvp/design.md) §3 §6 — filter 在 librime 管线的位置
