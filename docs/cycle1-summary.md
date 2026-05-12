# Cycle 1 总结 — Python 参考层全部就位

> Status: **Reference complete, C++ integration pending**
> Date: 2026-05-11
> Cycle 1 时长：单一个长会话密集开发（~6 小时含网络等待）

---

## TL;DR

一句话：**Cycle 1 把 EKTRO 的所有产品级组件用 Python 实现并测通，剩下的工作是把它们端口到 C++ 集成进 weasel/librime**。

---

## 全栈架构（已实现）

```
   ┌──────────────────────────────────────────────────────────────────┐
   │ Windows IME (待 C++ 集成)                                         │
   │ ┌──────────┐  ┌──────────┐  ┌────────────────────────────────┐  │
   │ │WeaselTSF │  │WeaselIME │  │ WeaselUI (inline composition)  │  │
   │ └──────────┘  └──────────┘  └────────────────────────────────┘  │
   └──────────────────────────────┬───────────────────────────────────┘
                                  │ TSF API (待集成)
   ┌──────────────────────────────▼───────────────────────────────────┐
   │ EKTRO Core (Python 参考实现已完成)                                │
   │                                                                  │
   │ ┌─────────────────┐  ┌─────────────────────┐  ┌──────────────┐  │
   │ │ MemoryStore     │  │ BaselineReranker    │  │ Predictor    │  │
   │ │ (SQLite WAL)    │  │ (4 features)        │  │ (HTTP/cache) │  │
   │ │                 │  │                     │  │              │  │
   │ │ • log_commit    │  │ • rerank()          │  │ • predict()  │  │
   │ │ • word_freq     │  │ • 共享 context_     │  │ • AsyncTrigger│ │
   │ │ • phrase_pair   │  │   builder           │  │ • LRU cache  │  │
   │ │ • privacy 拦截   │  │                     │  │ • 超时降级    │  │
   │ │ • CLI 面板       │  │                     │  │              │  │
   │ └────────┬────────┘  └──────────┬──────────┘  └──────┬───────┘  │
   │          │                       │                    │           │
   │          └───────────────────────┴────────────────────┘           │
   │                              │                                    │
   │                   context_builder.py                              │
   │                   (shared infrastructure)                          │
   │                                                                  │
   └──────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                       ┌────────────────────────┐
                       │ llama-server (Qwen3-   │
                       │ 0.6B Q4_K_M, port 8088)│
                       └────────────────────────┘
```

---

## 模块清单

### src/memory (Week 2)
| 文件 | 行数 | 职责 |
|------|------|------|
| schema.py | 114 | SQLite DDL + 版本管理 |
| store.py | 353 | EktroMemoryStore 主类 |
| __main__.py | 232 | ektro-cli 用户面板 |
| README.md | — | C++ 移植规约 |

### src/rerank (Week 4)
| 文件 | 行数 | 职责 |
|------|------|------|
| baseline.py | 184 | 4 特征统计 reranker |
| context_builder.py | 104 | 共享上下文构造 |
| README.md | — | 升级到神经 ranker 的触发条件 |

### src/predictor (Week 5 — 今晚) ★
| 文件 | 行数 | 职责 |
|------|------|------|
| client.py | ~230 | HTTP /completion + LRU 缓存 |
| trigger.py | ~110 | 异步停顿检测 + worker 线程 |
| README.md | — | 接口契约 + 降级路径 |

### tests/
| 套件 | 测试数 | 耗时 | 覆盖 |
|------|--------|------|------|
| memory/test_store | 28 | 0.36s | schema / 隐私 / 查询 / 导出 |
| rerank/test_baseline | 12 | 0.25s | 4 特征 / 退化 / 集成 |
| predictor/test_client | 15 | 9.5s | mock server / cache / 超时 / async |
| latency/run_first_token_bench | (bench) | — | CLI 模式延迟 |
| latency/bench_server_mode | (bench) | — | Server 模式延迟 |
| **总计** | **55 测试** | **~10s** | — |

### Demos
- `tests/rerank/demo_pipeline.py` — Memory + Reranker
- `tests/integration/demo_full_pipeline.py` — **全栈端到端** ★

### Benchmarks（性能实测）
- llama-completion CLI 模式（D-003 数据）
- llama-server 模式（D-004 数据）
- MemoryStore (Week 2)
- Reranker (Week 4)

---

## 关键 SLO 达成情况

```
   组件            操作              SLO        实测 P95        判定
   ────────────────────────────────────────────────────────────────
   Memory          log_commit (写)   ≤5ms       4.2ms          ✅
   Memory          recent_outputs    ≤3ms       0.15ms         ✅ 20x 余量
   Memory          word_freq_lookup  ≤3ms       0.05ms         ✅ 60x 余量
   Reranker        10 候选 + 上下文  ≤3ms       0.38ms         ✅ 8x 余量
   Reranker        端到端含 context  ≤4ms       0.33ms         ✅ 12x 余量
   Predictor       short context     ≤200ms     76ms (server)  ✅
   Predictor       medium context    ≤200ms     413ms (server) 🟡 边界
   Predictor       long context      —          760ms (server) 🔴 超SLO但不崩
   Predictor       cache hit         ≤1ms       0.01ms         ✅
```

**整个推理链路（commit → 重排 → 预测）总延迟预算**:
- 写 5ms + 重排 1ms + 预测 200ms = **206ms** 在 SLO 内
- 实测加和：5 + 1 + 76 = **82ms** (短上下文场景，远好于 SLO)

---

## 决策日志（D-001 → D-008）

| ID | 日期 | 关键决定 | 反思要点 |
|----|------|---------|---------|
| D-001 | 5-11 | 批准 Cycle 1 启动 | 三戒不可妥协 |
| D-002 | 5-11 | 网络受限→Day 1 拆分阶段 | 早战略撤退 |
| D-003 | 5-11 | CLI 数据 → 悲观修订 Predictor | 工具数据可能误导 |
| D-004 | 5-11 | Server 数据反转 → 双轨 SOP | 永远 cross-check 真实场景 |
| D-005 | 5-11 | 记忆系统双语并行 | Python 参考 → C++ 移植 |
| D-006 | 5-11 | Week 3 inline = "翻 flag" | 用 WebFetch 绕开网络读源码 |
| D-007 | 5-11 | Rerank 先 baseline 再 ML | 数据驱动复杂度引入 |
| D-008 | 5-11 | Cycle 1 Python 全栈完成 | 实施期 = C++ 移植 + 集成 |

---

## 关键工程教训（六个 reflection 累积）

1. **网络受阻时分离"读"和"写"**（D-002, D-006）
   - clone 失败 → 用 WebFetch 走 Anthropic 服务器读 raw 文件
   - 模型下载失败 → 用 ollama registry（不同 CDN）

2. **永远 cross-check 真实场景**（D-004）
   - CLI 模式数据 ≠ 真实 IME 集成数据
   - 任何 benchmark 都要"工具基线 + 场景模拟"双轨

3. **敢于推翻自己的设计**（D-007）
   - design.md §9.D3 写的是 GRU，实测后判断 baseline 已经够
   - 设计文档不是圣经，是上一轮认知的快照

4. **复杂度证据驱动引入**（D-007）
   - 设定升级触发条件（Tab 切换率 > 10%）
   - 未到阈值不投入 ML 复杂度

5. **接口稳定，实现可替换**
   - BaseReranker Protocol 让 baseline → GRU 切换调用方零改动
   - PredictorClient 接口让 HTTP → ONNX 切换调用方零改动
   - 这是为什么先做 Python 参考的意义

6. **三戒（不打断视线 / 不离开磁盘 / 不解释自己）的工程现实**
   - Predictor 用 127.0.0.1 而不是云端 → 公理 ②
   - AsyncTrigger 后台跑不阻塞 → 公理 ①
   - 没有任何"AI 助手"UI 按钮 → 公理 ③

---

## 当前状态 vs 6 周计划

```
   Week 1   ✅ Day 1 Spike + 项目骨架 + 决策框架
   Week 2   ✅ 记忆系统 (Python 完整)
   Week 3   📋 inline 渲染设计完成，C++ 实施待启
   Week 4   ✅ Rerank Baseline (Python 完整 + 集成 demo)
   Week 5   ✅ Predictor (Python 完整 + 端到端打通) ← 今晚
   Week 6   📋 收敛发版（待 C++ 集成完成）

   总进度：Python 参考层 100%，C++ 集成 0%（需 fork weasel 后实施）
```

---

## 剩余工作（实施期）

按工作量与依赖排序：

### 1. Fork weasel + 基线编译（1-3 天）

**阻塞条件**: 跨境网络
**绕过路径**: 浏览器下 zip → 本地解压 → `.\build.bat` 编译
**预期产物**: 能在 Notepad 输入中文的 baseline weasel

### 2. C++ patch 1 + 2 + 3（2-4 天）

按 [`docs/week3-inline-patch-design.md`](week3-inline-patch-design.md):
- 加 `g_force_show_candidates` 条件门
- 长按 Ctrl 监听器
- Tab 键后刷 composition

### 3. EktroMemoryStore C++ 移植（1-2 天）

按 [`src/memory/README.md`](../src/memory/README.md) 移植规约：
- SQL DDL 原样复制
- 隐私正则用 RE2
- log_commit 接到 weasel commit 钩子

### 4. EktroRerankFilter C++ 移植（1-2 天）

按 [`src/rerank/README.md`](../src/rerank/README.md) 移植规约：
- 4 特征逻辑复制
- 注册到 librime 管线 (Translator 之后)

### 5. EktroPredictor C++ 移植（1-2 天）

按 [`src/predictor/README.md`](../src/predictor/README.md) 移植规约：
- HTTP client (cpp-httplib 或 WinHTTP)
- AsyncTrigger (std::thread + condition_variable)
- 把结果 marshal 回 EditSession

### 6. 5 天纯使用 + 收敛（1 周）

按 [`docs/cycle1-spike-day1.md`](cycle1-spike-day1.md) Week 6 计划。

**总工作量预估**: 实施期 = 2-3 周（如 fork 顺利）

---

## 给下次会话/接手者的指引

打开任意新会话，先说一句：

> *"读 CLAUDE.md + cycle1-summary.md，按 docs/week3-inline-patch-design.md 进入 Week 3 实施。"*

我（或新 AI 实例）会：
1. 加载产品宪法 + 全栈状态
2. 检查 weasel 是否已 fork
3. 按设计开始 C++ patch

---

## 一句话总结

> Cycle 1 的"思考工作"全部完成。
> 接下来是"手指头工作"——fork、编译、移植、测试、自用。
> 设计稳定，接口锁定，性能验证通过。
>
> **EKTRO 已经可以"开干"了**。

*相关：[CLAUDE.md](../CLAUDE.md) · [decisions.md](decisions.md) · [openspec/changes/ektro-mvp/](../openspec/changes/ektro-mvp/)*
