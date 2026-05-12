"""
llama-server 模式 benchmark：模型常驻 = 真实 IME 场景。

启动条件：
    llama-server.exe -m <model.gguf> --host 127.0.0.1 --port 8088 -t 6

测试维度：
    1. 三档 prompt 长度 (~22 / ~50-70 / ~200+ tokens)
    2. 每档 10 次重复
    3. 关键：cache_prompt=false，强制重新 prefill（模拟用户新输入）
    4. 验证 long context 是否仍崩溃
"""
from __future__ import annotations

import io
import json
import statistics
import sys
import time
from dataclasses import dataclass, asdict
from typing import List, Optional

import urllib.request
import urllib.error

# Force UTF-8 stdout
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SERVER_URL = "http://127.0.0.1:8088"

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
    prompt_tokens: Optional[int]
    prefill_ms: Optional[float]
    decode_tps: Optional[float]
    wall_ms: float
    http_status: int
    error: Optional[str] = None


def call_completion(prompt: str, n_predict: int = 1) -> tuple[dict, int, float]:
    """
    POST /completion → 返回 (json_dict, http_status, wall_seconds).
    cache_prompt=false 强制重新 prefill (模拟 IME 新输入场景)。
    """
    body = json.dumps({
        "prompt": prompt,
        "n_predict": n_predict,
        "cache_prompt": False,
        "temperature": 0.0,
        "top_k": 1,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{SERVER_URL}/completion",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            wall = time.perf_counter() - start
            return data, resp.status, wall
    except urllib.error.HTTPError as e:
        wall = time.perf_counter() - start
        return {"error": f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}"}, e.code, wall
    except Exception as e:
        wall = time.perf_counter() - start
        return {"error": str(e)}, -1, wall


def warmup():
    """One throwaway request to ensure KV cache state is clean."""
    print(">> Warmup...", flush=True)
    call_completion("你好", n_predict=1)


def benchmark(reps: int) -> List[RunResult]:
    results: List[RunResult] = []
    warmup()

    for key, prompt in PROMPTS.items():
        print(f"\n=== {key} (prompt chars: {len(prompt)}) ===", flush=True)
        for i in range(reps):
            data, status, wall = call_completion(prompt, n_predict=1)

            if status != 200:
                print(f"  run {i+1}/{reps}: FAILED status={status} err={data.get('error', 'unknown')[:120]}", flush=True)
                results.append(RunResult(
                    prompt_key=key,
                    prompt_chars=len(prompt),
                    run_idx=i + 1,
                    prompt_tokens=None,
                    prefill_ms=None,
                    decode_tps=None,
                    wall_ms=wall * 1000,
                    http_status=status,
                    error=str(data.get("error", ""))[:200],
                ))
                continue

            t = data.get("timings", {})
            prompt_n = t.get("prompt_n")
            prompt_ms = t.get("prompt_ms")
            predicted_per_second = t.get("predicted_per_second")
            print(f"  run {i+1}/{reps}: tokens={prompt_n} prefill={prompt_ms} ms decode={predicted_per_second} tps wall={wall*1000:.0f}ms", flush=True)

            results.append(RunResult(
                prompt_key=key,
                prompt_chars=len(prompt),
                run_idx=i + 1,
                prompt_tokens=prompt_n,
                prefill_ms=prompt_ms,
                decode_tps=predicted_per_second,
                wall_ms=wall * 1000,
                http_status=status,
            ))
    return results


def summarize(results: List[RunResult]) -> dict:
    by_key: dict[str, dict] = {}
    for key in PROMPTS:
        vals = [r.prefill_ms for r in results if r.prompt_key == key and r.prefill_ms is not None]
        errors = [r.error for r in results if r.prompt_key == key and r.error]
        if not vals:
            by_key[key] = {"error": f"all runs failed: {errors[:1]}", "n_errors": len(errors)}
            continue
        s = sorted(vals)
        n = len(s)
        by_key[key] = {
            "n": n,
            "n_errors": len(errors),
            "min_ms": min(s),
            "max_ms": max(s),
            "mean_ms": statistics.mean(s),
            "stdev_ms": statistics.stdev(s) if n > 1 else 0.0,
            "p50_ms": s[n // 2],
            "p95_ms": s[min(n - 1, int(n * 0.95))],
            "p99_ms": s[min(n - 1, int(n * 0.99))],
            "tokens_seen": [r.prompt_tokens for r in results if r.prompt_key == key and r.prompt_tokens is not None][:1],
        }
    return by_key


def main():
    # Health check
    try:
        with urllib.request.urlopen(f"{SERVER_URL}/health", timeout=3) as resp:
            print(f"Server health: {resp.read().decode('utf-8')}")
    except Exception as e:
        sys.exit(f"Server not reachable at {SERVER_URL}: {e}")

    REPS = 10
    print(f"Reps per prompt: {REPS}\n")

    results = benchmark(REPS)
    summary = summarize(results)

    # JSON 全量
    out_json = r"E:\CLAUDE\EKTRO输入法\tests\latency\server_results.json"
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "runs": [asdict(r) for r in results]}, f, ensure_ascii=False, indent=2)

    # 终端汇总
    print("\n" + "=" * 60)
    print("SUMMARY (prefill = 首 token 延迟, server mode 模型常驻)")
    print("=" * 60)
    for key, s in summary.items():
        if "error" in s:
            print(f"\n{key}: {s.get('error')}")
            print(f"  errors: {s.get('n_errors', 0)}")
            continue
        print(f"\n{key} (n={s['n']}, errors={s.get('n_errors',0)}):")
        print(f"  P50:   {s['p50_ms']:.1f} ms")
        print(f"  P95:   {s['p95_ms']:.1f} ms")
        print(f"  P99:   {s['p99_ms']:.1f} ms")
        print(f"  mean:  {s['mean_ms']:.1f} ± {s['stdev_ms']:.1f} ms")
        print(f"  range: [{s['min_ms']:.1f}, {s['max_ms']:.1f}]")
        print(f"  tokens: {s.get('tokens_seen', [])}")

    # SLO 判定
    print("\n" + "=" * 60)
    print("SLO 判定 (design.md §6 修订后: 首 token ≤ 200ms P95 仅对短上下文)")
    print("=" * 60)
    for key, s in summary.items():
        if "error" in s:
            print(f"  {key:20s} ❌ {s.get('error', '')[:60]}")
            continue
        slo_pass = s["p95_ms"] <= 200
        verdict = "✅ PASS" if slo_pass else ("🟡 MARGINAL" if s["p95_ms"] <= 400 else "🔴 FAIL")
        print(f"  {key:20s} P95={s['p95_ms']:6.1f} ms → {verdict}")

    print(f"\n详细数据: {out_json}")


if __name__ == "__main__":
    main()
