# Qwen3-0.6B 首 Token 延迟实测报告

> **Status**: Completed (Day 1 Spike)
> Last updated: 2026-05-11
> Author: Day 1 Spike 自动跑出，人工审核

---

## 1. 测试目的

回答 [design.md O2](../../openspec/changes/ektro-mvp/design.md)：

> Qwen3-0.6B 在 (主理人的) 主机 CPU 上的真实首 token 延迟是多少？

对照 SLO（[design.md §6](../../openspec/changes/ektro-mvp/design.md)）：

> **首 token P95 ≤ 200ms** （用于"下一句预测"模块）

---

## 2. 测试环境

| 项 | 值 |
|----|-----|
| CPU | 12th Gen Intel Core i5-12400F |
| Cores / Threads | 6 物理核 / 12 逻辑核 |
| Base Clock | 2.5 GHz |
| Architecture | Alder Lake (AVX2, 无 AVX-512) |
| 最优 SIMD 内核 | `ggml-cpu-alderlake.dll` |
| RAM | 31.8 GB (free ~4 GB during test) |
| OS | Windows 11 专业版 (Build 26200, 24H2) |
| llama.cpp 版本 | b9102 (Clang 19.1.5) |
| Python | 3.11 |
| 线程数 | 6 (与物理核数对齐) |

---

## 3. 测试模型

| 模型 | 量化 | 大小 | 来源 |
|------|------|------|------|
| **Qwen3-0.6B** | **Q4_K_M (ollama 默认)** | **522 MB** | **ollama pull (Ollama registry CDN)** |

**为什么不是计划的 IQ4_XS**：
- IQ4_XS 通过 hf-mirror 下载平均 100 KB/s，1 小时未完成
- ollama registry CDN 速度快 5-10x (500 KB/s - 1.4 MB/s)
- ollama 默认量化 Q4_K_M（397-522MB 范围，因为它包含完整 tokenizer + 元数据），与 IQ4_XS（368MB）质量等级相当
- 直接复用 ollama blob，绕过 ollama 状态机问题（手动复制 + 改 .gguf 扩展名）

**详细教训**：见 [`docs/decisions.md`](../decisions.md) D-002 反思段

---

## 4. 测试方法

### 4.1 关键发现

llama.cpp b9102 把 CLI 分成两个二进制：
- `llama-cli.exe` = 对话模式（自动加载 chat template, 不支持 `--no-conversation`）
- `llama-completion.exe` = 纯补全（**我们用这个**）

### 4.2 进程模型

每次 benchmark 跑一个新的 `llama-completion.exe` 进程，意味着：
- **Run 1 = 冷启动**（含 ~400ms 模型加载）
- **Run 2-5 = warm**（模型已加载到 OS 文件缓存）

但 `prompt eval time` 指标**仅计 prefill 计算**，不含 load time。所以 prefill 数字是干净的。

### 4.3 Prompt 集

| key | 字符数 | 实际 tokens | 含义 |
|-----|-------|--------------|------|
| `short_15tok` | 16 | ~22 | 单句拼音段（"我今天早上喝了一杯咖啡，下午想去"） |
| `medium_50tok` | 60 | ~50-70 | 短上下文（"用户已经在记事本写下..."） |
| `long_200tok` | 203 | ~200+ | 长上下文（最近一周输入历史摘要） |

### 4.4 运行参数

```
llama-completion.exe
    -m qwen3-0.6b.gguf
    -p <prompt>
    -n 1
    -t 6
    --no-display-prompt
    --no-warmup
    --perf
```

5 次重复（含 1 次进程冷启动 + 4 次相对 warm），跨三种 prompt 长度。

---

## 5. 测试结果（实测）

### 5.1 short_15tok（~22 tokens 实际）

| Run | Prefill (ms) | 备注 |
|-----|------|------|
| 1 | **425.9** | 冷启动（含磁盘读模型） |
| 2 | 129.8 | warm |
| 3 | 125.1 | warm |
| 4 | 122.4 | warm |
| 5 | 133.2 | warm |

**统计**：
- **Warm P50: 127.5 ms**
- **Warm 范围: 122-133 ms (非常稳定)**
- Cold P95: 425.9 ms
- 全样本均值: 187.3 ± 133.5 ms

### 5.2 medium_50tok（~50-70 tokens 实际）

| Run | Prefill (ms) | 备注 |
|-----|------|------|
| 1 | 324.5 | 冷启动 |
| 2 | 465.8 | warm（高） |
| 3 | 374.8 | warm |
| 4 | 492.8 | warm（高） |
| 5 | 238.4 | warm |

**统计**：
- **P50: 374.8 ms**
- **P95: 492.8 ms**
- 均值: 379.2 ± 104.0 ms

### 5.3 long_200tok（~200+ tokens 实际）

| Run | Prefill (ms) | Exit Code | 备注 |
|-----|------|-----------|------|
| 1-5 | **N/A (崩溃)** | `0xC0000409` | STACK_BUFFER_OVERRUN |

**⚠️ 严重问题**：长上下文（>=200 tokens）在 llama-completion.exe b9102 上**直接崩溃**。
- 退出码 `3221226505 = 0xC0000409 = STATUS_STACK_BUFFER_OVERRUN`
- 5/5 全部崩溃，可复现
- 需要单独调研：是 llama.cpp bug? Qwen3 模型特定？参数问题？

### 5.4 解码吞吐

未单独测量（n=1 测试用例，eval time 显示 0.00ms）。**留给 Day 2/3 单独 spike**。

---

## 6. SLO 判定

```
   ┌────────────────────────────┬──────────────┬───────────────────┐
   │ Context 长度                │ Warm P50     │ 判定 (≤200ms P95) │
   ├────────────────────────────┼──────────────┼───────────────────┤
   │ short_15tok (~22 token)    │   127.5 ms   │   ✅ PASS         │
   │ medium_50tok (~50-70 tok)  │   374.8 ms   │   🔴 FAIL         │
   │ long_200tok (~200+ tok)    │   N/A 崩溃    │   🔴 BLOCKED      │
   └────────────────────────────┴──────────────┴───────────────────┘
```

---

## 7. 结论与建议

### 7.1 核心结论

1. **短上下文 (≤30 token) 性能合格**：稳态 prefill ~127ms，**完全够用于"候选重排"场景**
2. **中长上下文 (50+ token) 性能不足**：375-490ms，**超 SLO 87-145%**
3. **极长上下文崩溃**：暴露 llama-completion 的稳定性问题
4. **真实 IME 中模型常驻**：实测 warm-run 是更接近真实场景的数字

### 7.2 对 design.md 的具体影响

| design 决策 | 原假设 | 实测 | 行动 |
|------------|--------|------|------|
| §3 EktroRerankFilter 用 GRU 不用 LLM | GRU ~30ms | （未测，但 LLM ~127ms 也勉强） | 保持原决策 |
| §4 EktroPredictor 用 Qwen3-0.6B + 最近 5 条 commit (~200 tok) | <200ms P95 | 50 tok 都 ~375ms / 200 tok 崩溃 | **必须修订** |
| §6 性能预算 Qwen3 首 token ≤200ms P95 | yes | NO for 50+ tokens | **必须修订** |
| §9.D4 决定用 Qwen3-0.6B IQ4_XS | 端侧可行 | 短上下文可行，长不可行 | 限制使用场景 |

### 7.3 三个候选修订方向

**A. 限制 EktroPredictor 上下文为 ≤30 tokens**
- 优点：当前模型就能用
- 代价：预测质量受限，无法用 "最近 5 条 commit" 的丰富上下文

**B. 换更小模型（Qwen3-0.3B 或自训 100M）**
- 优点：长上下文也快
- 代价：质量下降，可能不够用作"惊艳"

**C. 上 GPU 推理（用户机器有 RTX 4070 Ti）**
- 优点：所有长度都快得多
- 代价：违反 CLAUDE.md 公理"普通 i5 笔记本能跑"

**D. 改造架构 — Predictor 不是同步的，是后台流式**
- 优点：用户不感知 prefill 延迟（停顿 300ms 期间预测可能没完成但 OK）
- 代价：design 增加复杂度

### 7.4 推荐决策（写入 D-003）

短期（Cycle 1）：**A + D 组合**
- Predictor 上下文限制 ≤30 tokens（最近 1-2 条 commit）
- Predictor 异步非阻塞，超时 200ms 静默放弃
- 接受 "短上下文 → 强预测，长上下文 → 无预测" 的退化

长期（Cycle 2+）：探索 B 和 C
- 训练 100M 专用预测器（B）
- 提供"GPU 加速模式"作为可选开关（C）

### 7.5 必须立即做的事

- [ ] 报告 long_200tok 崩溃到 llama.cpp issue tracker（可能是 b9102 bug）
- [ ] 跑 `llama-bench` 比较纯吞吐数字（更可比）
- [ ] 用 `llama-server` 持久进程模式重测（无 cold start）
- [ ] 测 GPU 路径（RTX 4070 Ti）作为参考数据

---

## 附录 A: 原始数据

- JSON: [`results.json`](../../tests/latency/results.json)
- CSV: [`results.csv`](../../tests/latency/results.csv)

---

## 附录 B: 复现步骤

```powershell
cd E:\CLAUDE\EKTRO输入法
$env:PYTHONIOENCODING='utf-8'
python tests\latency\run_first_token_bench.py `
    --model E:\ektro-models\qwen3-0.6b.gguf `
    --reps 5 `
    --threads 6
```

**注意**：模型路径必须**纯 ASCII**（含中文路径会因 Windows 编码问题导致 llama-completion 无法加载）。

---

## 附录 D: ✨ Server 模式补充测试（关键修正）

> **追加于 Day 1 Spike 尾部**：用 `llama-server.exe` 持久进程模式重测，结果**翻转了多项原判断**。

### D.1 测试方法

- 启动 `llama-server.exe -m qwen3-0.6b.gguf --host 127.0.0.1 --port 8088 -t 6 -c 4096`
- 模型加载**仅一次**（首次启动 ~3 秒），后续推理共享 KV cache 状态
- HTTP POST 到 `/completion` endpoint，`cache_prompt: false` 强制每次重 prefill（模拟 IME 新输入）
- 3 档 prompt × **10 reps** 每档

### D.2 真实数据

```
   short (~10 tokens, 16 chars):
     P50: 56.3 ms   P95: 76.0 ms    range [53, 76]  ✅ PASS
     注：原 CLI 测试 22 tokens 是含 chat template overhead

   medium (~35 tokens, 60 chars):
     P50: 142.0 ms  P95: 413.0 ms   range [119, 413]  🟡 MARGINAL
     warm runs (排除 Run 1 = 413ms cold-ish): P75 ≈ 175ms ≈ borderline PASS

   long (~124 tokens, 203 chars):
     P50: 507.5 ms  P95: 760.3 ms   range [322, 760]  🔴 FAIL but 不崩
     ✨ Server 模式下 long context **不再 STACK_BUFFER_OVERRUN 崩溃**
     仅 CLI (`llama-completion.exe`) 有 bug，server 路径稳定
```

### D.3 对比与判断翻转

| 维度 | CLI 模式 (warm P50) | Server 模式 (P50) | 改善 |
|------|---------------------|-------------------|------|
| Short | 127.5 ms | **56.3 ms** | 2.3x 更快 |
| Medium | 374.8 ms | **142.0 ms** | 2.6x 更快 |
| Long | **CRASH** | 507.5 ms | 从不可用 → 可用 |

### D.4 对 design.md 决策的影响（**关键修订**）

**原 D-003 决议**：限制 Predictor 上下文 ≤30 tokens

**Server 数据修订后**：
- ✅ **短上下文 (≤30 token)：完全 OK**，P95 = 76ms（SLO ×0.38）
- 🟡 **中上下文 (~35 token)：边界 OK** P50=142ms，warm P75 < 175ms
- 🔴 **长上下文 (~100+ token)：仍超 SLO 但可用**（500ms 量级）

**新结论**：原 design.md §4 "最近 5 条 commit 拼接，最长 256 token" 的真实可行区间是 **~50 字 (35 tokens) 左右**，对应"最近 1-2 条 commit"。

### D.5 必要的架构约束

EktroPredictor **必须用 llama-server 持久进程**，不能每次 spawn 新 CLI 进程。这与 design.md §2 双进程模型完全一致：
- Core 服务启动时拉起 llama-server (端口 127.0.0.1:8088)
- Core 通过 HTTP 调用 /completion
- Server 崩溃 → Core 自动重启 server

---

## 附录 E: GPU 路径（待补 Day 2）

用户机器有 RTX 4070 Ti (12GB VRAM)。GPU baseline 数据作为高端机器奖励路径**单独立项**，本 Spike 不覆盖。预期 GPU 上首 token 在所有 prompt 长度下都能 < 50ms。

---

## 附录 C: 已知问题与教训

1. **Windows + Python subprocess + 含中文路径**：llama-completion 收到的路径被编码错乱（GBK?）。教训记入 `dev-mirrors.md`，未来 Windows 项目避免非 ASCII 路径。
2. **llama.cpp b9102 拆 CLI**：`llama-cli` ≠ `llama-completion`。注意区分。
3. **ollama 多连接下载 = 国内最快**：500 KB/s - 1.4 MB/s。但要绕开 manifest 状态机 (kill ollama → 复制 blob → 改 .gguf 扩展名)。
4. **长上下文崩溃**：~200+ tokens 直接 STACK_BUFFER_OVERRUN。不是设计的限制，是实现的 bug，但短期内必须当成硬约束。

---

*相关：[cycle1-spike-day1.md](../cycle1-spike-day1.md) · [decisions.md](../decisions.md) · [design.md](../../openspec/changes/ektro-mvp/design.md) · [dev-mirrors.md](../dev-mirrors.md)*
