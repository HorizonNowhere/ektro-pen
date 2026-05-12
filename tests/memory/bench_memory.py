"""
EktroMemoryStore 性能 benchmark.

测的三个关键指标对照 design.md §6 性能预算：
1. 单次 log_commit 延迟（写路径不阻塞输入）
2. recent_outputs(limit=20) 延迟（rerank 上下文查询）
3. word_freq_lookup 批量查询（rerank 候选打分）
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


def percentile(xs: list[float], p: float) -> float:
    s = sorted(xs)
    n = len(s)
    return s[min(n - 1, int(n * p))]


def summarize(name: str, samples_ms: list[float]):
    p50 = percentile(samples_ms, 0.50)
    p95 = percentile(samples_ms, 0.95)
    p99 = percentile(samples_ms, 0.99)
    mx = max(samples_ms)
    mn = min(samples_ms)
    mean = statistics.mean(samples_ms)
    print(f"{name:35s}  n={len(samples_ms):5d}  "
          f"P50={p50:7.3f}ms  P95={p95:7.3f}ms  P99={p99:7.3f}ms  "
          f"min={mn:6.3f}  max={mx:7.3f}  mean={mean:7.3f}")


def bench_insert(store: EktroMemoryStore, n: int):
    """连续 n 次 insert，测每次单独延迟。"""
    samples = []
    for i in range(n):
        start = time.perf_counter()
        store.log_commit(f"input_{i}", f"输出{i % 100}", app_name="Bench.exe")
        samples.append((time.perf_counter() - start) * 1000)
    summarize("log_commit (single)", samples)
    return samples


def bench_recent(store: EktroMemoryStore, queries: int, limit: int):
    samples = []
    for _ in range(queries):
        start = time.perf_counter()
        store.recent_outputs(limit=limit)
        samples.append((time.perf_counter() - start) * 1000)
    summarize(f"recent_outputs(limit={limit})", samples)


def bench_wordfreq(store: EktroMemoryStore, queries: int, batch_size: int):
    # 准备查询词库
    pool = [chr(0x4E00 + i) for i in range(2000)]  # 常用汉字 unicode 范围
    samples = []
    for _ in range(queries):
        batch = random.sample(pool, batch_size)
        start = time.perf_counter()
        store.word_freq_lookup(batch)
        samples.append((time.perf_counter() - start) * 1000)
    summarize(f"word_freq_lookup(batch={batch_size})", samples)


def bench_top_words(store: EktroMemoryStore, queries: int, top_n: int):
    samples = []
    for _ in range(queries):
        start = time.perf_counter()
        store.top_words(limit=top_n)
        samples.append((time.perf_counter() - start) * 1000)
    summarize(f"top_words(limit={top_n})", samples)


def bench_export(store: EktroMemoryStore, runs: int):
    samples = []
    for _ in range(runs):
        start = time.perf_counter()
        data = store.export_all()
        samples.append((time.perf_counter() - start) * 1000)
        size_kb = len(str(data)) // 1024
    summarize(f"export_all (~{size_kb} KB output)", samples)


def main():
    db_path = Path("E:/CLAUDE/EKTRO输入法/tests/memory/bench.db")
    if db_path.exists():
        db_path.unlink()
        for ext in ("-wal", "-shm"):
            p = Path(str(db_path) + ext)
            if p.exists():
                p.unlink()

    store = EktroMemoryStore(db_path)

    print("=" * 90)
    print("EKTRO Memory Store - Performance Benchmark")
    print("=" * 90)
    print(f"DB path: {db_path}")
    print()

    print("Phase 1: 1000 inserts (cold)")
    bench_insert(store, 1000)

    print("\nPhase 2: 4000 more inserts (warm + accumulated index)")
    bench_insert(store, 4000)

    print("\nPhase 3: read-side perf with 5000 commits in DB")
    bench_recent(store, queries=500, limit=20)
    bench_recent(store, queries=500, limit=50)
    bench_recent(store, queries=200, limit=200)

    bench_wordfreq(store, queries=500, batch_size=20)
    bench_wordfreq(store, queries=500, batch_size=50)

    bench_top_words(store, queries=500, top_n=30)
    bench_top_words(store, queries=500, top_n=100)

    print("\nPhase 4: export benchmark")
    bench_export(store, runs=10)

    print("\nPhase 5: DB stats")
    stats = store.stats()
    print(f"  Total commits:        {stats['total_commits']:,}")
    print(f"  Unique chars:         {stats['unique_chars']:,}")
    print(f"  Unique phrase pairs:  {stats['unique_phrase_pairs']:,}")
    print(f"  DB size on disk:      {stats['db_size_bytes']:,} bytes "
          f"({stats['db_size_bytes']/1024/1024:.2f} MB)")

    store.close()
    print("\nSLO 对照 (design.md §6):")
    print("  • EktroRerankFilter 预算 30ms 中, SQLite 查询应 ≤ 3ms")
    print("  • 单次 commit 写应 ≤ 5ms (异步不阻塞 IME)")


if __name__ == "__main__":
    main()
