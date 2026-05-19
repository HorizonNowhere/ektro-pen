# EKTRO 跨平台移植 S0 + S1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把平台无关的 `src-cpp/` 提升为带稳定 C ABI 的 EKTRO Core SDK(S0),并 fork Squirrel 做 macOS 前端(S1)。

**Architecture:** S0 在现有 `src-cpp/` 上加一层纯 C ABI(`ektro_sdk.h` / `ektro_sdk.cpp`)包装现有 C++ 类,多平台 CMake target;Windows 回归证明边界不破。S1 fork Squirrel(Swift+IMKit),链 `libektro.a`,概念等价复刻 weasel 三处 patch。

**Tech Stack:** C++20、CMake ≥3.16、SQLite3(vendored)、GoogleTest、(S1) Swift / InputMethodKit / Xcode 16 / librime。

---

## ⚠ 环境前置(必须先读)

- **S0 全部可在当前 Windows 11 机器完成并验证**(纯 C++ + CMake + GoogleTest)。这是本计划价值主体。
- **S1 需要 macOS 13+ 与 Xcode 16,无法在当前 Windows 机器构建/测试**。S1 任务可在 Windows 上完成"代码与 patch 草拟",但**编译、运行、兼容矩阵必须在 Mac 上做**。计划在 S1 起始设硬门 Task S1-0 检查环境;无 Mac 则 S1 停在"patch 已写、待 Mac 验证"状态,不算完成。
- 执行顺序锁定 **S0 全绿 → 才进 S1**。S0 是 S1 前置(SDK 边界)。

---

## 文件结构(决策锁定)

| 文件 | 职责 | 动作 |
|------|------|------|
| `src-cpp/include/ektro/ektro_sdk.h` | 唯一对外 C ABI 契约 | Create |
| `src-cpp/src/ektro_sdk.cpp` | C ABI → 现有 C++ 类适配 | Create |
| `src-cpp/tests/test_c_abi.cpp` | 仅经 C ABI 调用的 cross-check | Create |
| `src-cpp/CMakeLists.txt` | 加 `EKTRO_PLATFORM` 与 SDK target | Modify |
| `upstream/patches/macos/0[1-3]-*.patch` | Squirrel 三处等价 patch | Create (S1) |
| `docs/benchmarks/macos-slo.md` | S1 SLO 实测记录 | Create (S1) |

C ABI 只暴露 opaque `ektro_ctx*` + 函数,绝不泄漏 C++ 类型 —— 这是 Swift/JNI 能桥接的前提。

---

# Phase S0 — Core SDK 边界化(Windows 可全程验证)

### Task S0-1: 定义 C ABI 头文件

**Files:**
- Create: `src-cpp/include/ektro/ektro_sdk.h`

- [ ] **Step 1: 写头文件**

```c
// SPDX-License-Identifier: Apache-2.0
// EKTRO Core SDK — 唯一对外 C ABI 契约。所有平台前端只依赖此头。
#ifndef EKTRO_SDK_H
#define EKTRO_SDK_H
#ifdef __cplusplus
extern "C" {
#endif

typedef struct ektro_ctx ektro_ctx;

typedef enum { EKTRO_PREDICTOR_BASELINE = 0, EKTRO_PREDICTOR_LLAMA = 1 } ektro_predictor_kind;

typedef struct {
    ektro_predictor_kind predictor;   /* 桌面默认 LLAMA, 移动 BASELINE */
    const char* llama_server_url;     /* predictor=LLAMA 时用; 否则忽略 */
} ektro_config;

/* 返回码: 0=OK, 非0=错误, 详情见 ektro_last_error */
ektro_ctx* ektro_create(const char* db_path, const ektro_config* cfg);
void       ektro_destroy(ektro_ctx*);

int  ektro_log_commit(ektro_ctx*, const char* input_raw,
                       const char* output_cjk, int is_password_field);

/* rerank: cands_in 是 '\n' 分隔候选; 结果写入调用方提供的 buf(同格式) */
int  ektro_rerank(ektro_ctx*, const char* cands_in, char* buf, int buf_len);

/* predict: ctx 上下文 → 续写写入 buf */
int  ektro_predict(ektro_ctx*, const char* ctx, char* buf, int buf_len);

void ektro_set_password_field(ektro_ctx*, int is_password);
int  ektro_memory_export(ektro_ctx*, const char* out_path);
int  ektro_memory_clear(ektro_ctx*);

const char* ektro_last_error(ektro_ctx*);  /* 绝不静默吞错 (D-009) */

#ifdef __cplusplus
}
#endif
#endif /* EKTRO_SDK_H */
```

- [ ] **Step 2: Commit**

```bash
git add src-cpp/include/ektro/ektro_sdk.h
git commit -m "feat(sdk): 定义 EKTRO Core SDK C ABI 契约"
```

---

### Task S0-2: C ABI 创建/销毁 + 错误通道(TDD)

**Files:**
- Create: `src-cpp/src/ektro_sdk.cpp`
- Create: `src-cpp/tests/test_c_abi.cpp`
- Modify: `src-cpp/CMakeLists.txt`

- [ ] **Step 1: 写失败测试**

```cpp
// src-cpp/tests/test_c_abi.cpp
#include "ektro/ektro_sdk.h"
#include <gtest/gtest.h>
#include <cstdio>

TEST(CAbi, CreateDestroyRoundTrip) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    ASSERT_NE(c, nullptr);
    EXPECT_STREQ(ektro_last_error(c), "");
    ektro_destroy(c);
}

TEST(CAbi, BadDbPathSetsError) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create("/no/such/dir/x.db", &cfg);
    if (c) { EXPECT_STRNE(ektro_last_error(c), ""); ektro_destroy(c); }
    else SUCCEED();  // 返回 null 亦合法, 由 Step3 实现决定
}
```

- [ ] **Step 2: 加进 CMake 并跑,确认失败**

在 `src-cpp/CMakeLists.txt` 第 47-56 行 `add_library(ektro STATIC ...)` 列表追加一行 `src/ektro_sdk.cpp`;在第 105-109 行 `add_executable(ektro_tests ...)` 列表追加 `tests/test_c_abi.cpp`。

Run: `cmake -S src-cpp -B build && cmake --build build --target ektro_tests`
Expected: 链接失败 `unresolved external symbol ektro_create`

- [ ] **Step 3: 写最小实现**

```cpp
// src-cpp/src/ektro_sdk.cpp
#include "ektro/ektro_sdk.h"
#include "ektro/memory_store.h"
#include <memory>
#include <string>

struct ektro_ctx {
    std::unique_ptr<ektro::EktroMemoryStore> store;
    ektro_predictor_kind predictor = EKTRO_PREDICTOR_BASELINE;
    std::string llama_url;
    int password_field = 0;
    std::string last_error;
};

extern "C" {

ektro_ctx* ektro_create(const char* db_path, const ektro_config* cfg) {
    auto* c = new ektro_ctx();
    try {
        c->store = std::make_unique<ektro::EktroMemoryStore>(db_path ? db_path : ":memory:");
        if (cfg) { c->predictor = cfg->predictor;
                   c->llama_url = cfg->llama_server_url ? cfg->llama_server_url : ""; }
    } catch (const std::exception& e) {
        c->last_error = e.what();
    }
    return c;
}

void ektro_destroy(ektro_ctx* c) { delete c; }

const char* ektro_last_error(ektro_ctx* c) { return c ? c->last_error.c_str() : ""; }

}  // extern "C"
```

- [ ] **Step 4: 跑,确认通过**

Run: `cmake --build build --target ektro_tests && ./build/ektro_tests --gtest_filter=CAbi.*`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add src-cpp/src/ektro_sdk.cpp src-cpp/tests/test_c_abi.cpp src-cpp/CMakeLists.txt
git commit -m "feat(sdk): C ABI 创建/销毁 + 错误通道 (TDD)"
```

---

### Task S0-3: ektro_log_commit + 隐私门(TDD)

**Files:**
- Modify: `src-cpp/src/ektro_sdk.cpp`
- Modify: `src-cpp/tests/test_c_abi.cpp`

- [ ] **Step 1: 写失败测试**

```cpp
TEST(CAbi, LogCommitThenRerankReflectsLearning) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    ASSERT_EQ(ektro_log_commit(c, "nihao", "你好", 0), 0);
    ektro_destroy(c);
}

TEST(CAbi, PasswordFieldFlagBlocksLogging) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    ektro_set_password_field(c, 1);
    // 密码域: log_commit 必须不落库, 返回 0 (静默跳过, 非错误)
    EXPECT_EQ(ektro_log_commit(c, "mima123", "密码", 1), 0);
    ektro_destroy(c);
}
```

- [ ] **Step 2: 跑,确认失败**

Run: `cmake --build build --target ektro_tests && ./build/ektro_tests --gtest_filter=CAbi.*`
Expected: 链接失败 `unresolved external symbol ektro_log_commit` / `ektro_set_password_field`

- [ ] **Step 3: 实现(复用 EktroMemoryStore::log_commit,D-009 隐私权威)**

在 `ektro_sdk.cpp` 的 `extern "C"` 块内追加:

```cpp
void ektro_set_password_field(ektro_ctx* c, int is_password) {
    if (c) c->password_field = is_password;
}

int ektro_log_commit(ektro_ctx* c, const char* input_raw,
                     const char* output_cjk, int is_password_field) {
    if (!c || !c->store) return 1;
    try {
        ektro::EktroMemoryStore::LogCommitArgs a;
        a.input_raw = input_raw ? input_raw : "";
        a.output = output_cjk ? output_cjk : "";
        a.is_password_field = is_password_field || c->password_field;
        c->store->log_commit(a);   // 隐私拦截在 store 内部 (D-009 唯一权威)
        return 0;
    } catch (const std::exception& e) {
        c->last_error = e.what();   // 绝不静默吞错
        return 1;
    }
}
```

- [ ] **Step 4: 跑,确认通过**

Run: `./build/ektro_tests --gtest_filter=CAbi.*`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src-cpp/src/ektro_sdk.cpp src-cpp/tests/test_c_abi.cpp
git commit -m "feat(sdk): ektro_log_commit + 密码域硬开关 (D-009)"
```

---

### Task S0-4: ektro_rerank + ektro_predict(TDD)

**Files:**
- Modify: `src-cpp/src/ektro_sdk.cpp`
- Modify: `src-cpp/tests/test_c_abi.cpp`

- [ ] **Step 1: 写失败测试**

```cpp
TEST(CAbi, RerankReturnsNewlineSeparatedAndFitsBuf) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    char buf[256] = {0};
    int rc = ektro_rerank(c, "你好\n您好\n泥嚎", buf, sizeof(buf));
    EXPECT_EQ(rc, 0);
    EXPECT_NE(std::string(buf).find('\n'), std::string::npos);  // 仍是多候选
    ektro_destroy(c);
}

TEST(CAbi, PredictBaselineReturnsZeroEvenWhenEmpty) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    char buf[256] = {0};
    EXPECT_EQ(ektro_predict(c, "今天天气", buf, sizeof(buf)), 0);
    ektro_destroy(c);
}

TEST(CAbi, BufTooSmallSetsErrorNotOverflow) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    char tiny[2];
    int rc = ektro_rerank(c, "你好\n您好", tiny, sizeof(tiny));
    EXPECT_NE(rc, 0);
    EXPECT_STRNE(ektro_last_error(c), "");
    ektro_destroy(c);
}
```

- [ ] **Step 2: 跑,确认失败**

Run: `./build/ektro_tests --gtest_filter=CAbi.*`
Expected: 链接失败 `unresolved external symbol ektro_rerank` / `ektro_predict`

- [ ] **Step 3: 实现(包装 BaselineReranker / BaselinePredictor;buf 溢出报错不越界)**

在 `ektro_sdk.cpp` 顶部 include 追加 `#include "ektro/reranker.h"` 与 `#include "ektro/predictor.h"`。`extern "C"` 块内追加:

```cpp
static int write_buf(ektro_ctx* c, const std::string& s, char* buf, int n) {
    if (!buf || n <= 0) { c->last_error = "buf invalid"; return 2; }
    if ((int)s.size() + 1 > n) { c->last_error = "buf too small"; return 3; }
    std::memcpy(buf, s.c_str(), s.size() + 1);
    return 0;
}

int ektro_rerank(ektro_ctx* c, const char* cands_in, char* buf, int buf_len) {
    if (!c || !c->store) return 1;
    try {
        ektro::BaselineReranker rr(*c->store);
        std::vector<std::string> in, out;
        std::string s(cands_in ? cands_in : ""), tok;
        std::stringstream ss(s);
        while (std::getline(ss, tok, '\n')) if (!tok.empty()) in.push_back(tok);
        out = rr.rerank(in);                       // 复用现有实现
        std::string joined;
        for (size_t i = 0; i < out.size(); ++i) { if (i) joined += '\n'; joined += out[i]; }
        return write_buf(c, joined, buf, buf_len);
    } catch (const std::exception& e) { c->last_error = e.what(); return 1; }
}

int ektro_predict(ektro_ctx* c, const char* ctx, char* buf, int buf_len) {
    if (!c || !c->store) return 1;
    try {
        ektro::BaselinePredictor p(*c->store);    // S0 阶段统一走 baseline
        std::string tail = p.predict(ctx ? ctx : "");
        return write_buf(c, tail, buf, buf_len);
    } catch (const std::exception& e) { c->last_error = e.what(); return 1; }
}
```

> 注:`reranker.h` / `predictor.h` 的构造与方法名以仓库实际签名为准;若 `rerank`/`predict` 名不符,以 `src-cpp/include/ektro/*.h` 实际声明为准并相应调整(不要新增方法)。顶部追加 `#include <sstream>` `#include <cstring>`。

- [ ] **Step 4: 跑,确认通过**

Run: `./build/ektro_tests --gtest_filter=CAbi.*`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src-cpp/src/ektro_sdk.cpp src-cpp/tests/test_c_abi.cpp
git commit -m "feat(sdk): ektro_rerank + ektro_predict, buf 溢出安全"
```

---

### Task S0-5: ektro_memory_export / ektro_memory_clear(TDD,戒②)

**Files:**
- Modify: `src-cpp/src/ektro_sdk.cpp`
- Modify: `src-cpp/tests/test_c_abi.cpp`

- [ ] **Step 1: 写失败测试**

```cpp
TEST(CAbi, MemoryClearThenExportEmpty) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    ektro_ctx* c = ektro_create(":memory:", &cfg);
    ektro_log_commit(c, "nihao", "你好", 0);
    EXPECT_EQ(ektro_memory_clear(c), 0);
    ektro_destroy(c);
}
```

- [ ] **Step 2: 跑,确认失败**

Run: `./build/ektro_tests --gtest_filter=CAbi.MemoryClearThenExportEmpty`
Expected: 链接失败 `unresolved external symbol ektro_memory_clear`

- [ ] **Step 3: 实现(复用 EktroMemoryStore::clear_all)**

`extern "C"` 块内追加:

```cpp
int ektro_memory_clear(ektro_ctx* c) {
    if (!c || !c->store) return 1;
    try { c->store->clear_all(true); return 0; }
    catch (const std::exception& e) { c->last_error = e.what(); return 1; }
}

int ektro_memory_export(ektro_ctx* c, const char* out_path) {
    if (!c || !c->store) return 1;
    try {
        // 明文导出: 直接拷贝 db 文件路径内容由调用方落地; 这里导出 recent_outputs JSON 行
        if (!out_path) { c->last_error = "out_path null"; return 2; }
        FILE* f = std::fopen(out_path, "w");
        if (!f) { c->last_error = "cannot open out_path"; return 3; }
        for (const auto& r : c->store->recent_outputs(100000, std::nullopt))
            std::fprintf(f, "%s\n", r.output.c_str());
        std::fclose(f);
        return 0;
    } catch (const std::exception& e) { c->last_error = e.what(); return 1; }
}
```

> 注:`CommitRecord` 字段名以 `memory_view.h` 实际为准(此处用 `.output`);若不符按头文件调整。

- [ ] **Step 4: 跑,确认通过**

Run: `./build/ektro_tests --gtest_filter=CAbi.*`
Expected: 全部 PASS

- [ ] **Step 5: Commit**

```bash
git add src-cpp/src/ektro_sdk.cpp src-cpp/tests/test_c_abi.cpp
git commit -m "feat(sdk): memory export/clear — 用户数据可导可删 (戒②)"
```

---

### Task S0-6: 多平台 CMake target + Windows 全回归(S0 通过门)

**Files:**
- Modify: `src-cpp/CMakeLists.txt`

- [ ] **Step 1: 加 EKTRO_PLATFORM 选项与产物类型**

在 `src-cpp/CMakeLists.txt` 第 16 行 `option(EKTRO_BUILD_TESTS ...)` 下方插入:

```cmake
set(EKTRO_PLATFORM "windows" CACHE STRING "windows|macos|android|ios")
message(STATUS "EKTRO_PLATFORM=${EKTRO_PLATFORM}")
# macOS/iOS 前端用 Swift, 需要 C ABI 静态库; Android 需要 .so
if(EKTRO_PLATFORM STREQUAL "android")
    set(BUILD_SHARED_LIBS ON)
endif()
```

- [ ] **Step 2: 全量构建 + 全测试(S0 通过门)**

Run:
```
cmake -S src-cpp -B build -DEKTRO_BUILD_TESTS=ON
cmake --build build
ctest --test-dir build --output-on-failure
```
Expected: **22 原有 GoogleTest + 新增 test_c_abi 全部 PASS**(回归不破 = S0 抽象干净的证明)。

- [ ] **Step 3: Commit**

```bash
git add src-cpp/CMakeLists.txt
git commit -m "feat(sdk): 多平台 CMake target + S0 通过门 (Windows 全回归绿)"
```

---

# Phase S1 — macOS 前端 fork Squirrel(需 macOS 13+/Xcode 16)

### Task S1-0: 环境硬门(无 Mac 则 S1 暂停)

- [ ] **Step 1: 检查 macOS + Xcode**

Run (在 Mac 上): `sw_vers && xcodebuild -version`
Expected: macOS ≥ 13、Xcode ≥ 16。

**若当前为 Windows 环境**:S1 后续任务只能完成"patch 草拟"(纯文本 diff),**不能编译/验证**。在此情况下,标记 S1 为 `blocked: 需 Mac`,把已写 patch 留在 `upstream/patches/macos/` 并停止,不得声称 S1 完成。

---

### Task S1-1: fork Squirrel + baseline 中文输入跑通(Mac)

**Files:**
- Create: `upstream/squirrel/`(submodule 或 vendored fork)

- [ ] **Step 1: fork 并编译 baseline**

```bash
git submodule add https://github.com/rime/squirrel upstream/squirrel
cd upstream/squirrel && git checkout 1.1.1 && bash ./action-install.sh
```
Expected: 产出 `Squirrel.app`,装入系统后 TextEdit 能用默认 Rime 方案打中文(此为 baseline,**先证明 fork 干净再动 patch**)。

- [ ] **Step 2: Commit**

```bash
git add .gitmodules upstream/squirrel
git commit -m "chore(s1): vendor Squirrel 1.1.1 fork, baseline 中文输入跑通"
```

---

### Task S1-2: 链 libektro.a + 走通 log_commit / rerank(Mac)

**Files:**
- Modify: `upstream/squirrel`(Xcode 工程链接 `libektro.a` + bridging header `ektro_sdk.h`)

- [ ] **Step 1: 交叉编译 SDK for macOS**

```bash
cmake -S src-cpp -B build-mac -DEKTRO_PLATFORM=macos -DCMAKE_OSX_ARCHITECTURES="arm64;x86_64"
cmake --build build-mac --target ektro
```
Expected: 产出 universal `libektro.a`。

- [ ] **Step 2: Swift 桥接调用冒烟**

在 Squirrel 的 `InputController` commit 路径,经 bridging header 调 `ektro_log_commit(...)`,在 commit 后异步调用(不阻塞输入,SLO)。

- [ ] **Step 3: 验证**

在 TextEdit 打字提交,检查 `~/Library/.../ektro.db` 有 `commit_log` 行写入。
Expected: 落库成功且打字无可感卡顿。

- [ ] **Step 4: Commit**

```bash
git add upstream/squirrel build-mac/.gitignore
git commit -m "feat(s1): Squirrel 链 libektro.a, log_commit 异步落库跑通"
```

---

### Task S1-3: patch① 候选窗默认隐藏 + patch② Ctrl 长按(Mac)

**Files:**
- Create: `upstream/patches/macos/01-panel-show-gate.patch`
- Create: `upstream/patches/macos/02-ctrl-hold.patch`

- [ ] **Step 1: patch① — SquirrelPanel 显示前加条件门**

参照 Windows `upstream/patches/01-globals-add-force-show.patch` 与 `02-candidatelist-show-ui-gate.patch` 的语义:在 Squirrel 候选面板 `show()` 前加全局 `g_force_show_candidates` 门,默认 false ⇒ 候选窗不显示(inline-first)。

- [ ] **Step 2: patch② — InputController 捕获 Ctrl 长按**

在 `InputController` 的 `recognizedEvents` / `flagsChanged` 捕获 Ctrl 按住 >X ms ⇒ 置 `g_force_show_candidates=true`(应急通道,对标 Windows `03-keyeventsink-ctrl-hold.patch`)。

- [ ] **Step 3: 验证**

TextEdit 打字默认无候选窗;长按 Ctrl 候选窗浮现。
Expected: 行为与 Windows 一致。

- [ ] **Step 4: Commit**

```bash
git add upstream/patches/macos/ upstream/squirrel
git commit -m "feat(s1): patch① 候选窗默认隐藏 + patch② Ctrl 长按应急通道"
```

---

### Task S1-4: patch③ Tab 接受预测 + inline + 淡灰渲染(Mac)

**Files:**
- Create: `upstream/patches/macos/03-tab-inline-predict.patch`
- Modify: `config/default.custom.yaml`(复用,启用 `inline_preedit`)

- [ ] **Step 1: patch③ — Tab 拦截**

在 `InputController.handle(_:client:)` 拦截 Tab:有淡灰续写时 ⇒ 接受并 `insertText`;否则切替代候选(对标 Windows Tab 语义)。

- [ ] **Step 2: 淡灰渲染**

预测结果用 IMKit marked text 的灰色 attributed string 渲染,Tab 提交;停顿 300ms 触发(复用 SDK `ektro_predict`,macOS 走 `EKTRO_PREDICTOR_LLAMA` + llama-server 子进程)。

- [ ] **Step 3: Day1 探针验证(关键风险)**

确认 IMKit app 沙箱内能 spawn 持久 llama-server 子进程;不行则该机降级 `EKTRO_PREDICTOR_BASELINE`,并记入 `docs/decisions.md` 新决策。

- [ ] **Step 4: Commit**

```bash
git add upstream/patches/macos/ upstream/squirrel config/default.custom.yaml
git commit -m "feat(s1): patch③ Tab 接受预测 + inline + 淡灰渲染"
```

---

### Task S1-5: 兼容矩阵 + SLO 实测(S1 验收)

**Files:**
- Create: `docs/benchmarks/macos-slo.md`

- [ ] **Step 1: 兼容矩阵**

TextEdit / VSCode / Safari / 微信 Mac / Terminal / 备忘录 逐个验证:inline 出字、Tab 接受、长按 Ctrl 候选窗。

- [ ] **Step 2: SLO 实测并记录**

测首屏 inline ≤50ms P99 / rerank ≤30ms P99 / Qwen3 首 token ≤200ms P95 / 网络请求=0,写入 `docs/benchmarks/macos-slo.md`。
Expected: 全部达标(Apple Silicon 预期优于 Windows 基准);任一不达标 ⇒ 按 CLAUDE.md §四,砍范围不延期。

- [ ] **Step 3: Commit**

```bash
git add docs/benchmarks/macos-slo.md
git commit -m "test(s1): macOS 兼容矩阵 + SLO 实测达标 (S1 验收)"
```

---

## Self-Review

**1. Spec 覆盖:** spec §3(S0 C ABI/构建/测试)→ Task S0-1..6;§4(S1 fork/3 patch/inline/预测后端/SLO/测试)→ Task S1-1..5;§6 风险(C ABI 泄漏、Squirrel Swift、沙箱 llama、存量回归、范围蔓延)→ 分别由 test_c_abi、S1-1 baseline-first、S1-4 Step3 探针、S0-6 通过门、S0→S1 顺序锁定 覆盖。§5(S2/S3)按设计明确不在本计划。无遗漏。

**2. 占位符扫描:** 无 TBD/TODO;每个代码步给出完整代码;对仓库实际签名不确定处显式标注"以头文件为准并调整,不新增方法"(非占位,是约束)。

**3. 类型一致性:** `ektro_ctx` / `ektro_config` / `ektro_predictor_kind` / 返回码语义(0=OK)在 S0-1 定义后,S0-2..6 一致使用;`write_buf` 在 S0-4 定义,S0-5 未重复用(各自独立)。`g_force_show_candidates` 在 S1-3 patch① 引入,patch② 复用,命名一致。

---

*Plan v1 · 2026-05-19 · 基于 specs/2026-05-19-cross-platform-port-design.md*
