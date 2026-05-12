"""
EKTRO 端到端 demo: 给定拼音 → librime 候选（模拟）→ 个性化重排 → 显示结果。

这个 demo 把目前所有模块串起来，演示"惊艳"的核心体验。
后续 Week 5 Qwen3 predictor 集成后，加上"下一句预测"。

运行:
    cd E:\\CLAUDE\\EKTRO输入法
    set PYTHONPATH=src
    python tests/rerank/demo_pipeline.py
"""
from __future__ import annotations

import io
import sys
import tempfile
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from memory.store import EktroMemoryStore
from rerank.baseline import EktroBaselineReranker
from shared.context_builder import build_context


def banner(text: str):
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70)


def show_candidates(title: str, candidates: list, ranked=None):
    print(f"\n{title}")
    print("-" * 50)
    if ranked is None:
        # 显示原始 librime 顺序
        for i, c in enumerate(candidates):
            marker = "★" if i == 0 else " "
            print(f"  {marker}  {i+1}. {c}")
    else:
        # 显示重排后
        for r in ranked:
            moved = ""
            if r.new_rank != r.base_rank:
                arrow = "↑" if r.new_rank < r.base_rank else "↓"
                moved = f" {arrow}{abs(r.new_rank - r.base_rank)}"
            marker = "★" if r.new_rank == 0 else " "
            print(f"  {marker}  {r.new_rank+1}. {r.candidate:12s}  "
                  f"score={r.score:6.3f} (base={r.base_rank+1}{moved})")


def main():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()

    store = EktroMemoryStore(tmp.name)
    reranker = EktroBaselineReranker(store)

    banner("EKTRO 端到端演示 — '记忆驱动的拼音输入法'")
    print("""
本 demo 模拟一个真实使用场景:
  1. 用户首次使用 → 候选用 librime 默认排序
  2. 用户输入一段咖啡笔记 → 系统学习
  3. 再次输入相关内容 → 候选被个性化重排
  4. 上下文改变 → 重排逻辑跟着变
""")

    # ────────── 场景 1: 首次使用 ──────────
    banner("场景 1: 首次使用 - 用户库为空")
    candidates_v1 = ["你好世界", "你好十届", "泥嚎诗节", "你好诗节"]
    show_candidates("librime 原始候选 (打 'nihaoshijie'):", candidates_v1)
    ranked = reranker.rerank(candidates_v1)
    show_candidates("EKTRO 重排 (无用户数据，保持原序):", candidates_v1, ranked)

    # ────────── 场景 2: 训练: 用户写咖啡笔记 ──────────
    banner("场景 2: 用户写一段咖啡笔记 (训练数据)")
    coffee_session = [
        ("zaoshang", "早上"),
        ("hele", "喝了"),
        ("yibei", "一杯"),
        ("kafei", "咖啡"),
        ("zhenhao", "真好"),
        ("wo", "我"),
        ("xihuan", "喜欢"),
        ("kafei", "咖啡"),
        ("dedouzi", "的豆子"),
        ("xiang", "香"),
        ("v60", "V60"),
        ("shouchong", "手冲"),
        ("kafei", "咖啡"),
        ("zuixihuan", "最喜欢"),
        ("naitie", "拿铁"),
    ]
    print("\n用户连续 commit (模拟一段写作时间):")
    for pinyin, output in coffee_session:
        store.log_commit(pinyin, output, app_name="Code.exe")
        print(f"  '{pinyin}' → '{output}'")
    stats = store.stats()
    print(f"\n现在记忆库：{stats['total_commits']} commits, "
          f"{stats['unique_chars']} unique chars, "
          f"{stats['unique_phrase_pairs']} unique pairs")

    # ────────── 场景 3: 同主题继续输入 → 个性化生效 ──────────
    banner("场景 3: 同主题继续 - 'wozhongyu...'")
    candidates_v2 = ["我终于", "我中於", "握终于", "卧终于"]
    context_v2 = build_context(store, cursor_prefix="今天我又喝了一杯咖啡")
    print(f"\n构造上下文 (前文 + 最近输入):")
    print(f"  > {context_v2}")

    show_candidates("librime 原始候选:", candidates_v2)
    ranked = reranker.rerank(candidates_v2, context=context_v2)
    show_candidates("EKTRO 个性化重排:", candidates_v2, ranked)

    # ────────── 场景 4: 二元组接续 ──────────
    banner("场景 4: 二元组接续 - context='我要买咖' → 'fei' 候选")
    # 给用户加一些 "咖啡" 二元组
    for _ in range(10):
        store.log_commit("kafei", "咖啡")
    candidates_v4 = ["非常", "啡", "废铁", "肥沃", "翡翠"]
    context_v4 = "我要买咖"
    show_candidates(f"librime 原始候选 (context='{context_v4}'):", candidates_v4)
    ranked = reranker.rerank(candidates_v4, context=context_v4)
    show_candidates("EKTRO 重排 (期望 '啡' 因二元组靠前):", candidates_v4, ranked)

    # ────────── 场景 5: 性能 demo ──────────
    banner("场景 5: 性能演示 - 1000 次连续 rerank")
    candidates_perf = ["你好", "尼浩", "妮好", "你号", "拟好"]
    context_perf = "今天我打开电脑"
    start = time.perf_counter()
    for _ in range(1000):
        reranker.rerank(candidates_perf, context=context_perf)
    elapsed = (time.perf_counter() - start) * 1000
    print(f"\n1000 次 rerank 总耗时: {elapsed:.1f} ms")
    print(f"平均每次: {elapsed/1000:.3f} ms")
    print(f"SLO (≤3ms): {'✅ PASS' if elapsed/1000 <= 3 else '🔴 FAIL'}")

    # ────────── 收尾 ──────────
    banner("Demo 完成")
    print(f"""
这个 demo 展示了:
  ✓ 个性化字频驱动重排
  ✓ 二元组接续 (相邻字概率)
  ✓ 上下文字符共现
  ✓ 保留 librime 原始先验 (避免颠覆)
  ✓ 性能远超 SLO

下一步 (Week 5):
  + 集成 llama-server 持久进程 → 接 EktroPredictor
  + Predictor 用同样的 build_context() 拿上下文
  + 用户停顿 300ms 后浮现淡灰下一句
""")

    store.close()
    Path(tmp.name).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
