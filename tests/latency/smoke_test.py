"""
冒烟测试：跑一次 llama-cli，dump stderr 原文，验证 timing 解析正则是否匹配。
模型下载完成后第一时间运行此脚本，确认管线打通再跑全量 benchmark。
"""
import io
import re
import subprocess
import sys
from pathlib import Path

# Force UTF-8 stdout/stderr (Windows defaults to GBK)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

LLAMA_CLI = Path(r"E:\CLAUDE\EKTRO输入法\tools\llama.cpp\llama-completion.exe")
MODEL = Path(r"E:\CLAUDE\EKTRO输入法\data\models\Qwen3-0.6B-from-ollama.gguf")
PROMPT = "我今天早上喝了一杯咖啡，下午想去"


def main():
    if not LLAMA_CLI.exists():
        sys.exit(f"missing: {LLAMA_CLI}")
    if not MODEL.exists():
        sys.exit(f"missing: {MODEL}")

    print(f"Model: {MODEL.name} ({MODEL.stat().st_size / 1024 / 1024:.1f} MB)")
    print(f"Prompt: {PROMPT}")
    print("=" * 60)

    cmd = [
        str(LLAMA_CLI),
        "-m", str(MODEL),
        "-p", PROMPT,
        "-n", "1",
        "-t", "6",
        "--no-display-prompt",
        "--no-warmup",
        "--perf",
    ]
    print("Running:", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")

    print("\n=== STDOUT (first 500 chars) ===")
    print(proc.stdout[:500])

    print("\n=== STDERR (last 2000 chars) ===")
    print(proc.stderr[-2000:])

    print(f"\n=== Exit code: {proc.returncode} ===")

    # Try to parse timings
    print("\n=== Parsing test ===")
    patterns = [
        ("prompt eval time", r"prompt eval time\s*=\s*([\d.]+)\s*ms"),
        ("eval time", r"eval time\s*=\s*([\d.]+)\s*ms"),
        ("total time", r"total time\s*=\s*([\d.]+)\s*ms"),
        ("load time", r"load time\s*=\s*([\d.]+)\s*ms"),
    ]
    for name, pat in patterns:
        m = re.search(pat, proc.stderr)
        if m:
            print(f"  ✓ {name:20s} = {m.group(1)} ms")
        else:
            print(f"  ✗ {name:20s} NOT FOUND")

    # Detect Qwen3 thinking output
    print("\n=== Qwen3 thinking detection ===")
    if "<think>" in proc.stdout or "<think>" in proc.stderr:
        print("  ⚠ DETECTED <think> tag — model is in thinking mode")
        print("  → 这会影响 first-token latency 测量，建议关闭 thinking")
    else:
        print("  ✓ No <think> tag detected")


if __name__ == "__main__":
    main()
