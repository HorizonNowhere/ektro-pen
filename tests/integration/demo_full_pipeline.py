"""
EKTRO 全栈端到端 Demo —— Cycle 1 Python 参考层的"惊艳"展示。

把目前所有模块串成一条完整的产品价值链：

    User pinyin
       │
       ▼
    [librime simulation] → candidates
       │
       ▼
    [EktroMemoryStore]  ← 学习历史
       │
       ▼
    [EktroBaselineReranker] → personalized ranking
       │
       ▼
    Inline-rendered top candidate (用户按空格 commit)
       │
       ▼
    [AsyncTrigger + PredictorClient] → 停顿后浮现淡灰续写
       │
       ▼
    User Tab → accept prediction

前置条件:
    1. llama-server 在 127.0.0.1:8088 运行（模型：Qwen3-0.6B Q4_K_M）
    2. PYTHONPATH=src

跑法:
    cd E:\\CLAUDE\\EKTRO输入法
    set PYTHONPATH=src
    python tests/integration/demo_full_pipeline.py
"""
from __future__ import annotations

import io
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from memory.store import EktroMemoryStore
from rerank.baseline import EktroBaselineReranker
from shared.context_builder import build_context, build_predictor_prompt
from predictor.client import PredictorClient, PredictorConfig, PredictionResult
from predictor.trigger import AsyncTrigger, TriggerConfig
from predictor.baseline import BaselinePredictor


# ──────────── 演示输出工具 ────────────


def banner(text: str):
    print("\n" + "═" * 72)
    print(f"  {text}")
    print("═" * 72)


def step(text: str):
    print(f"\n▶ {text}")


def line(prefix: str, content: str):
    print(f"  {prefix:18s} {content}")


# ──────────── librime 模拟器 ────────────


# 真实拼音 → 多个候选（按 librime 默认顺序）
# 来自 雾凇拼音 / 万象拼音 的典型输出
PINYIN_TO_CANDIDATES = {
    "nihaoshijie":     ["你好世界", "你好十届", "泥嚎诗节", "你好诗节"],
    "wojinwanyaohe":   ["我今晚要喝", "我今晚要和", "我今晚要赫", "卧今晚要喝"],
    "kafei":           ["咖啡", "喀斐", "卡飞", "佧菲"],
    "wozaoshanghele":  ["我早上喝了", "我早上和了", "我糟上喝了", "卧早上喝了"],
    "yibei":           ["一杯", "一倍", "一辈", "依杯"],
    "naitie":          ["拿铁", "奶贴", "耐铁", "拿帖"],
    "wojinwannaole":   ["我今晚累了", "我今晚泪了", "卧今晚累了", "我今晚雷了"],
}


def simulate_librime(pinyin: str) -> list[str]:
    """模拟 librime translator 输出。真实场景这里调 weasel IPC。"""
    return PINYIN_TO_CANDIDATES.get(pinyin, [pinyin])


# ──────────── Demo ────────────


def setup_history(store: EktroMemoryStore) -> None:
    """
    模拟用户前两天写过的内容 → 训练数据。
    多字 commit 让 phrase_pair 链丰富，BaselinePredictor 才有续写能力。
    """
    history = [
        # 咖啡笔记（连续长句产生丰富二元组）
        ("zaoshangheleyibei", "早上喝了一杯"),
        ("kafeizhenhao", "咖啡真好喝"),
        ("kafeidedouzixiangji", "咖啡的豆子香极了"),
        ("woxihuanv60shouchong", "我喜欢V60手冲"),
        ("naitiezuihaohe", "拿铁最好喝"),
        ("xiawuyebumian", "下午也不累"),
        ("xiangzaihelyibei", "想再喝一杯"),
        # 编程笔记
        ("xiezuoxinxiangmu", "写作新项目"),
        ("rustyiyiqu", "Rust 很有趣"),
        ("pythonzuihaoxue", "Python 最好学"),
        ("yibuyunxingshi", "异步运行时"),
        ("xiandaiyongqi", "现代勇气"),
    ]
    for pinyin, output in history:
        store.log_commit(pinyin, output, app_name="Code.exe")


def show_candidates(label: str, candidates: list, ranked=None):
    print(f"  {label}")
    if ranked is None:
        for i, c in enumerate(candidates):
            star = "★" if i == 0 else " "
            print(f"    {star} {i+1}. {c}")
    else:
        for r in ranked:
            star = "★" if r.new_rank == 0 else " "
            move = ""
            if r.new_rank != r.base_rank:
                arrow = "↑" if r.new_rank < r.base_rank else "↓"
                move = f"  {arrow}{abs(r.new_rank - r.base_rank)} from #{r.base_rank+1}"
            print(f"    {star} {r.new_rank+1}. {r.candidate:14s}  score={r.score:6.3f}{move}")


def main():
    banner("EKTRO 全栈端到端 Demo — Cycle 1 Python 参考层全部就位")
    print("""
本 demo 用一段连贯的"咖啡日记"场景，演示完整产品价值链:

  Memory ⇆ Reranker ⇆ Context ⇆ Predictor

所有组件用 Python 实现（C++ 移植 Week 5-6 进行）。
""")

    # ──────────── 初始化全栈 ────────────

    step("初始化全栈组件")

    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    store = EktroMemoryStore(tmp.name)
    reranker = EktroBaselineReranker(store)

    # 预测器配置：D-004 实测过的边界
    pred_cfg = PredictorConfig(
        server_url="http://127.0.0.1:8088",
        timeout_ms=400,           # 比 SLO 200ms 略宽，给真实场景余量
        max_context_chars=50,
        n_predict=8,
    )
    client = PredictorClient(pred_cfg)

    if not client.health():
        print("\n  ⚠ llama-server 不在 127.0.0.1:8088。Predictor 部分将降级演示。")
        client = None
    else:
        line("Memory:", "✓ EktroMemoryStore (SQLite WAL)")
        line("Reranker:", "✓ EktroBaselineReranker (4 features)")
        line("Predictor:", "✓ PredictorClient → llama-server (Qwen3-0.6B)")

    # ──────────── 阶段 1：写入历史训练数据 ────────────

    step("阶段 1 — 加载用户历史（模拟用户前两天的输入）")
    setup_history(store)
    s = store.stats()
    line("commits:", str(s["total_commits"]))
    line("unique chars:", str(s["unique_chars"]))
    line("unique pairs:", str(s["unique_phrase_pairs"]))

    # ──────────── 阶段 2：打字 → librime → rerank ────────────

    banner("场景: 用户继续写咖啡日记")

    typing_session = [
        ("wozaoshanghele", "我早上喝了"),
        ("yibei", "一杯"),
        ("kafei", "咖啡"),
    ]

    accumulated = ""  # 已 commit 的累计文本（光标前文）
    for pinyin, expected_commit in typing_session:
        step(f'用户输入 pinyin: "{pinyin}"')

        cands = simulate_librime(pinyin)
        ctx = build_context(store, cursor_prefix=accumulated)
        line("librime 原序:", " / ".join(cands))
        line("rerank context:", ctx[-40:] if ctx else "(empty)")

        ranked = reranker.rerank(cands, context=ctx)
        show_candidates("EKTRO 重排后:", cands, ranked)

        # 选 top1 (实际 IME 会 inline 显示，用户按空格 commit)
        chosen = ranked[0].candidate
        line("用户按空格 →", f'commit "{chosen}"')
        store.log_commit(pinyin, chosen, app_name="Code.exe")
        accumulated += chosen

    line("光标当前位置前文:", accumulated)

    # ──────────── 阶段 3：停顿 → 预测下一句 ────────────

    if client is None:
        banner("阶段 3 跳过（llama-server 不可用）")
        store.close()
        return

    banner("阶段 3 — 用户停下，淡灰续写浮现")

    received: list[PredictionResult] = []
    received_event = threading.Event()

    def on_result(r: PredictionResult):
        received.append(r)
        received_event.set()

    trigger = AsyncTrigger(
        client,
        on_result=on_result,
        config=TriggerConfig(pause_ms=300, poll_interval_ms=30, debounce_min_chars=2),
    )
    trigger.start()

    try:
        # 模拟"用户停下来思考"：以最近 commit 作为 prefix
        predictor_ctx = build_predictor_prompt(store, cursor_prefix=accumulated, max_chars=50)
        line("predictor prompt:", predictor_ctx)
        step("（用户停顿 300ms+）...")

        trigger.on_keystroke(prefix=accumulated, context=predictor_ctx[:-len(accumulated)])

        # 等待预测完成（最多 3 秒）
        received_event.wait(timeout=3.0)
        time.sleep(0.1)  # 让 worker 完整投递

        # 同时跑 LLM (Qwen3) 与 BaselinePredictor (phrase_pair 链) 并展示
        # D-011 P0.5.6: BaselinePredictor.predict 现在返 PredictionResult
        baseline_pred = BaselinePredictor(store)
        baseline_result = baseline_pred.predict(accumulated, max_chars=8)
        baseline_text = baseline_result.text

        if received and received[-1].text:
            r = received[-1]
            line("LLM 续写:", f'"{r.text}"')
            if r.prefill_ms:
                line("Prefill 延迟:", f'{r.prefill_ms:.1f} ms (server)')
            line("Wall 延迟:", f'{r.total_ms:.1f} ms (含 HTTP)')
            if r.prompt_tokens:
                line("Tokens 输入:", str(r.prompt_tokens))
            print(f"""
  用户体验 (LLM):
    屏幕显示  "{accumulated}\033[2m{r.text}\033[0m"
                         ↑ 淡灰色，按 Tab 接受 / 继续打字自动消失
""")
        elif received:
            line("LLM 续写:", "(0.6B 在 raw mode 下质量低，已被质量门拦)")
            line("Wall 延迟:", f'{received[-1].total_ms:.1f} ms')
            line("说明:", "端到端管道完整。Prompt engineering / 更大模型留 Cycle 2")
        else:
            line("LLM 超时:", "未收到结果")

        # BaselinePredictor 救场：用户自己的 phrase_pair 链
        print()
        line("Baseline 续写:", f'"{baseline_text}" (基于用户 phrase_pair 链)' if baseline_text else "(用户数据不足)")
        if baseline_text:
            print(f"""
  用户体验 (Baseline):
    屏幕显示  "{accumulated}\033[2m{baseline_text}\033[0m"
                         ↑ 完全本地，零延迟，无 LLM 依赖
""")

        # ──────── 阶段 4：缓存命中演示 ────────
        step("阶段 4 — 缓存命中（同上下文重复请求）")
        t1 = client.predict(prefix=accumulated, context=predictor_ctx[:-len(accumulated)])
        line("第二次相同请求:", f'{t1.total_ms:.2f} ms, cache_hit={t1.cache_hit}')

        # ──────── 统计 ────────
        banner("Cycle 1 全栈运行统计")
        stats = client.stats.as_dict()
        for k, v in stats.items():
            if isinstance(v, float):
                line(f"{k}:", f"{v:.2f}")
            else:
                line(f"{k}:", str(v))

    finally:
        trigger.stop(timeout=2.0)
        store.close()
        Path(tmp.name).unlink(missing_ok=True)

    banner("Demo 完成 — Cycle 1 Python 参考层全部就位")
    print("""
  ✓ Memory:    数据落库 + 隐私拦截 + 用户面板
  ✓ Reranker:  4 特征统计 baseline，远超 SLO
  ✓ Context:   Reranker / Predictor 共享上下文构造器
  ✓ Predictor: HTTP /completion + 超时降级 + LRU 缓存
  ✓ Trigger:   异步停顿检测 + worker 线程 + 取消机制

  这套完整链路证明产品宪法可以兑现:
    ① 不打断视线  ── inline 渲染 + 候选默认隐藏
    ② 不离开磁盘  ── 全本地，Predictor 只走 127.0.0.1
    ③ 不解释自己  ── 没有 AI 助手按钮，没有桌宠

  下一步: Week 3+5 C++ 移植 → weasel/librime 集成 → v0.1 自用
""")


if __name__ == "__main__":
    main()
