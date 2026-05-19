# EKTRO S2 — Android 前端设计 (fork Trime + JNI 绑定 ektro_sdk)

> 状态: 设计已认可 (用户授权, 2026-05-19)。S2 子项目,前置 = S0 (已完成并入 master)。
> 关联: `CLAUDE.md` 三戒 · `docs/superpowers/specs/2026-05-19-cross-platform-port-design.md` §S2 · S0 `ektro_sdk` C ABI

---

## 0. 一句话

Trime(同文)作为 fork 的 Android Rime 前端,通过新增薄 JNI 层调 S0 的 `ektro_sdk` C ABI;不重造交互,在 Trime 既有候选流程里插入 EKTRO 个性化智能。

---

## 1. 背景与约束

- **S0 已就位**: `ektro_sdk` 纯 C ABI 已并入 master(33 GoogleTest 全绿),`EKTRO_PLATFORM=android` CMake 选项已就绪(`BUILD_SHARED_LIBS ON`)。Android 前端有干净地基。
- **Trime 现状(2026-05 核实)**: v3.3.10(2026-05-02),Java/Kotlin + JNI → librime,JDK 17/21,`Rime` 类在 core 包。
- **环境约束**: 本机无 Android 工具链(JDK/SDK/NDK/Gradle 均未装)。本设计与实现计划可在本机完成;**编译/真机测试需用户先装 Android Studio + NDK + JDK17**。
- **产品身份决策(用户已定)**: **质量兼容派**。保留候选条(Android 习惯),不重造交互;EKTRO 价值 = 个性化 rerank 让候选1几乎总对 + 淡灰续写点击接受。戒①降为"你几乎不必看候选条",非强制隐藏。
- **续写接受方式(用户已定)**: 点击淡灰文字本身即提交。
- **预测后端**: `EKTRO_PREDICTOR_BASELINE`(纯 SQLite phrase_pair 链,零模型;延续全局"移动端轻量预测";`ektro_predict` S0 已实现)。

### 1.1 三戒合规

| 戒 | Android 落地 |
|----|--------------|
| ① 不打断视线 | 妥协但不破:候选条保留,靠 rerank 质量 + 淡灰续写让用户"几乎不必看"。已用户确认。 |
| ② 不离开磁盘 | 完整成立:`ektro_sdk` 本地 SQLite 明文,无 App Group 限制(优于 iOS)。预测走本地 BASELINE,网络请求=0。 |
| ③ 不解释自己 | 完整成立:无助手/桌宠,纯输入法。 |

> **假设/风险**: master 上出现方向重定调提交 `94e562c`(EKTRO=输入法+云端 AI 数字分身)。本设计坚持本地 BASELINE、网络=0。若产品确转云端,需先更新 `CLAUDE.md` 三戒②,本设计随之重审。当前以 checked-in 三戒为准。

---

## 2. 架构

```
   Trime (Kotlin/Java, fork)
        │  3 接入点 (= weasel 三 patch 的 Android 同构)
        │   ① onCommit   → EktroSdk.logCommit(output)
        │   ② 候选构建    → EktroSdk.rerankOrder(cands,ctx,recent) 重排候选条
        │   ③ 输入/停顿后 → EktroSdk.predict(ctx) → 淡灰可点击文字, 点击=提交
        ▼
   EktroSdk.kt  ──JNI──>  ektro_jni.cpp  ──C ABI──>  libektro.so
                                                     (= S0 ektro_sdk, 零改动)
        │
        └─ librime.so (Trime 既有, 不动)
```

**方案 A — JNI sidecar(已选)**: `ektro_sdk` 经 NDK 交叉编译为 `libektro.so`,与 Trime `librime.so` 并排;`ektro_jni.cpp` 仅做 jstring↔char* / jintArray↔int* 转换,不碰 `ektro_sdk` 内核、不碰 librime。是 Windows `bridge` 在 Android 边界的直接同构。

(否决: B librime filter 插件 — 耦合 Trime librime 构建且预测仍需独立 UI 路径;C 深度 fork 改候选 UI — 违背"站成熟引擎肩上")。

---

## 3. 文件结构(锁定)

| 路径 | 职责 | 动作 |
|------|------|------|
| `src-cpp/src/ektro_jni.cpp` | JNI ↔ `ektro_sdk` C ABI 转换薄层(无业务逻辑) | 新建 |
| `android/ektro/EktroSdk.kt` | Kotlin native 声明 + ctx 生命周期(进程级单例) | 新建(Trime fork 内) |
| Trime 提交点 (`TrimeInputMethodService` 或等价 commitText 路径) | 接入点①③ | patch |
| Trime 候选构建处 (`CandidatesAdapter` / `Rime` 输出转候选列表处) | 接入点② rerankOrder | patch |
| `upstream/patches/android/01-jni-loadlib.patch` | 加载 `libektro.so` + EktroSdk 初始化 | 新建 |
| `upstream/patches/android/02-rerank-candidates.patch` | 候选列表按 rerankOrder 重排 | 新建 |
| `upstream/patches/android/03-gray-prediction-tap.patch` | 淡灰续写渲染 + 点击提交 | 新建 |
| `src-cpp/CMakeLists.txt` | 加 `ektro_jni.cpp`(仅 android target 编入)+ 确认 .so 产物 | 改 |

`ektro_jni.cpp` 必须保持极薄,职责单一(类型桥接),便于独立审读与测试。

---

## 4. 数据流(三接入点契约)

1. **提交即学** (①): Trime commit 文本 `s` → `EktroSdk.logCommit(s)` → JNI → `ektro_log_commit(ctx,"",s,isPasswordField)`。密码域由 Trime 的 `EditorInfo` 推导传入;落库异步不阻塞输入(S0 保证)。
2. **候选重排** (②): Rime 产出候选 `c[0..n)` → `EktroSdk.rerankOrder(joinNL(c), context, joinNL(recent))` → JNI → `ektro_rerank_order(...)` → 返回 `int[]`。空数组 = 直通(原序);完整排列 = 按 `order[新位]=原索引` 重排候选条。失败 → 直通 + log(绝不静默吞错,D-009)。
3. **淡灰续写** (③): 输入/短停顿后 → `EktroSdk.predict(context)` → JNI → `ektro_predict(...)` → 非空则在候选区/输入区渲染灰色文字,点击该灰字 → 作为 commit 文本走接入点① + 清除续写。

错误处理: JNI 层所有 `ektro_*` 非零返回 → 读 `ektro_last_error` 写 Android logcat(D-009),功能降级为直通,绝不崩溃输入法进程。

---

## 5. 测试

- **JNI 层**: 复用 S0 `test_c_abi`(C ABI 已测);新增 `ektro_jni` 的 host 侧编译冒烟(NDK 编出 `libektro.so` 即通过最小门)。
- **集成**: 需真机/模拟器(待工具链)。兼容矩阵: Android 微信 / Chrome / 短信 / 备忘录 —— inline 候选条重排、淡灰点击接受、密码域不落库、飞行模式下网络请求=0。
- **退出标准**(对标 CLAUDE.md §五): 能在 Android 整天打字不切回搜狗;学得会口头禅;无崩溃/数据丢失;飞行模式功能不变(证明本地)。

---

## 6. 实现顺序(交 writing-plans 细化)

```
A0  ▸ 环境门: 确认 Android Studio + NDK + JDK17 (无则停, 设计/计划已就绪待解锁)
A1  ▸ fork Trime, baseline 编译跑通, 默认 Rime 方案打中文 (先证 fork 干净)
A2  ▸ NDK 交叉编译 ektro_sdk → libektro.so; 写 ektro_jni.cpp + EktroSdk.kt; loadLibrary 冒烟
A3  ▸ patch① 提交→logCommit; 验证 SQLite 落库 + 密码域不落
A4  ▸ patch② 候选 rerankOrder 重排; 验证候选1命中率
A5  ▸ patch③ 淡灰续写渲染 + 点击接受
A6  ▸ 兼容矩阵 + 飞行模式网络=0 + 整天试用 (S2 验收)
```

---

*Last updated: 2026-05-19 · 设计 v1*
