# Tasks: ektro-mvp Cycle 1

> 配套 [proposal.md](./proposal.md) 与 [design.md](./design.md)。
>
> Status: `Draft` · Cycle: 6 weeks · Created: 2026-05-11
>
> **法则**：本文件按 Week 推进，每周末勾选完成项，未勾选项进 Week 7 评审决定砍/续。

---

## Week 0 — 准备（启动前的 1-2 天）

- [ ] T0.1 在 Windows 11 主机安装 Visual Studio 2022 (含 C++ workload, ATL/MFC)
- [ ] T0.2 安装 Rust 工具链（stable + nightly，给 ime-rs 参考用）
- [ ] T0.3 安装 CMake、git、Boost（weasel 依赖）
- [ ] T0.4 准备一台干净 Windows 11 虚拟机（VMware/Hyper-V），用于注册测试
- [ ] T0.5 备份当前默认输入法配置（搜狗/微软拼音的词库），方便对比 & 应急回滚
- [ ] T0.6 创建 git 仓库 `EKTRO输入法`，初始 commit 包含 CLAUDE.md + openspec/

---

## Week 1 — 探针与决策

> **目标**：回答 design.md 的 O1-O5，决定是否继续按当前路线。

### 调研阅读

- [ ] T1.1 通读 [rime/weasel](https://github.com/rime/weasel) 仓库 README + DeepWiki 架构图
- [ ] T1.2 通读 [rime/librime](https://github.com/rime/librime) 的 `src/rime/engine.h` 与 `gear/` 目录
- [ ] T1.3 阅读 librime 的 `Filter` 接口，理解扩展点
- [ ] T1.4 通读 [万象拼音](https://github.com/amzxyz/rime_wanxiang) README + dict 结构
- [ ] T1.5 通读 [llama.cpp](https://github.com/ggml-org/llama.cpp) 的 server API 文档

### 探针 1：weasel 编译跑通

- [ ] T1.6 fork rime/weasel 到 `EKTRO/upstream/weasel`
- [ ] T1.7 按 README 编译 weasel x64 Release
- [ ] T1.8 安装到主机，验证能在 Notepad / Chrome / VSCode 打字
- [ ] T1.9 切到干净 VM 验证安装注册流程（验证 O4）

### 探针 2：Qwen3 延迟实测（验证 O2）

- [x] T1.10 下载 Qwen3-0.6B GGUF（Q4_K_M via ollama, 522MB）— ✓ 用 ollama registry CDN 解决了网络问题
- [x] T1.11 部署 llama.cpp Windows CPU 预编译版（b9102，alderlake 内核验证）
- [x] T1.12 跑首 token 延迟测试（用 `llama-completion.exe`，benchmark harness 三档 prompt × 5 reps）
- [x] T1.13 真实数据写入 `docs/benchmarks/qwen3-firsttoken.md`
- [x] T1.14 数据汇总：short P50=127.5ms ✅ / medium P50=374.8ms 🔴 / long crash 🔴
- [x] T1.15 决策：见 D-003 — design.md §4 §6 §9.D4 必须修订（Predictor 限制 ≤30 token 上下文，异步非阻塞）

### 探针 3：万象词库准确率（验证 O1）

- [ ] T1.16 用万象拼音 + 雾凇底座配置 weasel
- [ ] T1.17 准备 1000 条自然中文语料（个人微信聊天记录 / 个人笔记节选）
- [ ] T1.18 用 librime CLI 跑批，统计首选命中率
- [ ] T1.19 把数据写入 `docs/benchmarks/wanxiang-top1.md`
- [ ] T1.20 决策：准确率 ≥90% ✓ 继续；<85% ✗ 评估词库调优

### 探针 4：inline 在 UWP 兼容（验证 O3）

- [ ] T1.21 在 Microsoft Edge 浏览器尝试用 weasel 输入，观察 composition 行为
- [ ] T1.22 在 Mail / Calendar UWP 应用尝试，记录是否能注入 composition
- [ ] T1.23 在管理员权限 cmd 测试（预期不行，确认 fallback）
- [ ] T1.24 写入 `docs/compatibility-matrix.md`

### Week 1 收尾

- [ ] T1.25 写 Week 1 回顾文档 `docs/cycle1-week1-review.md`
- [ ] T1.26 **Go/No-Go 决策点**：根据 O1-O5 结果，决定是否继续
- [ ] T1.27 如继续：把决策日志合并到 `docs/decisions.md`
- [ ] T1.28 如调整：更新 design.md 并通知"自己"（在 CLAUDE.md 加 changelog）

---

## Week 2 — 记忆系统 & 用户面板

> **目标**：能落库、能可视化、能导出。"记忆"先于 AI。
> **状态**: Python 参考实现完成；C++ weasel 集成留待 Week 4-5

- [x] T2.1 创建 SQLite schema (design.md §8) — `src/memory/schema.py`
- [x] T2.2 写 `EktroMemoryStore` 模块（落库 API + 查询 API）— `src/memory/store.py`
- [ ] T2.3 集成到 weasel 的 commit 钩子（C++ 实现，等 weasel fork） — Week 4-5
- [x] T2.4 隐私拦截（密码模式 + 银行卡 + 身份证 + email + 排除应用）— 通过 7 个测试用例
- [x] T2.5 用户面板（CLI）— `python -m memory status/recent/top/...`
- [x] T2.6 用户面板：清空 (`clear --confirm`)、导出 (`export --out`) 已实现
- [x] T2.7 模拟数据验证：生成 500 条 mock，500/500 落库正确
- [x] T2.8 Week 2 阶段性回顾：性能远超 SLO（log_commit P99 4.2ms，recent_outputs P99 0.15ms）

### Week 2 性能实测（design.md §6 SLO 对照）

| 操作 | P50 | P99 | SLO | 余量 |
|------|-----|-----|-----|------|
| `log_commit` 写 | 0.125 ms | 4.2 ms | 5 ms | 1.2x |
| `recent_outputs(20)` | 0.062 ms | 0.15 ms | 3 ms | 20x |
| `word_freq_lookup(20)` | 0.011 ms | 0.05 ms | 3 ms | 60x |

**结论**：记忆系统不是性能瓶颈。详见 [`src/memory/README.md`](../../../src/memory/README.md)

---

## Week 2.5 — P0 修复期（D-009 验收结果，C++ 移植前置）

> **触发**: D-009 Agent Swarm 验收发现 4 个 cross-validated 严重问题。
> **目标**: 在进入 Week 3 C++ 集成前，把 Python 参考层的"production-ready 硬伤"修完。
> **预估**: 3-5 天

### P0 修复（违反公理 / 阻塞 C++ 移植）

- [ ] T2.5.1 重写隐私拦截：砍掉 output 正则误用 + 仅靠 is_password_field + input_raw 银行卡/身份证检测
- [ ] T2.5.2 引入 `ektro.logging` 模块（`%LOCALAPPDATA%\Ektro\logs\`，日志中不写 commit 内容）
- [ ] T2.5.3 把所有 `except: pass` / `except: return None` 接上 logger
- [ ] T2.5.4 `log_commit` 返回 `LogResult` enum（COMMITTED / SKIPPED_PASSWORD / SKIPPED_SENSITIVE / SKIPPED_APP / DB_ERROR）
- [ ] T2.5.5 `predict()` 的 error 改 `PredictionErrorKind` enum
- [ ] T2.5.6 补 `tests/concurrency/test_store_threaded.py` (4 写 2 读 × 1000)
- [ ] T2.5.7 补 `tests/predictor/test_trigger_race.py` (threading.Event 替 sleep)
- [ ] T2.5.8 修 `clear_all` 的 VACUUM bug（先 commit 再 VACUUM）

### P1 重要（建议同期完成）

- [ ] T2.5.9 AsyncTrigger task_id 改 `itertools.count()` 单调计数器
- [ ] T2.5.10 PredictorClient 用 `isinstance(e.reason, socket.timeout)` 替字符串匹配
- [ ] T2.5.11 rerank phrase_pair 批量查询消除 N+1
- [ ] T2.5.12 `context_builder.py` 移到 `src/shared/`（rerank + predictor 共用）
- [ ] T2.5.13 声明 `BaseReranker` Protocol + `MemoryView` Protocol（小接口暴露）
- [ ] T2.5.14 补 UTF-8 / emoji / 零长度 边界测试
- [ ] T2.5.15 替换 test_realistic_scenario 软断言为强校验

### 退出标准

```
✓ 4 个 ⭐ cross-validated 问题全部解决
✓ 全部测试套件（含新增并发/竞态）通过
✓ logging 日志在 %LOCALAPPDATA%\Ektro\logs\ 可见且可读
✓ swarm 验收第二轮（可选）评分从 B- 提到 A-
```

---

## Week 3 — inline 渲染 + Tab 切换

> **目标**：候选窗默认消失。视线不被打断。
> **重大发现** (D-006): weasel 已实现 inline preedit 完整链路，EKTRO 只需 3 个小 patch + YAML 配置。

### Week 3 准备（已完成）

- [x] T3-Prep.1 探索 weasel 源码结构（通过 WebFetch 读 raw.githubusercontent.com，无需本地 clone）
- [x] T3-Prep.2 确认 inline preedit 已有实现（`CInlinePreeditEditSession`）
- [x] T3-Prep.3 定位关键改动点（`_ShowUI`, `_HideUI`, `_UpdateUI`, `KeyEventSink`）
- [x] T3-Prep.4 写 [`docs/week3-inline-patch-design.md`](../../../docs/week3-inline-patch-design.md) 详细方案

### Cycle 2 启动准备（D-011 GO 后已就位 ✅）

- [x] T2.5-准备 src-cpp/ 骨架目录
- [x] T2.5-写 `src-cpp/include/ektro/memory_view.h` (MemoryView + BatchMemoryView interface)
- [x] T2.5-写 `src-cpp/include/ektro/log_result.h` (LogResult enum + LogOutcome)
- [x] T2.5-写 `src-cpp/include/ektro/reranker.h` (Reranker + BaselineReranker + RankedCandidate)
- [x] T2.5-写 `src-cpp/include/ektro/predictor.h` (BasePredictor + Client + Baseline + Trigger)
- [x] T2.5-写 `src-cpp/PORTING_GUIDE.md` (D-011 完整移植路线)

### Week 3 实施（按 src-cpp/PORTING_GUIDE.md 5 日路线）

- [ ] T3-Day1 Fork weasel 仓库（跨境网络方案: 浏览器 / ghproxy / 实体介质）
- [ ] T3-Day1 拉 librime submodule + baseline 编译跑通
- [ ] T3-Day1 安装 baseline weasel，Notepad 中文输入验证
- [ ] T3-Day2 创建 weasel/src/ektro_plugin/ 目录加入 CMake
- [ ] T3-Day2 移植 schema.py → sqlite_schema.cpp
- [ ] T3-Day2 移植 store.py → memory_store.cpp（含 WAL + 线程锁）
- [ ] T3-Day2 跑等价 GoogleTest（store / boundary / concurrency）
- [ ] T3-Day3 移植 baseline.py(rerank) → baseline_reranker.cpp
- [ ] T3-Day3 实现 librime::Filter 子类调 BaselineReranker
- [ ] T3-Day3 跑等价 rerank 单元测试
- [ ] T3-Day4 移植 trigger.py + client.py（注意 task_id 用 std::atomic<uint64_t>，D-011 P1.1）
- [ ] T3-Day4 跑等价 trigger 竞态测试
- [ ] T3-Day5 `_ShowUI` 加条件门 + 长按 Ctrl + Tab 键拦截 + default.custom.yaml
- [ ] T3-Day5 兼容矩阵测试（Notepad / VSCode / Chrome / 微信 / Edge / cmd）

---

## Week 4 — Rerank

> **目标**：候选排序开始"像你"。
> **重大决策修订** (D-007): 先做统计 baseline，神经 ranker 留到证据驱动。

### Phase A — 统计 Baseline (已完成 ✓)

- [x] T4.A.1 设计 EktroBaselineReranker 架构（4 特征：字频 + 二元组 + 上下文 + rime先验）
- [x] T4.A.2 实现 `src/rerank/baseline.py` (~200 行)
- [x] T4.A.3 实现 `src/rerank/context_builder.py`（与 predictor 共用）
- [x] T4.A.4 12 个单元测试 0.25s 全通过
- [x] T4.A.5 性能 benchmark：P95 远低于 3ms SLO（10 候选 0.376 ms）
- [x] T4.A.6 端到端 demo (`demo_pipeline.py`)：真实演示"咖→啡"二元组重排
- [x] T4.A.7 README 文档 + ONNX 升级路径预留

### Phase B — 神经 Ranker（推迟，证据驱动）

> 升级触发条件（见 [`src/rerank/README.md`](../../../src/rerank/README.md)）：
> - 用户 Tab 切换率 > 10%
> - 长尾词错排率高
> - 主观体验"不懂我"

- [ ] T4.B.1 收集真实 Tab 切换率数据（需 Week 5+ 集成后跑一段时间）
- [ ] T4.B.2 评估是否触发升级阈值
- [ ] T4.B.3 (如触发) 训 ~20M GRU + 导出 ONNX
- [ ] T4.B.4 (如触发) 实现 OnnxGRUReranker，复用同一 BaseReranker 接口
- [ ] T4.B.5 (如触发) 性能验证 + 离线 A/B 对照 baseline

### Phase C — C++ 移植 (Week 5 weasel filter 集成时)

- [ ] T4.C.1 把 `baseline.py` 移植为 `EktroRerankFilter` (librime Filter 子类)
- [ ] T4.C.2 跑 Python 测试的等价 C++ 测试，cross-check 一致
- [ ] T4.C.3 注册到 librime 管线（在 Translator 之后、Filter2 之前）

### Phase D 性能实测（design.md §6 SLO 对照）

| 操作 | P50 | P95 | SLO |
|------|-----|-----|-----|
| rerank(10 候选 + 长上下文) | 0.184 ms | 0.376 ms | 3 ms ✅ |
| 端到端 build_context + rerank | 0.148 ms | 0.333 ms | 4 ms ✅ |
| 100 候选压力 | 1.093 ms | 1.940 ms | 5 ms ✅ |

**结论**：rerank 不是性能瓶颈。详见 [`src/rerank/README.md`](../../../src/rerank/README.md)

---

## Week 5 — Predictor

> **目标**：用户停顿后浮现淡灰续写。
> **重大修订** (D-004): 不用子进程，用 llama-server HTTP 持久进程 + AsyncTrigger
> **重大修订** (D-008): Python 参考实现已完整，C++ 移植留实施期

### Phase A — Python 参考实现（已完成 ✓）

- [x] T5.A.1 PredictorClient: HTTP /completion 调用，超时降级 (`src/predictor/client.py`)
- [x] T5.A.2 LRU 缓存：同 prompt 不重复请求（cache_capacity=128）
- [x] T5.A.3 上下文构造：复用 `context_builder.build_predictor_prompt()`
- [x] T5.A.4 AsyncTrigger: 后台 worker + 停顿检测 + 取消机制 (`src/predictor/trigger.py`)
- [x] T5.A.5 失败降级：所有异常封装到 PredictionResult.error，不抛
- [x] T5.A.6 15 个单元测试（mock HTTP server）9.5s 全通过
- [x] T5.A.7 端到端集成 demo (`tests/integration/demo_full_pipeline.py`)，全栈打通
- [x] T5.A.8 实测：48.9ms server prefill + 0.01ms cache hit
- [x] T5.A.9 README + Cycle 1 总结文档

### Phase B — C++ 移植 (实施期)

- [ ] T5.B.1 选 HTTP client 库（cpp-httplib 或 WinHTTP）
- [ ] T5.B.2 移植 PredictorClient 接口（按 README 规约）
- [ ] T5.B.3 移植 AsyncTrigger (std::thread + condition_variable)
- [ ] T5.B.4 结果 marshal 回 IME 主线程（TSF EditSession）
- [ ] T5.B.5 浮动透明窗口 UI（50% 灰度，贴光标位置）
- [ ] T5.B.6 Tab 接受：把淡灰内容当 composition 写入
- [ ] T5.B.7 启动 llama-server 子进程（IME 启动时拉起）
- [ ] T5.B.8 跑等价 C++ 测试 cross-check 与 Python 行为一致

### Phase C — 度量与优化（Cycle 2 评估期）

- [ ] T5.C.1 度量预测接受率（用户按 Tab 接受 / 总预测数）
- [ ] T5.C.2 Prompt engineering 优化（解决 `!!!` 退化模式）
- [ ] T5.C.3 评估流式输出 SSE
- [ ] T5.C.4 评估自训专用模型替代 Qwen3

### 实测性能（design.md §6 SLO 对照）

| 场景 | Server prefill | Wall time | SLO |
|------|---------------|-----------|-----|
| short context (~10 tok) | 56 ms P50 / 76 ms P95 | ~150 ms | ✅ |
| medium context (~35 tok) | 142 ms P50 | ~250 ms | 🟡 边界 |
| 集成 demo (9 tokens) | 48.9 ms | 302 ms | ✅ |
| Cache hit | — | 0.01 ms | ✅ 极快 |

**结论**: 短上下文场景 SLO 完美。中等上下文需 AsyncTrigger 的异步非阻塞 + 缓存才能保持流畅。
详见 [`src/predictor/README.md`](../../../src/predictor/README.md)

---

## Week 6 — 收敛、发版、决策

> **目标**：能日常用，5 天不切回搜狗。

- [ ] T6.1 5 天**纯使用**，每天记 3 条："惊艳时刻" / "想吐槽" / "想加"
- [ ] T6.2 修关键 bug（崩溃 / 卡死 / 数据丢失 / 渲染错位）
- [ ] T6.3 性能 SLO 全部验证（design.md §6 的所有指标）
- [ ] T6.4 威胁模型文档 `docs/threat-model.md` v0.1
- [ ] T6.5 写 `INSTALL.md`（如何安装到自己电脑）
- [ ] T6.6 打 zip 包 `ektro-v0.1-windows-x64.zip`
- [ ] T6.7 自我安装一次（在干净 VM）
- [ ] T6.8 **Cycle 1 退出评审**：
   - 主观验收：每天用 1 周没切回？(y/n)
   - 性能 SLO：全部达标？(y/n)
   - 数据完整性：commit_log 完整无丢？(y/n)
- [ ] T6.9 写 `docs/cycle1-retrospective.md`：什么成功 / 什么失败 / 学到什么
- [ ] T6.10 决定 Cycle 2 议题（**只选一个**）

---

## Cycle 2 候选议题（Week 6 评审后决定，不提前规划）

> 见 [CLAUDE.md](../../../CLAUDE.md) §"对其他功能的判决"，砍掉了桌宠/语音/Chat 等。
> Cycle 2 议题 **只能选一个**：

```
   ⓐ 双拼方案（小鹤/自然码）
   ⓑ rerank 模型在 GPU 加速（如果 CPU 满载）
   ⓒ "私密应用"机制完善
   ⓓ 开源准备（README / LICENSE / CI / 数字签名）
   ⓔ 其他（Cycle 1 实际使用中暴露出的真实痛点）
```

**不应该提前选**。让 6 周的真实使用告诉你最痛的是什么。

---

## 进度可视化

```
   Week 0   [          ]   准备
   Week 1   [          ]   探针与决策
   Week 2   [          ]   记忆系统
   Week 3   [          ]   inline 渲染
   Week 4   [          ]   GRU rerank
   Week 5   [          ]   Qwen3 预测
   Week 6   [          ]   收敛 & 发版
```

每周末把 `[          ]` 涂成 `[██████████]` 比例。

---

## 风险监控（每周末检查）

| # | 风险 | 触发指标 | 缓解 |
|---|------|---------|------|
| R1 | 进度严重落后 | 周末未完成 ≥50% 任务 | 砍非核心任务，不延期 |
| R2 | 性能 SLO 不达 | 任何指标超 1.5x 预算 | 立即停止加功能，修性能 |
| R3 | 范围蠕变 | 出现未在 proposal 中的功能 | 立即砍，记入 docs/decisions.md |
| R4 | 信心崩溃 | 连续 3 天不想碰 | 暂停 2 天，写一封"为什么做这个"的信给自己 |

---

*相关文档：[proposal.md](./proposal.md) · [design.md](./design.md) · [../../../CLAUDE.md](../../../CLAUDE.md)*
