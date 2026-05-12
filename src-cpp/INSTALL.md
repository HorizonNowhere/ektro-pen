# EKTRO C++ 模块 — 安装与编译

> Cycle 2 Day 1-5 全部代码就位。本文档指导你**第一次编译跑通**。

---

## 0. 前置条件

- Windows 11 + Visual Studio 2022 Build Tools (含 CMake)
- 已 fork weasel: `upstream/weasel-master/` (Day 1 完成 ✓)
- librime/plum submodule (待拉)

---

## 1. 拉 librime + plum

```powershell
cd E:\CLAUDE\EKTRO输入法\upstream\weasel-master

# 方案 A: git submodule (推荐)
git init  # zip 解压后无 .git，先初始化
git submodule add https://github.com/rime/librime.git librime
git submodule add https://github.com/rime/plum.git plum

# 方案 B: 单独下载 zip
# 浏览器下:
#   https://github.com/rime/librime/archive/refs/heads/master.zip → librime/
#   https://github.com/rime/plum/archive/refs/heads/master.zip → plum/
```

---

## 2. 安装 boost（首次 30-60 分钟）

```powershell
cd E:\CLAUDE\EKTRO输入法\upstream\weasel-master
.\install_boost.bat
```

会下 boost_1_84_0.7z 到 `deps/` 然后用 b2 build。

---

## 3. baseline weasel 编译

```powershell
cd E:\CLAUDE\EKTRO输入法\upstream\weasel-master
.\xbuild.bat   # 或 .\build.bat (Release x64)
```

成功后产物在 `output/`：`weasel-setup.exe`、`WeaselTSF.dll` 等。

**验证**: 装 `weasel-setup.exe` 到机器（管理员权限），在 Notepad 输入拼音看是否出中文。

---

## 4. EKTRO C++ 模块（独立验证）

```powershell
cd E:\CLAUDE\EKTRO输入法\src-cpp

# 下第三方 header-only 依赖
mkdir third_party
mkdir third_party\nlohmann

# 浏览器下 (或 curl):
#   https://raw.githubusercontent.com/yhirose/cpp-httplib/master/httplib.h
#   → third_party/httplib.h
#
#   https://raw.githubusercontent.com/nlohmann/json/develop/single_include/nlohmann/json.hpp
#   → third_party/nlohmann/json.hpp

# CMake 构建
mkdir build && cd build
cmake .. -G "Visual Studio 17 2022" -A x64 `
    -DCMAKE_PREFIX_PATH="..\..\upstream\weasel-master\deps"

cmake --build . --config Release
```

**期望输出**:
- `ektro.lib` 静态库
- `ektro_tests.exe` (如果 GoogleTest 找到了)

**跑测试**:
```powershell
ctest -C Release --output-on-failure
```

---

## 5. 应用 weasel patch (启用 EKTRO 三戒)

**✅ D-014 时我已经把 3 个 patch 直接 inline 到 upstream/weasel-master/WeaselTSF/ 里**:

```
Globals.h:        +extern bool g_force_show_candidates 声明
CandidateList.cpp: +定义 + _ShowUI() 加条件门
KeyEventSink.cpp:  +长按 Ctrl ≥500ms 监听
```

**你跑 build.bat 自动包含 patch**。如需回滚，恢复 `*.bak` 文件即可:

```powershell
cd E:\CLAUDE\EKTRO输入法\upstream\weasel-master\WeaselTSF
foreach ($f in 'Globals.h', 'CandidateList.cpp', 'KeyEventSink.cpp') {
    if (Test-Path "$f.bak") { Copy-Item "$f.bak" $f -Force }
}
```

**重新编译 weasel** (含 EKTRO patch):

```powershell
cd E:\CLAUDE\EKTRO输入法\upstream\weasel-master
.\build.bat
```

**关键改动概要**:
- `_ShowUI()` 加 `g_force_show_candidates` 条件门（默认 false，候选窗不显示）
- KeyEventSink 长按 Ctrl ≥500ms 切 `g_force_show_candidates = true` → 候选窗唤起
- 松开 Ctrl 切回 false → 候选窗隐藏

---

## 6. 安装用户 YAML 配置

```powershell
$src = 'E:\CLAUDE\EKTRO输入法\config\default.custom.yaml'
$dst = "$env:APPDATA\Rime\default.custom.yaml"
Copy-Item $src $dst

# 重新部署 (Rime 重读配置)
& "$env:ProgramFiles\Rime\weasel-0.x.x\WeaselDeployer.exe" /deploy
```

---

## 7. 端到端验证（公理 ①）

打开 Notepad，输入 `nihaoshijie`。

```
   ✓ "你好世界" 直接 inline 显示到光标位（带下划线 composition 样式）
   ✓ 没有候选窗弹出
   ✓ 按空格 commit
   ✓ 按 Tab 切换候选（仍然 inline）
   ✓ 长按 Ctrl ≥500ms 候选窗才浮现（应急通道）
   ✓ 松开 Ctrl 候选窗消失
```

如果以上全部 ✓，**Cycle 2 Day 5 完成**。

---

## 8. 故障排查

| 问题 | 解决 |
|------|------|
| CMake 找不到 SQLite3 | weasel deps 编译完成后会有 `deps/sqlite3.lib`，加 `-DCMAKE_PREFIX_PATH` |
| `httplib.h not found` | 按 §4 下载放对位置；或接受 stub（PredictorClient 会返 ServerDown） |
| GTest 找不到 | `vcpkg install gtest:x64-windows` 或编译时跳过 `-DEKTRO_BUILD_TESTS=OFF` |
| patch apply 失败 | 手动 grep `_ShowUI` 找到位置，按 patch 内容编辑 |
| 应用没显示 inline | 部分 UWP 应用（Edge）不支持 composition，会 fallback 候选窗 |
| 候选窗一直不显示 | 检查 `default.custom.yaml` 的 `style/inline_preedit: true` 是否生效 |

---

## 9. 与 Python 参考的差异

C++ 实现严格对照 Python 行为，但有 4 处工程化差异（已记 D-012）:

1. **PredictorClient 缓存**：当前是文件级静态变量（简化）。生产应改 pImpl 模式。
2. **AsyncTrigger 状态**：当前用 `unordered_map<void*, TriggerState*>` 维护（简化）。生产应改 pImpl。
3. **日志格式**：与 Python 完全一致（时间 + 级别 + 模块 + 消息）。
4. **质量门**：与 Python 一致（含 ASCII 数字字母拒绝）。

---

## 10. 下一步（Cycle 3 议题）

- 把 EKTRO 静态库链接进 weasel WeaselTSF.dll（修改 weasel CMakeLists）
- 在 `_HandleCompartment` 或 commit 钩子调用 `MemoryStore::log_commit`
- 注册 librime filter `EktroRerankFilter` 走 `_UpdateUI` 之前
- 启动 llama-server 作为 IME 子进程（service-style）

详见 `docs/decisions.md` D-013 + 未来 D-014。
