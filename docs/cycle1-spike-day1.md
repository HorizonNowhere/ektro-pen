# Day 1 Spike: Qwen3-0.6B 延迟实测

> Cycle 1 Week 1 的**零号 spike**。
> 它的结果决定 [`design.md`](../openspec/changes/ektro-mvp/design.md) 中预测模块是否可行。
>
> **时间预算**: 2-4 小时。**不超过半天**。
> **退出标准**: 一份带数字的结论，写入 `docs/benchmarks/qwen3-firsttoken.md`。

---

## 0. 为什么这是 Day 1

整个 cycle 投入巨大（≥6 周）。预测模块（Qwen3 淡灰续写）是用户体验的"惊艳点"，但也是技术风险最高的部分。

> **不验证就开干 = 在沙上盖楼**。

如果 Qwen3-0.6B 在主机首 token >400ms，整个预测模块要重新设计（换模型 / 加 GPU 依赖 / 砍预测）。

**Day 1 花半天，可能省 Week 5 整周。**

---

## 1. 目标

回答 [`design.md`](../openspec/changes/ektro-mvp/design.md) 的 Open Question **O2**：

> Qwen3-0.6B IQ4_XS 在 (主理人的) 主机 CPU 上的真实首 token 延迟分布是多少？

---

## 2. 验收阈值

```
   ┌────────────────────────────────────┬────────────────────────────┐
   │ 首 token P95                        │ 决策                       │
   ├────────────────────────────────────┼────────────────────────────┤
   │ ≤ 100ms                             │ ✅ 极佳，按 design.md 继续 │
   │ 100ms < P95 ≤ 200ms                  │ ✅ 达标，按 design.md 继续 │
   │ 200ms < P95 ≤ 400ms                  │ 🟡 评估换更小模型/GPU     │
   │ P95 > 400ms                          │ 🔴 重大决策点 → D-002 决议│
   └────────────────────────────────────┴────────────────────────────┘
```

---

## 3. 准备

### 3.1 工具

llama.cpp，二选一：
- **推荐**：下载预编译 Windows x64 (CPU 版本，含 AVX2) — https://github.com/ggml-org/llama.cpp/releases
- 自编译：CMake + Visual Studio (慢，但能开 OpenMP/AVX512 优化)

### 3.2 模型

下载 GGUF 量化版本：
- 仓库：`Qwen/Qwen3-0.6B-Instruct-GGUF` (HuggingFace)
- 文件：`qwen3-0.6b-instruct-iq4_xs.gguf` (~350MB)
- 镜像：如 HF 慢，用 hf-mirror.com 或 modelscope

### 3.3 工作目录

```
E:\CLAUDE\EKTRO输入法\
└── docs\
    └── benchmarks\
        ├── qwen3-firsttoken.md        ← 本 spike 输出
        └── (模型文件可临时放这里，benchmarks 完成后移到 data/)
```

---

## 4. 步骤

### 步骤 1: 吞吐基准

```bat
llama-bench.exe ^
    -m qwen3-0.6b-instruct-iq4_xs.gguf ^
    -p 50 -n 100 ^
    -t 4 -r 3
```

记录：
- `pp 50` (prompt processing, tokens/s) — 与 prefill 相关
- `tg 100` (text generation, tokens/s)

### 步骤 2: 真实首 token 延迟

llama.cpp `--timings` 或自己用 PowerShell 计时。建议跑 10 次取分布。

```powershell
# 准备 3 种长度的 prompt（context 50 / 200 / 500 tokens）
$prompts = @(
    "我今天早上喝了一杯咖啡，下午想去",                              # ~15 tokens
    "用户已经在记事本写下: '今天去图书馆,看到一本关于咖啡冲煮的书,介绍了 V60 和 Chemex 的区别。明天我想买'", # ~50 tokens
    "..."  # 5 倍上面的长度
)

# 每个跑 10 次,记录首 token 时间
```

### 步骤 3: 记录指标

每次记录：
- prefill_ms (从 prompt 送入到第一个 token 产出)
- 总时间
- CPU 占用峰值
- 内存峰值

### 步骤 4: 写结论

到 `docs/benchmarks/qwen3-firsttoken.md`，必须含：

1. **环境**: CPU 型号 / 核数 / 主频 / 内存 / Windows 版本
2. **数字**: 三种 context 长度的 P50/P95/P99 首 token 延迟
3. **图表**: 一张分布图（可手画 ASCII）
4. **结论**: 对照 §2 阈值，给出绿/黄/红判定
5. **下一步**: 决策建议（继续/换模型/砍范围）

---

## 5. 决策落地

完成后：

1. 把结论摘要写入 `docs/decisions.md` 的 **D-002** 条目
2. 更新 `openspec/changes/ektro-mvp/design.md` §9.D4 决策详解（如果改变方向）
3. 在 `tasks.md` 的 T1.15 项打勾，附数字
4. 决定 Week 1 余下任务是否按原计划执行

---

## 6. 异常处理

```
   ┌──────────────────────────────────┬─────────────────────────────┐
   │ 现象                              │ 处理                        │
   ├──────────────────────────────────┼─────────────────────────────┤
   │ 模型下载失败                      │ 用国内镜像 / 自己量化       │
   │ llama.cpp 编译失败                │ 用 release 预编译            │
   │ 首 token >1000ms 异常             │ 检查是否启用了 AVX2 / 线程数 │
   │ 内存溢出                          │ 减小 context / 用 IQ3       │
   │ Spike 超过半天                    │ 砍掉非核心测试,先出大致数字│
   └──────────────────────────────────┴─────────────────────────────┘
```

---

## 7. Spike 完成后

把本文件归档到 `docs/archive/`，因为 spike 是一次性产物。决策结论留在 `decisions.md`，性能数据留在 `benchmarks/`。

---

*相关文档：[decisions.md](./decisions.md) · [tasks.md](../openspec/changes/ektro-mvp/tasks.md) · [design.md](../openspec/changes/ektro-mvp/design.md)*
