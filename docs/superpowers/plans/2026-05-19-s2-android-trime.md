# EKTRO S2 — Android (fork Trime + JNI) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 S0 的 `ektro_sdk` C ABI 经薄 JNI 层接入 fork 的 Trime,在 Android 候选流程里插入 EKTRO 个性化 rerank + 淡灰续写(点击接受),三戒②③成立、①按"质量兼容派"。

**Architecture:** JNI sidecar — `ektro_sdk` NDK 交叉编译为 `libektro.so`,`ektro_jni.cpp` 仅做类型桥接,`EktroSdk.kt` 暴露给 Trime,在 3 个接入点 patch(= weasel 三 patch 的 Android 同构)。`ektro_sdk` 内核与 librime 零改动。

**Tech Stack:** Trime 3.3.x (Kotlin/Java + JNI)、Android NDK (clang)、JDK 17、CMake、`ektro_sdk` C ABI (S0, master 已并入)。

---

## ⚠ 环境前置(必须先读)

- 本机**无 Android 工具链**(JDK/SDK/NDK/Gradle 均未装,bash+PowerShell 双重确认)。
- **可在 Windows 完成**:`ektro_jni.cpp` / `EktroSdk.kt` / 3 个 patch 的**源码草拟**、计划本身。
- **必须在装好工具链后做**:NDK 交叉编译 `libektro.so`、Trime fork 编译、真机/模拟器验证、兼容矩阵。
- 计划起始为硬门 **Task A0**。无工具链时 S2 停在"源码+patch 已写、待编译验证",不得声称 S2 完成。
- 执行顺序锁定 **S0(已完成,master)→ S2**。

---

## 文件结构(决策锁定)

| 文件 | 职责 | 动作 |
|------|------|------|
| `src-cpp/src/ektro_jni.cpp` | JNI ↔ `ektro_sdk` C ABI 类型桥接(无业务逻辑) | Create |
| `upstream/trime/` | Trime fork(submodule 或 vendored) | Create (A1) |
| `android/ektro/EktroSdk.kt` | Kotlin native 声明 + 进程级 ctx 单例 | Create (A2, Trime fork 内) |
| `upstream/patches/android/01-jni-loadlib.patch` | 加载 `libektro.so` + 初始化 | Create (A2) |
| `upstream/patches/android/02-rerank-candidates.patch` | 候选按 rerankOrder 重排 | Create (A4) |
| `upstream/patches/android/03-gray-prediction-tap.patch` | 淡灰续写渲染 + 点击提交 | Create (A5) |
| `src-cpp/CMakeLists.txt` | android target 编入 `ektro_jni.cpp` | Modify (A2) |

`ektro_jni.cpp` 保持极薄,仅类型转换,可独立审读。

---

### Task A0: 环境硬门

- [ ] **Step 1: 检查工具链**

Run (Windows):
```
java -version ; (Get-Command gradle) ; ls $env:LOCALAPPDATA\Android\Sdk\ndk
```
Expected: JDK 17/21、Android SDK、NDK 均存在。

**若缺失**:S2 后续仅能完成"源码/patch 草拟",标记 `blocked: 需 Android 工具链`,产物留 `src-cpp/src/ektro_jni.cpp` + `upstream/patches/android/`,停止;不得声称完成。安装清单见本文件末「附:工具链安装」。

---

### Task A1: fork Trime + baseline 编译跑通(需工具链)

**Files:** Create `upstream/trime/`

- [ ] **Step 1: fork 并拉取**

```bash
git submodule add https://github.com/osfans/trime upstream/trime
cd upstream/trime && git checkout 3.3.10
```

- [ ] **Step 2: baseline 编译**

```bash
cd upstream/trime && ./gradlew assembleDebug
```
Expected: 产出 `app-debug.apk`;装入设备/模拟器,默认 Rime 方案能打中文(先证 fork 干净再动 patch)。

- [ ] **Step 3: Commit**

```bash
git add .gitmodules upstream/trime
git commit -m "chore(s2): vendor Trime 3.3.10 fork, baseline 中文输入跑通"
```

---

### Task A2: ektro_jni.cpp + EktroSdk.kt + 加载 libektro.so(JNI 桥接)

**Files:** Create `src-cpp/src/ektro_jni.cpp`; Modify `src-cpp/CMakeLists.txt`; Create `android/ektro/EktroSdk.kt`, `upstream/patches/android/01-jni-loadlib.patch`

- [ ] **Step 1: 写 `src-cpp/src/ektro_jni.cpp`**

```cpp
// SPDX-License-Identifier: Apache-2.0
// JNI ↔ ektro_sdk C ABI 薄桥接。无业务逻辑;仅类型转换。
#include <jni.h>
#include <string>
#include <vector>
#include "ektro/ektro_sdk.h"

namespace {
std::string jstr(JNIEnv* e, jstring s) {
    if (!s) return {};
    const char* c = e->GetStringUTFChars(s, nullptr);
    std::string r(c ? c : "");
    if (c) e->ReleaseStringUTFChars(s, c);
    return r;
}
}  // namespace

extern "C" {

JNIEXPORT jlong JNICALL
Java_com_ektro_EktroSdk_nativeCreate(JNIEnv* e, jclass, jstring db) {
    ektro_config cfg{ EKTRO_PREDICTOR_BASELINE, nullptr };
    std::string p = jstr(e, db);
    return reinterpret_cast<jlong>(ektro_create(p.c_str(), &cfg));
}

JNIEXPORT void JNICALL
Java_com_ektro_EktroSdk_nativeDestroy(JNIEnv*, jclass, jlong h) {
    ektro_destroy(reinterpret_cast<ektro_ctx*>(h));
}

JNIEXPORT jint JNICALL
Java_com_ektro_EktroSdk_nativeLogCommit(JNIEnv* e, jclass, jlong h,
        jstring out, jint isPwd) {
    auto* c = reinterpret_cast<ektro_ctx*>(h);
    std::string s = jstr(e, out);
    return ektro_log_commit(c, "", s.c_str(), isPwd);
}

JNIEXPORT jintArray JNICALL
Java_com_ektro_EktroSdk_nativeRerankOrder(JNIEnv* e, jclass, jlong h,
        jstring cands, jstring ctx, jstring recent) {
    auto* c = reinterpret_cast<ektro_ctx*>(h);
    std::string cj = jstr(e, cands), cx = jstr(e, ctx), rj = jstr(e, recent);
    int n_cands = cj.empty() ? 0 : 1;
    for (char ch : cj) if (ch == '\n') ++n_cands;
    std::vector<int> ord(n_cands > 0 ? n_cands : 1);
    int n = 0;
    int rc = ektro_rerank_order(c, cj.c_str(), cx.c_str(),
                                rj.empty() ? nullptr : rj.c_str(),
                                ord.data(), (int)ord.size(), &n);
    if (rc != 0 || n <= 0) return e->NewIntArray(0);  // 直通
    jintArray a = e->NewIntArray(n);
    e->SetIntArrayRegion(a, 0, n, ord.data());
    return a;
}

JNIEXPORT jstring JNICALL
Java_com_ektro_EktroSdk_nativePredict(JNIEnv* e, jclass, jlong h, jstring ctx) {
    auto* c = reinterpret_cast<ektro_ctx*>(h);
    std::string cx = jstr(e, ctx);
    char buf[512] = {0};
    if (ektro_predict(c, cx.c_str(), buf, (int)sizeof(buf)) != 0) return e->NewStringUTF("");
    return e->NewStringUTF(buf);
}

JNIEXPORT jstring JNICALL
Java_com_ektro_EktroSdk_nativeLastError(JNIEnv* e, jclass, jlong h) {
    return e->NewStringUTF(ektro_last_error(reinterpret_cast<ektro_ctx*>(h)));
}

}  // extern "C"
```

- [ ] **Step 2: Modify `src-cpp/CMakeLists.txt`** — 在 `add_library(ektro STATIC ...)` 之后追加 android 专属共享库目标:

```cmake
if(EKTRO_PLATFORM STREQUAL "android")
    find_library(ANDROID_LOG log)
    add_library(ektro_jni SHARED src/src/ektro_jni.cpp)  # 路径以仓库实际为准
    target_link_libraries(ektro_jni PRIVATE ektro ${ANDROID_LOG})
    target_include_directories(ektro_jni PRIVATE include)
endif()
```
> 注:实际源路径为 `src-cpp/src/ektro_jni.cpp`;CMake 工作目录在 `src-cpp/`,故写 `src/ektro_jni.cpp`。修正上面笔误为 `add_library(ektro_jni SHARED src/ektro_jni.cpp)`。

- [ ] **Step 3: 写 `android/ektro/EktroSdk.kt`**(Trime fork 内,包名与 JNI 符号一致 `com.ektro`):

```kotlin
package com.ektro

object EktroSdk {
    @Volatile private var handle: Long = 0L
    fun init(dbPath: String) {
        if (handle != 0L) return
        System.loadLibrary("ektro_jni")
        handle = nativeCreate(dbPath)
    }
    fun logCommit(output: String, isPassword: Boolean) {
        if (handle != 0L && output.isNotEmpty())
            nativeLogCommit(handle, output, if (isPassword) 1 else 0)
    }
    fun rerankOrder(cands: List<String>, ctx: String, recent: List<String>): IntArray {
        if (handle == 0L || cands.size < 2) return IntArray(0)
        return nativeRerankOrder(handle, cands.joinToString("\n"), ctx,
                                 recent.joinToString("\n"))
    }
    fun predict(ctx: String): String =
        if (handle == 0L) "" else nativePredict(handle, ctx)
    fun lastError(): String = if (handle == 0L) "" else nativeLastError(handle)

    private external fun nativeCreate(db: String): Long
    private external fun nativeDestroy(h: Long)
    private external fun nativeLogCommit(h: Long, out: String, isPwd: Int): Int
    private external fun nativeRerankOrder(h: Long, c: String, ctx: String, rec: String): IntArray
    private external fun nativePredict(h: Long, ctx: String): String
    private external fun nativeLastError(h: Long): String
}
```

- [ ] **Step 4: NDK 编译冒烟**

```bash
cmake -S src-cpp -B build-android -DEKTRO_PLATFORM=android \
  -DCMAKE_TOOLCHAIN_FILE=$NDK/build/cmake/android.toolchain.cmake \
  -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-26
cmake --build build-android --target ektro_jni
```
Expected: 产出 `libektro_jni.so`(含静态链入的 `ektro`)。这是 A2 通过门(本机无 NDK 则停在 Step1-3 源码草拟)。

- [ ] **Step 5: patch①(loadlib)+ Commit**

把 EktroSdk.kt 放入 Trime fork 源树,在 Trime 输入法 Service `onCreate` 调 `EktroSdk.init(<app files dir>/ektro.db)`;diff 存 `upstream/patches/android/01-jni-loadlib.patch`。
```bash
git add src-cpp/src/ektro_jni.cpp src-cpp/CMakeLists.txt upstream/patches/android/01-jni-loadlib.patch upstream/trime
git commit -m "feat(s2): ektro_jni 桥 + EktroSdk.kt + libektro.so 加载"
```

---

### Task A3: 接入点① 提交即学 + 密码域(需工具链)

**Files:** patch Trime commit 路径

- [ ] **Step 1: 接入**

在 Trime 提交文本处(`commitText` 实际调用点)调 `EktroSdk.logCommit(text, isPwd)`;`isPwd` 由当前 `EditorInfo.inputType` 是否 `TYPE_TEXT_VARIATION_PASSWORD`/`TYPE_NUMBER_VARIATION_PASSWORD` 推导。

- [ ] **Step 2: 验证**

设备打字提交 → `adb shell run-as <pkg> ls files/ektro.db` 存在且 `commit_log` 有行;密码框输入后该次**不入库**(密码域 D-009 由 ektro_sdk 内部权威处理)。

- [ ] **Step 3: Commit**

```bash
git add upstream/patches/android/ upstream/trime
git commit -m "feat(s2): 接入点① 提交即学 + 密码域不落库"
```

---

### Task A4: 接入点② 候选 rerank 重排(需工具链)

**Files:** Create `upstream/patches/android/02-rerank-candidates.patch`

- [ ] **Step 1: 接入**

在 Trime 把 Rime 候选转为列表/Adapter 的位置:取候选文本数组 `cands`,调 `val order = EktroSdk.rerankOrder(cands, contextStr, recentList)`。`order` 为空 → 用原序;长度==cands.size → 按 `display[i] = cands[order[i]]` 重排后再交给候选条 Adapter。失败/空 → 直通(绝不崩溃)。

- [ ] **Step 2: 验证**

打字时高频个性化词应被顶到候选1;清空学习库(`ektro_memory_clear` 经设置项或重装)后恢复原序。logcat 无 EKTRO error。

- [ ] **Step 3: Commit**

```bash
git add upstream/patches/android/02-rerank-candidates.patch upstream/trime
git commit -m "feat(s2): 接入点② 候选 rerankOrder 重排 (空=直通)"
```

---

### Task A5: 接入点③ 淡灰续写 + 点击接受(需工具链)

**Files:** Create `upstream/patches/android/03-gray-prediction-tap.patch`

- [ ] **Step 1: 接入**

输入/短停顿后调 `val tail = EktroSdk.predict(contextStr)`;`tail` 非空则在候选区起始渲染一个**灰色样式**的特殊候选项(或输入区 inline 灰字)。点击该项 → 把 `tail` 当作 commit 文本走接入点①(`commitText` + `EktroSdk.logCommit`),并清除续写显示。其它按键 → 续写消失。

- [ ] **Step 2: 验证**

打几个字后出现灰色续写;点击即上屏并被学习;不点继续打字续写消失,不干扰正常候选。飞行模式下续写仍工作(证明本地 BASELINE,网络=0)。

- [ ] **Step 3: Commit**

```bash
git add upstream/patches/android/03-gray-prediction-tap.patch upstream/trime
git commit -m "feat(s2): 接入点③ 淡灰续写 + 点击接受"
```

---

### Task A6: 兼容矩阵 + S2 验收(需工具链)

**Files:** Create `docs/benchmarks/android-s2.md`

- [ ] **Step 1: 兼容矩阵**

微信 / Chrome / 短信 / 备忘录 逐个验证:候选重排、灰字点击接受、密码域不落库、**飞行模式网络请求=0**(`adb` 抓包或断网验证功能不变)。

- [ ] **Step 2: 记录 + Commit**

写 `docs/benchmarks/android-s2.md`(命中率、是否整天可用、网络=0 证据)。退出标准:整天打字不切回搜狗;学得会口头禅;无崩溃/数据丢失。
```bash
git add docs/benchmarks/android-s2.md
git commit -m "test(s2): Android 兼容矩阵 + 飞行模式网络=0 (S2 验收)"
```

---

## 附:工具链安装(Windows,A0 缺失时)

1. JDK 17:`winget install EclipseAdoptium.Temurin.17.JDK`
2. Android Studio(含 SDK):https://developer.android.com/studio — 安装后 SDK Manager 装 `Android SDK Platform 34`、`NDK (Side by side)`、`CMake`
3. 设 `ANDROID_HOME=%LOCALAPPDATA%\Android\Sdk`,`JAVA_HOME` 指向 JDK17
4. 建一个 arm64 模拟器或接真机开 USB 调试
5. 重跑 Task A0

---

## Self-Review

**1. Spec 覆盖:** spec §2 架构→A2;§3 文件结构→A2/A4/A5 文件表;§4 三接入点契约→A3(①)/A4(②)/A5(③);§5 测试→A4/A5/A6 + 飞行模式网络=0;§6 实现顺序→A0-A6 一一对应;§1 环境约束→A0 硬门 + 附录安装。三戒②(本地)由 BASELINE+飞行模式验证覆盖;①(质量兼容)由 A4 命中率覆盖;③由"无助手"设计本身保证。无遗漏。

**2. 占位符扫描:** 无 TBD/TODO;ektro_jni.cpp/EktroSdk.kt 给出完整代码;CMake 笔误已在步内显式更正为 `src/ektro_jni.cpp`;对 Trime 内部具体调用点标注"以仓库实际为准"(约束非占位,因 Trime 为外部 fork 需 A1 后按真实源定位)。

**3. 类型一致性:** JNI 符号 `Java_com_ektro_EktroSdk_native*` 与 `EktroSdk.kt` 包 `com.ektro` + `external fun native*` 签名逐一对应(Create/Destroy/LogCommit/RerankOrder/Predict/LastError);`rerankOrder` 空数组=直通契约在 jni、kt、A4 三处一致;调用的 `ektro_*` 均为 S0 已实现并入 master 的 C ABI(含 `ektro_rerank_order`)。

---

*Plan v1 · 2026-05-19 · 基于 specs/2026-05-19-s2-android-design.md*
