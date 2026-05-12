# EKTRO Cycle 2 — C++ 移植指南

> 把 src/*.py 的 Python 参考实现移植成 C++，集成进 weasel/librime。

---

## 0. 移植原则（来自 D-005 / D-009 / D-011 累积）

1. **字段名 1:1 对应 Python**（让 git diff 能用 Python 测试同名 case）
2. **错误信号用 enum class**，不用 magic int 或 nullptr
3. **接口契约由 protocols.py 定义**（C++ 端是 pure virtual class）
4. **跑等价的 C++ 测试** cross-check 与 Python 行为一致（重点：边界 + 并发 + 隐私）
5. **不引入第三方依赖**（除非已在 weasel 用：boost, sqlite3, librime）

---

## 1. 文件对照表

| Python | C++ Header | C++ Impl | 移植难度 |
|--------|-----------|----------|---------|
| `src/memory/schema.py` | `include/ektro/schema.h` (待写) | `src/sqlite_schema.cpp` | Easy（DDL 字符串原样复制） |
| `src/memory/store.py` | `include/ektro/memory_store.h` (待写) | `src/memory_store.cpp` | Medium（线程安全 + 隐私拦截） |
| `src/shared/protocols.py` MemoryView | `include/ektro/memory_view.h` ✓ | — (interface only) | 已写 |
| `src/shared/protocols.py` Reranker | `include/ektro/reranker.h` ✓ | — | 已写 |
| `src/shared/protocols.py` BasePredictor | `include/ektro/predictor.h` ✓ | — | 已写 |
| `src/memory/store.py` LogResult | `include/ektro/log_result.h` ✓ | — | 已写 |
| `src/rerank/baseline.py` | (in reranker.h) | `src/baseline_reranker.cpp` | Medium |
| `src/predictor/client.py` PredictionResult | (in predictor.h) | — | 已写 |
| `src/predictor/client.py` PredictorClient | (in predictor.h) | `src/predictor_client.cpp` | Hard（HTTP + LRU + 缓存） |
| `src/predictor/baseline.py` | (in predictor.h) | `src/baseline_predictor.cpp` | Easy（贪心 phrase_pair 链） |
| `src/predictor/trigger.py` AsyncTrigger | (in predictor.h) | `src/async_trigger.cpp` | Hard（线程 + atomic task_id） |
| `src/shared/context_builder.py` | (待写 context_builder.h) | `src/context_builder.cpp` | Easy |
| `src/common/logging.py` | `include/ektro/log.h` (待写) | `src/log.cpp` | Easy（用 spdlog 或自写） |
| `src/memory/__main__.py` (CLI) | (CLI 不移植) | — | 保留 Python 当工具 |

---

## 2. 依赖选型

| 用途 | Python | C++ |
|------|--------|-----|
| SQLite | 标准库 `sqlite3` | `sqlite3.h` (weasel 已用) |
| HTTP client | `urllib.request` | **cpp-httplib** (header-only, 5K LOC) |
| JSON | `json` | **nlohmann/json** (header-only) |
| 线程 | `threading` | `std::thread` / `std::condition_variable` |
| 原子 task_id | `itertools.count` | `std::atomic<uint64_t>` |
| LRU 缓存 | 自写 dict-based | `std::list` + `std::unordered_map` |
| 日志 | 内置 `logging` | **spdlog** (header-only) 或 weasel 用的 google-glog |
| Regex (隐私) | `re` | `std::regex` (慎防 ReDoS) 或 **RE2** |
| Tests | `unittest` | **GoogleTest** (weasel 已用) |

**所有推荐第三方库都是 header-only 或 weasel 已用**，不增加构建复杂度。

---

## 3. 移植阶段路线（Cycle 2 第一周）

```
Day 1  ▸ fork weasel + librime submodule
       ▸ baseline 编译跑通（boost + librime + weasel）
       ▸ 安装 baseline weasel 到 Notepad 测试中文输入

Day 2  ▸ 创建 src-cpp/ 加入 weasel CMake build
       ▸ 移植 schema.py → sqlite_schema.cpp
       ▸ 移植 store.py → memory_store.cpp（含线程锁 + WAL pragma）
       ▸ 跑等价 store 单元测试（GoogleTest）

Day 3  ▸ 移植 baseline.py (rerank) → baseline_reranker.cpp
       ▸ 集成到 librime 管线：实现 librime::Filter 子类调 BaselineReranker
       ▸ 跑等价 rerank 单元测试

Day 4  ▸ 移植 trigger.py + client.py → async_trigger.cpp + predictor_client.cpp
       ▸ 注意 task_id 必须 std::atomic<uint64_t>（D-011 P1.1）
       ▸ 跑等价 trigger 竞态测试

Day 5  ▸ 把 _ShowUI 加 g_force_show_candidates 条件门 (week3-inline-patch-design.md §2.2)
       ▸ 长按 Ctrl 监听器
       ▸ Tab 键后 _UpdateComposition
       ▸ default.custom.yaml 启用 inline_preedit
       ▸ 兼容矩阵测试（Notepad / VSCode / Chrome / 微信 / Edge / cmd）
```

---

## 4. 已知陷阱（来自前 11 次决策）

```
🔴 D-005: phrase_pair 是字粒度，不要私自改词粒度
🔴 D-009: 隐私检测正则只能用在 input_raw，不能用在 output (中文)
🔴 D-009: is_password_field 是唯一权威（TSF IS_PASSWORD scope）
🔴 D-011: AsyncTrigger task_id 必须 std::atomic<uint64_t>，不要用浮点 chrono::time_point
🔴 D-011: trigger 不派发 !is_ok 的 result 到 UI（公理 ①）
🔴 D-011: _update_word_freq/_phrase_pairs 用 SQL batch（不是 N 次单独 execute）
🔴 D-011: CLI 顶层 try/except，不让用户看 traceback
🔴 D-004: Predictor 必须 llama-server 持久进程，不能每次 spawn CLI
🔴 D-008: Qwen3-0.6B 输出质量必须经过质量门（纯 CJK 检测）
🔴 D-007: BaselinePredictor 是 LLM 失败时的救场；不要随便砍
```

---

## 5. 测试 cross-check 规约

C++ 实现完成后，每个模块都必须跑等价测试，与 Python 输出对照：

```
tests-cpp/
├── test_memory_store.cpp     ↔ tests/memory/test_store.py
├── test_boundary.cpp         ↔ tests/memory/test_boundary.py
├── test_concurrency.cpp      ↔ tests/memory/test_concurrency.py
├── test_baseline_rerank.cpp  ↔ tests/rerank/test_baseline.py
├── test_predictor.cpp        ↔ tests/predictor/test_client.py
└── test_trigger_race.cpp     ↔ tests/predictor/test_trigger_race.py
```

**通过门**：C++ 测试输出在等价 case 上与 Python 完全一致（数字 ±0.01，结构 100%）。

---

## 6. 与 weasel 集成的具体方式

按 `docs/week3-inline-patch-design.md`，**只改 weasel 三处** + 加 EKTRO 模块作为 librime filter：

1. **新增 librime 插件目录** `weasel/src/ektro_plugin/`
   - 包含 EKTRO 的 memory_store / baseline_reranker / predictor_client / async_trigger
   - 编译进 weasel 的 librime 二进制
2. **改 WeaselTSF/UI*.cpp**: `_ShowUI` 加 `g_force_show_candidates` 条件门
3. **改 WeaselTSF/KeyEventSink.cpp**: 长按 Ctrl + Tab 键拦截
4. **打包 `default.custom.yaml`** 启用 inline_preedit + 注册 ektro_rerank_filter

详见 `docs/week3-inline-patch-design.md`。

---

## 7. 头文件清单（已写 ✓ / 待写）

```
src-cpp/include/ektro/
├── memory_view.h    ✓ (D-011)
├── log_result.h     ✓ (D-011)
├── reranker.h       ✓ (D-011)
├── predictor.h      ✓ (D-011)
├── memory_store.h   ⏳ (Cycle 2 Day 2)
├── schema.h         ⏳ (Cycle 2 Day 2)
├── context_builder.h ⏳ (Cycle 2 Day 3)
└── log.h            ⏳ (Cycle 2 Day 2)

src-cpp/src/
├── (全部待写)
```

---

## 8. 相关文档

- 设计：[`openspec/changes/ektro-mvp/design.md`](../openspec/changes/ektro-mvp/design.md)
- inline patch：[`docs/week3-inline-patch-design.md`](../docs/week3-inline-patch-design.md)
- 决策日志：[`docs/decisions.md`](../docs/decisions.md) D-001 → D-011
- Python 参考：[`src/`](../src)
- 测试基线：[`tests/`](../tests)
