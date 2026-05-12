"""
EktroBaselineReranker 性能 benchmark.

SLO 对照 (design.md §6):
  EktroRerankFilter 总预算 30ms
    ├─ SQLite 查询: ≤ 1ms (Week 2 测过)
    ├─ Reranker 主逻辑: ≤ 3ms     ★ 本 benchmark 验证这个
    └─ 余下 26ms 留给 GRU forward (Week 4 进阶任务)
"""
from __future__ import annotations

import io
import random
import statistics
import sys
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from memory.store import EktroMemoryStore  # noqa: E402
from rerank.baseline import EktroBaselineReranker  # noqa: E402
from shared.context_builder import build_context  # noqa: E402


def percentile(xs, p):
    s = sorted(xs)
    return s[min(len(s) - 1, int(len(s) * p))]


def summarize(name, samples_ms, slo_ms=None):
    p50 = percentile(samples_ms, 0.50)
    p95 = percentile(samples_ms, 0.95)
    p99 = percentile(samples_ms, 0.99)
    line = f"{name:45s}  n={len(samples_ms):4d}  P50={p50:6.3f}  P95={p95:6.3f}  P99={p99:6.3f}  max={max(samples_ms):7.3f}"
    if slo_ms is not None:
        verdict = "✅" if p95 <= slo_ms else "🔴"
        line += f"  SLO ≤{slo_ms}ms: {verdict}"
    print(line)


# 测试候选样例（来自真实拼音歧义场景）
CANDIDATE_SETS = {
    "5_cands_short": ["你好", "尼浩", "妮好", "你号", "拟好"],
    "10_cands_mixed": ["我今天", "我金天", "我经天", "握今天",
                       "卧今天", "我尽天", "雾今天", "我槿天",
                       "我巾天", "捂今天"],
    "20_cands_dense": [f"候选{i}号" for i in range(20)],
}


def setup_store_with_data(n_commits: int = 1000) -> EktroMemoryStore:
    """填一些真实数据让 reranker 有东西查。"""
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = EktroMemoryStore(tmp.name)

    rnd = random.Random(42)
    # 真实风格的高频字
    common_outputs = [
        "你好", "今天", "明天", "天气", "怎么样",
        "我要", "去公司", "回家", "吃饭", "工作",
        "代码", "项目", "需求", "设计", "实现",
        "咖啡", "拿铁", "美式", "V60", "手冲",
        "笔记", "记录", "想法", "灵感", "创意",
    ]
    now = int(time.time() * 1000)
    for i in range(n_commits):
        out = rnd.choice(common_outputs)
        store.log_commit(f"input{i}", out, timestamp=now - i * 1000)
    return store


def bench_rerank(reranker: EktroBaselineReranker, candidates: list, context: str, reps: int):
    samples = []
    for _ in range(reps):
        start = time.perf_counter()
        reranker.rerank(candidates, context=context)
        samples.append((time.perf_counter() - start) * 1000)
    return samples


def main():
    print("=" * 95)
    print("EktroBaselineReranker - Performance Benchmark")
    print("=" * 95)
    store = setup_store_with_data(n_commits=1000)
    reranker = EktroBaselineReranker(store)
    print(f"Store: 1000 commits, {store.stats()['unique_chars']} unique chars, "
          f"{store.stats()['unique_phrase_pairs']} unique pairs\n")

    contexts = {
        "no_context": "",
        "short_context": "我今天打开电脑",
        "long_context": "我今天打开电脑想写一段关于咖啡的笔记。早上喝了一杯V60手冲非常",
    }

    print("Phase 1: 不同候选数 × 不同上下文")
    print("-" * 95)
    for cands_name, cands in CANDIDATE_SETS.items():
        for ctx_name, ctx in contexts.items():
            label = f"{cands_name} + {ctx_name}"
            samples = bench_rerank(reranker, cands, ctx, reps=500)
            summarize(label, samples, slo_ms=3.0)

    print("\nPhase 2: 端到端含 build_context")
    print("-" * 95)
    samples = []
    for _ in range(500):
        start = time.perf_counter()
        ctx = build_context(store, cursor_prefix="我今天")
        reranker.rerank(CANDIDATE_SETS["10_cands_mixed"], context=ctx)
        samples.append((time.perf_counter() - start) * 1000)
    summarize("build_context + rerank(10)", samples, slo_ms=4.0)

    print("\nPhase 3: 高基数测试 (50/100 候选)")
    print("-" * 95)
    for n in [50, 100]:
        cands = [f"候选{i}号" for i in range(n)]
        samples = bench_rerank(reranker, cands, "上下文测试", reps=200)
        summarize(f"{n}_cands", samples, slo_ms=5.0)

    store.close()
    print("\nSLO 对照 (design.md §6):")
    print("  • EktroRerankFilter SQLite + reranker: ≤ 3ms target")
    print("  • 实际生产中 librime 通常给 10-20 个候选 (5_cands / 10_cands 是真实场景)")


if __name__ == "__main__":
    main()
