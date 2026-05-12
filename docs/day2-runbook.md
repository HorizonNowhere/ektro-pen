# Day 2 Runbook — 5 分钟开干指南

> 给明天醒来的你（或下次会话的 Claude 实例）。
> 假设：BITS 后台任务已在过夜运行。

---

## 第 1 步：检查 BITS 是否完成（30 秒）

```powershell
Import-Module BitsTransfer
$j = Get-BitsTransfer -Name Qwen3-IQ4_XS -ErrorAction SilentlyContinue
if ($j) {
    Write-Host ('State: ' + $j.JobState)
    Write-Host ('Bytes: ' + [math]::Round($j.BytesTransferred/1MB,1) + ' / ' + [math]::Round($j.BytesTotal/1MB,1) + ' MB')
    if ($j.JobState -eq 'Transferred') {
        Complete-BitsTransfer -BitsJob $j
        Write-Host 'DONE - file moved to final location'
    }
}
ls 'E:\CLAUDE\EKTRO输入法\data\models\'
```

**期望状态**：`Qwen3-0.6B-IQ4_XS.gguf` 大小约 350.8 MB。

### 三种情况

| BITS 状态 | 行动 |
|----------|------|
| `Transferred` + `Complete` 成功 | 进入第 2 步 |
| `Transferring` (仍未完成) | 继续等，或换更快网络 |
| `Error` 或任务消失 | 用 fallback：见 §A |

---

## 第 2 步：Smoke test 验证管线（5 秒）

```powershell
cd E:\CLAUDE\EKTRO输入法
python tests\latency\smoke_test.py
```

**期望输出**：
- `✓ prompt eval time = XX ms` 等 4 行 timing 解析成功
- `✓ No <think> tag detected`（或 ⚠ 告警，按 §B 处理）

---

## 第 3 步：跑完整 benchmark（5-10 分钟）

```powershell
python tests\latency\run_first_token_bench.py `
    --model data\models\Qwen3-0.6B-IQ4_XS.gguf `
    --reps 10 `
    --threads 6
```

**输出**：
- 终端 SUMMARY 段（P50/P95/P99）
- `tests\latency\results.json`（全量）
- `tests\latency\results.csv`（简表）
- 终端最后的 "SLO 判定" 段（✅/🟡/🔴）

---

## 第 4 步：填实测数据到 benchmark 报告（10 分钟）

打开 [`docs/benchmarks/qwen3-firsttoken.md`](./benchmarks/qwen3-firsttoken.md)，根据终端输出填三个 P50/P95/P99 表格 + SLO 判定段 + 结论建议。

模板里所有 `TBD` 都是占位符。

---

## 第 5 步：写 D-003 决策 + 勾选 tasks.md（5 分钟）

### 5.1 在 [`docs/decisions.md`](./decisions.md) 写 D-003

```markdown
## D-003 — 2026-05-1X — Qwen3 端侧延迟实测结论

**决策**
基于 docs/benchmarks/qwen3-firsttoken.md：
- short_15tok P95: XX ms
- medium_50tok P95: XX ms
- long_200tok P95: XX ms

**SLO 判定**: ✅/🟡/🔴

**影响 design.md**:
- 如果 ✅: 按原计划 Week 2 启动记忆系统
- 如果 🟡: 调整 predictor_delay_ms 或换模型
- 如果 🔴: 推翻 design.md §9.D4，重新设计预测模块
```

### 5.2 在 [`openspec/changes/ektro-mvp/tasks.md`](../openspec/changes/ektro-mvp/tasks.md) 勾选

```markdown
- [x] T1.10 下载 Qwen3-0.6B-Instruct GGUF 模型（IQ4_XS 量化版本）
- [x] T1.11 编译 llama.cpp（含 SIMD/AVX 优化）
- [x] T1.12 跑 llama-bench 在主机 CPU 上...
- [x] T1.13 用真实中文 prompt 测首 token 延迟
- [x] T1.14 把数据写入 docs/benchmarks/qwen3-firsttoken.md
- [x] T1.15 决策：首 token P95 ≤200ms ✓ 继续；>200ms ✗ 切换
```

---

## §A: BITS 失败的 fallback

### A.1 用浏览器手动下载

打开浏览器访问：
https://hf-mirror.com/unsloth/Qwen3-0.6B-GGUF/resolve/main/Qwen3-0.6B-IQ4_XS.gguf

存到 `E:\CLAUDE\EKTRO输入法\data\models\Qwen3-0.6B-IQ4_XS.gguf`。

### A.2 改用 Qwen 官方 Q8_0（ModelScope，国内 CDN 稳定些）

```powershell
$url = 'https://modelscope.cn/api/v1/models/Qwen/Qwen3-0.6B-GGUF/repo?Revision=master&FilePath=Qwen3-0.6B-Q8_0.gguf'
Invoke-WebRequest -Uri $url -OutFile 'E:\CLAUDE\EKTRO输入法\data\models\Qwen3-0.6B-Q8_0.gguf'
```

然后改 benchmark 命令的 `--model` 参数指向 Q8_0。

### A.3 如果有 VPN/代理

直接 `pip install -U "huggingface_hub[cli]"` 然后：

```powershell
huggingface-cli download unsloth/Qwen3-0.6B-GGUF Qwen3-0.6B-IQ4_XS.gguf --local-dir E:\CLAUDE\EKTRO输入法\data\models
```

---

## §B: 如果 smoke test 报 `<think>` 警告

Qwen3 默认开启思考模式，可能影响 first-token 测量。两个解决方法：

1. **改 prompt** 加 `<|im_start|>user\n<问题>\n<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n`
   把思考块伪造为空，跳过思考阶段。

2. **加 `--reasoning-budget 0`** flag 到 llama-cli（如果版本支持）

3. 直接接受 thinking 时间作为"最坏情况延迟"，对 design.md 也有意义。

具体处理见 [Qwen3 thinking 模式文档](https://huggingface.co/Qwen/Qwen3-0.6B)。

---

## §C: 如果今天就想做 GPU benchmark（奖励路径）

用户机器有 RTX 4070 Ti。下载 CUDA 版本 llama.cpp：

```powershell
$url = 'https://github.com/ggml-org/llama.cpp/releases/download/b9102/llama-b9102-bin-win-cuda-12.4-x64.zip'
Invoke-WebRequest -Uri $url -OutFile 'E:\CLAUDE\EKTRO输入法\tools\llama-b9102-cuda.zip'
# 解压到 tools\llama-cuda\
Expand-Archive -Path 'E:\CLAUDE\EKTRO输入法\tools\llama-b9102-cuda.zip' -DestinationPath 'E:\CLAUDE\EKTRO输入法\tools\llama-cuda\' -Force
# 还需要 CUDA runtime DLL
$url2 = 'https://github.com/ggml-org/llama.cpp/releases/download/b9102/cudart-llama-bin-win-cuda-12.4-x64.zip'
Invoke-WebRequest -Uri $url2 -OutFile 'E:\CLAUDE\EKTRO输入法\tools\cudart.zip'
Expand-Archive -Path 'E:\CLAUDE\EKTRO输入法\tools\cudart.zip' -DestinationPath 'E:\CLAUDE\EKTRO输入法\tools\llama-cuda\' -Force
```

然后改 `tests\latency\run_first_token_bench.py` 里 `--llama-cli` 路径指向 `tools\llama-cuda\llama-cli.exe`，加 `-ngl 999` 把所有层放 GPU。

**注意**：GPU 数字是奖励信息。**SLO 仍然要求 CPU 路径达标**（CLAUDE.md 公理 — 产品要服务普通用户）。

---

## §D: Week 1 余下任务

Day 2 完成后，Week 1 还有这些任务：

- [ ] T1.16-T1.20: 万象拼音准确率验证
- [ ] T1.21-T1.24: inline 渲染兼容性测试
- [ ] T1.25-T1.28: Week 1 收尾 + Go/No-Go 决策

见 [`openspec/changes/ektro-mvp/tasks.md`](../openspec/changes/ektro-mvp/tasks.md) Week 1 段。

---

*相关：[decisions.md](./decisions.md) · [cycle1-spike-day1.md](./cycle1-spike-day1.md) · [tasks.md](../openspec/changes/ektro-mvp/tasks.md)*
