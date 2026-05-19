# EKTRO 跨平台移植设计 — macOS / Android / iOS

> 状态: 设计已批准 (用户授权自主完成, 2026-05-19)
> 范围: S0 (Core SDK 边界化) + S1 (macOS 首付子项目) 详细设计;S2/S3 仅给方向, 各自单独 spec
> 关联: `CLAUDE.md` 三戒 · `src-cpp/PORTING_GUIDE.md` · `docs/decisions.md` D-004/D-007/D-009/D-011

---

## 0. 一句话

把已经平台无关的 `src-cpp/` 正式提升为带稳定 C ABI 的「EKTRO Core SDK」,三个平台只写薄前端适配层。三戒逻辑全局只有一份。

---

## 1. 背景与约束

### 1.1 现状

- **Windows 版**: fork weasel (TSF 前端, Win32/C++) + librime + EKTRO 核心 (编译进 librime 二进制的插件)。
- **EKTRO 核心** (`src-cpp/`, ~2400 行): 纯 C++、header-only 依赖 (sqlite3 / cpp-httplib / nlohmann-json)、22 个 GoogleTest 通过。已经**事实上平台无关**, 只差正式边界。
- 模块: `memory_store` (SQLite 明文) · `baseline_reranker` (GRU 救场) · `predictor_client` (HTTP→llama-server) · `baseline_predictor` (纯 SQLite phrase_pair 链, D-007) · `async_trigger` · `context_builder`。

### 1.2 三戒合规检查 (前置, 不可妥协)

| 戒 | 桌面 (Win/macOS) | 移动 (Android/iOS) |
|----|------------------|---------------------|
| ① 不打断视线 | inline 渲染, 候选窗默认隐藏 — **完整保留** | 软键盘**保留候选条**(平台习惯), 但默认走 inline-first, 候选条作为辅助 — **妥协但不破戒** |
| ② 不离开磁盘 | SQLite 明文本地 — **完整保留** | 同左 — **完整保留** (iOS 走 App Group 容器, 仍在设备内) |
| ③ 不解释自己 | 无助手/桌宠 — **完整保留** | 同左 — **完整保留** |

**结论**: 移动端唯一妥协是"候选条不强行隐藏"(物理键盘没有的场景), 不触碰②③, ①降级而非违反。该妥协已经用户确认。

### 1.3 关键技术事实 (2026-05 核实)

- **macOS = Squirrel/鼠须管**: Rime 官方前端, 现已 **Swift + InputMethodKit (IMKit)**, v1.1.1 (2026-01-11), universal binary (arm64 + x86_64)。⇒ 三处 patch 是"概念等价、Swift 重写", 非字面移植 weasel C++。
- **Android = Trime/同文**: Java/Kotlin + JNI → librime。
- **iOS = Hamster/仓鼠** (社区, 无官方前端): 键盘扩展受 iOS 限制 — 脏内存约 **40–48MB**、总内存约 60–77MB、**禁止后台常驻进程**。
  - ⇒ Qwen3-0.6B (权重 350MB+) **物理不可能** 进 iOS 键盘扩展。
  - ⇒ llama-server 持久进程模型 (D-004) 在 iOS **被系统禁止**。
  - ⇒ iOS 预测**只能**用 `baseline_predictor` (纯 SQLite, 零模型)。

---

## 2. 整体拆解 (4 个独立子项目)

| # | 子项目 | 产出 | 依赖 | 本 spec 覆盖 |
|---|--------|------|------|--------------|
| **S0** | Core SDK 边界化 | `src-cpp` → 稳定 C ABI + 多平台 CMake target | 无 | ✅ 详细 |
| **S1** | macOS 前端 | fork Squirrel + 3 等价 Swift patch + 链 SDK | S0 | ✅ 详细 |
| **S2** | Android 前端 | fork Trime + JNI 调 SDK + 软键盘交互 + BaselinePredictor | S0 | ⏳ 方向 (单独 spec) |
| **S3** | iOS 前端 | fork Hamster + App Group 容器 + 仅 BaselinePredictor | S0 | ⏳ 方向 (单独 spec) |

S0 是全部前置。S1 风险最低、复用最高, 作为首个完整闭环, 同时**验证 S0 抽象是否真干净**。S2/S3 在 S1 落地后各自走 brainstorm→spec→plan。

---

## 3. S0 — EKTRO Core SDK 边界化

### 3.1 目标

`src-cpp/` 当前是"编译进 librime 的插件", 三平台若各自重复这种耦合会维护三份漂移核心。S0 把它提升为带**稳定 C ABI** 的库, 编译成各平台 target。

### 3.2 C ABI (建议初版, 实现期细化)

```c
/* ektro_sdk.h — 唯一对外契约。所有平台前端只依赖此头。 */
typedef struct ektro_ctx ektro_ctx;

ektro_ctx*  ektro_create(const char* db_path, const ektro_config* cfg);
void        ektro_destroy(ektro_ctx*);

/* 落库: 每次 commit 异步入 SQLite (不阻塞输入, SLO) */
int         ektro_log_commit(ektro_ctx*, const char* input_raw,
                             const char* output_cjk, int is_password_field);

/* rerank: 候选数组 in→out, ≤30ms P99 */
int         ektro_rerank(ektro_ctx*, ektro_cands* inout);

/* predict: 上下文→淡灰续写; 后端 (llama-server / baseline) 由 cfg 决定 */
int         ektro_predict(ektro_ctx*, const char* ctx, ektro_prediction* out);

/* 隐私: 密码域硬开关 (D-009, 唯一权威) */
void        ektro_set_password_field(ektro_ctx*, int is_password);

/* 用户数据可视/可导/可删 (戒②) */
int         ektro_memory_export(ektro_ctx*, const char* out_path);
int         ektro_memory_clear(ektro_ctx*);

const char* ektro_last_error(ektro_ctx*);   /* 绝不静默吞错, D-009 */
```

设计要点:
- **错误信号**: 返回 int 错误码 + `ektro_last_error()`, 不用 magic int / nullptr (沿用 PORTING_GUIDE 原则 2)。
- **预测后端可配置**: `ektro_config.predictor = LLAMA_SERVER | BASELINE`。桌面默认 `LLAMA_SERVER`, 移动编译期/运行期切 `BASELINE`(= 用户选的"更小模型", 代码已存在, 零新调研)。
- **线程模型不变**: 异步落库 + `async_trigger` 的 `std::atomic<uint64_t>` task_id (D-011) 全在 SDK 内, 前端无需感知。
- **C ABI 而非 C++**: 让 Swift (macOS/iOS) / JNI (Android) 都能干净桥接。C++ 实现内部不变。

### 3.3 构建

- `src-cpp/CMakeLists.txt` 增加 platform target:
  - `EKTRO_PLATFORM=windows|macos|android|ios`
  - macOS/iOS: 输出 static lib (`libektro.a`) + module map (供 Swift import)。
  - Android: 输出 `.so` (供 JNI `System.loadLibrary`)。
- 不新增第三方依赖 (PORTING_GUIDE 原则 5)。

### 3.4 测试 (S0 通过门)

- 现有 22 GoogleTest 全绿 (回归不破)。
- 新增 `tests-cpp/test_c_abi.cpp`: 仅通过 C ABI 调用, 验证与 C++ 直调结果一致 (数字 ±0.01, 结构 100%, 沿用 PORTING_GUIDE §5)。
- Windows 现有 weasel 集成改为通过 C ABI 调 (证明边界不破坏存量)。

---

## 4. S1 — macOS 前端 (fork Squirrel)

### 4.1 方案

直接类比技术决策 #1 ("fork weasel + librime")。macOS 上 = **fork Squirrel + librime**, EKTRO 核心作为 librime filter (同 Windows), 前端 Swift 改 3 处。

### 4.2 三处等价 patch (weasel C++ → Squirrel Swift 概念映射)

| # | weasel (Win, 已存在) | Squirrel (macOS, 待写) | 作用 |
|---|----------------------|------------------------|------|
| 1 | `_ShowUI` + `g_force_show_candidates` 门 | `SquirrelPanel` 显示前加同条件门 | 候选窗默认隐藏 |
| 2 | `KeyEventSink` 长按 Ctrl 监听 | `InputController` 的 `flagsChanged` / `recognizedEvents` 捕获 Ctrl 长按 | 应急候选窗通道 |
| 3 | `KeyEventSink` Tab 拦截 + `_UpdateComposition` | `InputController.handle(_:client:)` 拦截 Tab → 接受淡灰预测 / 切替代候选 | Tab 接受预测 / 切候选 |

patch 产物对应 `upstream/patches/0[1-3]-*.patch` 的 macOS 版, 放 `upstream/patches/macos/`。

### 4.3 inline 渲染

- Squirrel 走 IMKit `setMarkedText` / `insertText`, 配 `default.custom.yaml` 的 `inline_preedit` (与 Windows 同一配置, 复用 `config/default.custom.yaml`)。
- 淡灰续写: 用 marked text 的 attributed string (灰色) 渲染, Tab 提交。

### 4.4 预测后端

macOS 保留全功能: `predictor = LLAMA_SERVER`, llama-server 作为独立进程 (macOS 允许, 不同于 iOS)。复用 `predictor_client.cpp`。Qwen3-0.6B IQ4_XS GGUF 走 `data/models/`。

### 4.5 SLO (macOS 验收, 沿用 §四)

首屏 inline ≤50ms P99 · rerank ≤30ms P99 · Qwen3 首 token ≤200ms P95 · 网络请求 = 0。macOS Apple Silicon 预期优于 Windows 基准, 需实测记入 `docs/benchmarks/`。

### 4.6 测试

- 兼容矩阵 (对标 PORTING_GUIDE Day5): TextEdit / VSCode / Safari / 微信 Mac / Terminal / 备忘录。
- 退出标准 (对标 CLAUDE.md §五): 能在 macOS 整天打字不切回系统拼音; 学得会口头禅; 无崩溃/数据丢失。

---

## 5. S2 / S3 方向 (各自单独 spec, 不在本次实现范围)

### S2 Android (fork Trime)
- JNI 桥 `libektro.so`; 预测后端 = `BASELINE` (旗舰机可选 llama 后续评估)。
- 软键盘交互重设计: 无物理 Tab → Tab 语义映射为软键盘手势/按键 (单独 brainstorm)。
- 候选条保留 (三戒妥协, 已确认)。

### S3 iOS (fork Hamster)
- 键盘扩展内存墙: **仅** `BASELINE` 预测器, 无 llama, 无后台进程。
- SQLite 走 App Group 共享容器 (主 App ⇄ 键盘扩展), 仍在设备内 (戒②不破)。
- 上架审核 + 内存压测为最大风险, 单独 spec 评估。

---

## 6. 风险

| 风险 | 影响 | 缓解 |
|------|------|------|
| C ABI 边界泄漏 C++ 细节 | 前端桥接困难 | S0 用 `test_c_abi.cpp` 强制纯 C 调用验证 |
| Squirrel Swift 化后 patch 难度高于 weasel | S1 延期 | Day1 先 fork+编译跑通 baseline 中文输入再动 patch |
| macOS 沙箱限制 llama-server 子进程 | 预测不可用 | Day1 探针验证 IMKit app 能否 spawn 持久子进程; 不行则降级 BASELINE |
| 存量 Windows 集成被边界化破坏 | 回归 | S0 通过门要求 Windows 改走 C ABI 且 22 测试全绿 |
| 范围蔓延 (一次想做三端) | Shape Up 失控 | 严格 S0→S1 闭环, S2/S3 不在本 spec, 各自重走流程 |

---

## 7. 实现顺序 (交 writing-plans 细化)

```
S0  ▸ 定 ektro_sdk.h C ABI → 内部 C++ 适配 → 多平台 CMake target
    ▸ test_c_abi.cpp; Windows 改走 C ABI; 22+1 测试全绿  (S0 通过门)
S1  ▸ Day1 fork Squirrel + librime, 编译, baseline 中文输入跑通
    ▸ Day1 探针: llama-server 子进程在 IMKit 沙箱可行性
    ▸ Day2 链 libektro.a, 走通 ektro_log_commit / rerank
    ▸ Day3 patch①候选门 · patch②Ctrl 长按
    ▸ Day4 patch③Tab + inline_preedit + 淡灰渲染
    ▸ Day5 兼容矩阵 + SLO 实测 + 整天试用
```

S2 / S3: S1 验收通过后, 各自 brainstorm → spec → plan。

---

*Last updated: 2026-05-19 · 设计 v1*
