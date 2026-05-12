"""
Qwen3 首 token 延迟 benchmark harness.

调用 llama-cli.exe 跑多个 prompt × 多次重复，解析 stderr 的 timing 信息，
输出 P50/P95/P99 分布到 CSV 和 markdown。

用法:
    python run_first_token_bench.py --model <path> --reps 10 --threads 4
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional


# 三档真实中文 prompt，覆盖典型上下文长度
PROMPTS = {
    "short_15tok": "我今天早上喝了一杯咖啡，下午想去",
    "medium_50tok": (
        "用户已经在记事本写下: 今天去图书馆，看到一本关于咖啡冲煮的书，"
        "介绍了 V60 和 Chemex 的区别。明天我想买一些"
    ),
    "long_200tok": (
        "用户最近一周的输入历史摘要：周一在 VSCode 写了一段关于 Rust 异步运行时的"
        "笔记，提到 tokio 与 async-std 的优劣。周二在微信和朋友讨论了周末是否去爬山，"
        "约定如果周日不下雨就一起出发。周三在记事本整理了一份待办：买咖啡豆、续订域名、"
        "联系设计师。周四在 Word 写了篇短文，主题是为什么本地优先软件比云端 SaaS 更值得"
        "投资。周五加班，写了几条邮件。周六上午，他打开记事本，输入"
    ),
}


@dataclass
class RunResult:
    prompt_key: str
    prompt_chars: int
    run_idx: int
    prefill_ms: Optional[float]
    decode_tps: Optional[float]
    total_s: float
    cmd_exit: int
    raw_timings: str


def parse_llama_timings(stderr_text: str) -> dict:
    """
    llama.cpp --timings 输出形如:
        load time =     xxx.xx ms
        sample time =   xxx.xx ms / N runs   (...)
        prompt eval time = xxx.xx ms / N tokens (...)
        eval time =     xxx.xx ms / N runs   (...)
        total time =    xxx.xx ms
    我们关心 prompt eval time (= prefill 时间, 即首 token 前花费).
    """
    out = {}
    pe = re.search(r"prompt eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*tokens", stderr_text)
    if pe:
        out["prompt_eval_ms"] = float(pe.group(1))
        out["prompt_tokens"] = int(pe.group(2))
    ev = re.search(r"eval time\s*=\s*([\d.]+)\s*ms\s*/\s*(\d+)\s*(?:runs|tokens)", stderr_text)
    if ev:
        out["eval_ms"] = float(ev.group(1))
        out["eval_tokens"] = int(ev.group(2))
        if out["eval_tokens"] > 0:
            out["decode_tps"] = out["eval_tokens"] / (out["eval_ms"] / 1000.0)
    return out


def run_one(llama_cli: Path, model: Path, prompt: str, threads: int, n_predict: int = 1) -> tuple[str, int, float]:
    """
    跑一次 llama-cli, 返回 (stderr, exit_code, wall_seconds).
    -n 1 = 只预测 1 个 token, 但 timing 会报 prompt eval time (prefill).
    """
    cmd = [
        str(llama_cli),
        "-m", str(model),
        "-p", prompt,
        "-n", str(n_predict),
        "-t", str(threads),
        "--no-display-prompt",
        "--no-warmup",          # 让结果反映真实冷+热混合
        "--perf",               # 显式开启 timing 输出
    ]
    start = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    wall = time.perf_counter() - start
    return proc.stderr, proc.returncode, wall


def run_warmup(llama_cli: Path, model: Path, threads: int):
    """第一次跑模型必然慢(磁盘加载+预热). 做 1 次 warmup 不计入."""
    print(">> Warmup run...", flush=True)
    run_one(llama_cli, model, "你好", threads, n_predict=1)


def benchmark(llama_cli: Path, model: Path, reps: int, threads: int) -> List[RunResult]:
    results: List[RunResult] = []
    run_warmup(llama_cli, model, threads)

    for key, prompt in PROMPTS.items():
        print(f"\n=== {key} (prompt chars: {len(prompt)}) ===", flush=True)
        for i in range(reps):
            stderr, exit_code, wall = run_one(llama_cli, model, prompt, threads, n_predict=1)
            t = parse_llama_timings(stderr)
            prefill_ms = t.get("prompt_eval_ms")
            decode_tps = t.get("decode_tps")
            results.append(RunResult(
                prompt_key=key,
                prompt_chars=len(prompt),
                run_idx=i + 1,
                prefill_ms=prefill_ms,
                decode_tps=decode_tps,
                total_s=wall,
                cmd_exit=exit_code,
                raw_timings=stderr[-500:],
            ))
            print(f"  run {i+1}/{reps}: prefill={prefill_ms} ms, wall={wall*1000:.0f} ms, exit={exit_code}", flush=True)
    return results


def summarize(results: List[RunResult]) -> dict:
    by_key: dict[str, dict] = {}
    for key in PROMPTS:
        vals = [r.prefill_ms for r in results if r.prompt_key == key and r.prefill_ms is not None]
        if not vals:
            by_key[key] = {"error": "no valid timing data"}
            continue
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        p50 = vals_sorted[n // 2]
        p95 = vals_sorted[min(n - 1, int(n * 0.95))]
        p99 = vals_sorted[min(n - 1, int(n * 0.99))]
        by_key[key] = {
            "n": n,
            "min_ms": min(vals_sorted),
            "max_ms": max(vals_sorted),
            "mean_ms": statistics.mean(vals_sorted),
            "stdev_ms": statistics.stdev(vals_sorted) if n > 1 else 0.0,
            "p50_ms": p50,
            "p95_ms": p95,
            "p99_ms": p99,
        }
    return by_key


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--llama-cli", default=r"E:\CLAUDE\EKTRO输入法\tools\llama.cpp\llama-completion.exe")
    ap.add_argument("--model", required=True, help="Path to GGUF model file")
    ap.add_argument("--reps", type=int, default=10)
    ap.add_argument("--threads", type=int, default=6, help="Match physical core count for best results")
    ap.add_argument("--out-json", default=r"E:\CLAUDE\EKTRO输入法\tests\latency\results.json")
    ap.add_argument("--out-csv", default=r"E:\CLAUDE\EKTRO输入法\tests\latency\results.csv")
    args = ap.parse_args()

    cli = Path(args.llama_cli)
    model = Path(args.model)
    if not cli.exists():
        sys.exit(f"llama-cli not found: {cli}")
    if not model.exists():
        sys.exit(f"model not found: {model}")

    print(f"Model: {model.name} ({model.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"Reps:  {args.reps}, Threads: {args.threads}")

    results = benchmark(cli, model, args.reps, args.threads)
    summary = summarize(results)

    # JSON 全量
    Path(args.out_json).write_text(
        json.dumps({"summary": summary, "runs": [asdict(r) for r in results]}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # CSV 简表
    with open(args.out_csv, "w", encoding="utf-8") as f:
        f.write("prompt_key,prompt_chars,run_idx,prefill_ms,decode_tps,total_ms,exit\n")
        for r in results:
            f.write(f"{r.prompt_key},{r.prompt_chars},{r.run_idx},{r.prefill_ms},{r.decode_tps},{r.total_s*1000:.1f},{r.cmd_exit}\n")

    # 终端汇总
    print("\n" + "=" * 60)
    print("SUMMARY (prefill = 首 token 延迟)")
    print("=" * 60)
    for key, s in summary.items():
        if "error" in s:
            print(f"{key}: {s['error']}")
            continue
        print(f"\n{key} (n={s['n']}):")
        print(f"  P50:   {s['p50_ms']:.1f} ms")
        print(f"  P95:   {s['p95_ms']:.1f} ms")
        print(f"  P99:   {s['p99_ms']:.1f} ms")
        print(f"  mean:  {s['mean_ms']:.1f} ± {s['stdev_ms']:.1f} ms")
        print(f"  range: [{s['min_ms']:.1f}, {s['max_ms']:.1f}]")

    # SLO 判定
    print("\n" + "=" * 60)
    print("SLO 判定 (design.md §6: 首 token ≤ 200ms P95)")
    print("=" * 60)
    for key, s in summary.items():
        if "error" in s:
            continue
        slo_pass = s["p95_ms"] <= 200
        verdict = "✅ PASS" if slo_pass else ("🟡 MARGINAL" if s["p95_ms"] <= 400 else "🔴 FAIL")
        print(f"  {key:20s} P95={s['p95_ms']:6.1f} ms → {verdict}")


if __name__ == "__main__":
    main()
