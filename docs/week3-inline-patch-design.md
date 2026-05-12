# Week 3 — Inline 渲染 Patch 设计

> **基于 WebFetch 读 weasel 源码总结的修改方案**。
> 网络限制无法本地 clone，但通过 raw.githubusercontent.com + DeepWiki 已确认全部关键 API。
>
> Status: **Design / Pre-implementation**
> Date: 2026-05-11

---

## TL;DR — 一句话方案

> **weasel 已经实现了 inline preedit 完整链路。EKTRO Week 3 不写新代码，而是「翻一个 flag」+「在 `_ShowUI` 加条件门」。**

工作量预估：**从原计划"2-3 周"缩到"3-5 天"**。

---

## 1. 关键发现（写源码 mental model）

### 1.1 weasel TSF 已有的 inline 链路

```
   按键
    │
    ▼
   CWeaselTSF::OnTestKeyDown  →  传给 librime
    │
    ▼
   librime 返回 Context (含 preedit.str)
    │
    ▼
   CWeaselTSF::_UpdateUI(ctx, status)
    │
    ├─ if (inline_preedit) → _ShowInlinePreedit(ctx)     ← ★ 当前已有
    │       │
    │       └─ Edit session: CInlinePreeditEditSession
    │              └─ pRange->SetText(ec, 0, preedit.c_str(), len)   ← 核心写入
    │
    └─ if (有候选) → _ShowUI()                            ← ★ 这里弹候选窗
            │
            └─ WeaselClient::UpdateUI()  →  IPC → WeaselServer  →  WeaselPanel 渲染
```

### 1.2 关键类与方法（来自 WeaselTSF.h）

```cpp
// composition 控制
void _StartComposition(com_ptr<ITfContext>, BOOL);
void _EndComposition(com_ptr<ITfContext>, BOOL);
BOOL _ShowInlinePreedit(com_ptr<ITfContext>, const std::shared_ptr<weasel::Context>);
void _UpdateComposition(com_ptr<ITfContext>);
BOOL _UpdateCompositionWindow(com_ptr<ITfContext>);

// 候选窗 UI 控制
void _UpdateUI(const weasel::Context&, const weasel::Status&);
void _StartUI();
void _EndUI();
void _ShowUI();   // ← ★ EKTRO 在这里加条件门
void _HideUI();
```

### 1.3 inline preedit 写文本的具体代码（来自 Composition.cpp）

```cpp
STDAPI CInlinePreeditEditSession::DoEditSession(TfEditCookie ec) {
  std::wstring preedit = _context->preedit.str;
  com_ptr<ITfRange> pRangeComposition;
  if (_pComposition == nullptr) return E_FAIL;
  if ((_pComposition->GetRange(&pRangeComposition)) != S_OK)
    return E_FAIL;

  // ★ 核心：把 preedit 文本写入应用编辑区
  if ((pRangeComposition->SetText(ec, 0, preedit.c_str(),
            static_cast<LONG>(preedit.length()))) != S_OK)
    return E_FAIL;

  _pTextService->_SetCompositionDisplayAttributes(ec, _pContext,
                                                  pRangeComposition);

  // 光标定位
  TF_SELECTION tfSelection;
  if (sel_cursor < 0) {
    pRangeComposition->Collapse(ec, TF_ANCHOR_END);
  } else {
    pRangeComposition->Collapse(ec, TF_ANCHOR_START);
    pRangeComposition->ShiftStart(ec, sel_cursor, &cch, NULL);
  }
  tfSelection.range = pRangeComposition;
  _pContext->SetSelection(ec, 1, &tfSelection);
  return S_OK;
}
```

**这就是 inline 渲染的全部秘密**：`SetText` 写入，应用按 composition 样式自动渲染（下划线）。

---

## 2. EKTRO 的改动方案（最小侵入）

### 2.1 改动 1：默认启用 inline_preedit

**文件**：用户配置 YAML（不是 C++ 代码）

```yaml
# 用户 default.yaml 加这一行（或合并 default.custom.yaml）
style:
  inline_preedit: true     # ← EKTRO 默认 true，传统 weasel 默认 false
  display_tray_icon: true
  horizontal: false        # 候选窗（虽然默认隐藏）排列方向
```

> 这一改不需要 C++ 修改。weasel 已经支持 `inline_preedit: true` 的配置。

### 2.2 改动 2：候选窗默认隐藏 + 长按 Ctrl 应急

**文件**：`WeaselTSF/UIManager.cpp`（推测；可能名为 `UI.cpp` 或 `TextService_*.cpp`）

```cpp
// === EKTRO patch: _ShowUI 加条件门 ===

// 全局状态：是否处于"应急候选窗"模式
static bool g_force_show_candidates = false;

// 修改 _ShowUI()：
void CWeaselTSF::_ShowUI() {
    // ★ EKTRO 核心改动
    if (!g_force_show_candidates) {
        // 默认行为：不弹候选窗，candidates 留在 IPC 数据里
        // 用户按 Tab 时通过 _UpdateComposition 切换
        return;
    }

    // 应急通道（长按 Ctrl 触发）：原 weasel 行为
    m_client.ShowUI();   // 这是原代码
}
```

### 2.3 改动 3：长按 Ctrl 监听器

**文件**：`WeaselTSF/KeyEventSink.cpp`（推测）

```cpp
// === EKTRO patch: 长按 Ctrl 触发候选窗 ===

static auto g_ctrl_hold_start = std::chrono::steady_clock::time_point::min();
constexpr auto CTRL_HOLD_THRESHOLD = std::chrono::milliseconds(500);

STDMETHODIMP CWeaselTSF::OnKeyDown(/* ... */) {
    if (wParam == VK_CONTROL || wParam == VK_LCONTROL || wParam == VK_RCONTROL) {
        if (g_ctrl_hold_start == std::chrono::steady_clock::time_point::min()) {
            g_ctrl_hold_start = std::chrono::steady_clock::now();
        }
        auto held = std::chrono::steady_clock::now() - g_ctrl_hold_start;
        if (held >= CTRL_HOLD_THRESHOLD && !g_force_show_candidates) {
            g_force_show_candidates = true;
            _ShowUI();   // 触发应急候选窗
        }
    }
    // ... 原代码
}

STDMETHODIMP CWeaselTSF::OnKeyUp(/* ... */) {
    if (wParam == VK_CONTROL || wParam == VK_LCONTROL || wParam == VK_RCONTROL) {
        g_ctrl_hold_start = std::chrono::steady_clock::time_point::min();
        if (g_force_show_candidates) {
            g_force_show_candidates = false;
            _HideUI();
        }
    }
    // ... 原代码
}
```

### 2.4 改动 4：Tab 键切换候选

**文件**：`WeaselTSF/KeyEventSink.cpp` 中处理 Tab 键

weasel 已经把 Tab 传给 librime，librime 默认会改变 candidate index。**问题是**：weasel UI 已隐藏，所以视觉上看不出 Tab 改了候选。

EKTRO 改动：Tab 时强制 `_UpdateComposition()`，让 inline 文本立刻反映新候选：

```cpp
// === EKTRO patch: Tab 后 inline 文本更新 ===

if (wParam == VK_TAB) {
    // 原代码：把 Tab 传给 librime
    bool handled = m_client.ProcessKeyEvent(VK_TAB, ...);
    if (handled) {
        // ★ EKTRO 加这一句：强制刷新 inline 文本
        _UpdateComposition(_pEditSessionContext);
        // 同时记录到 EktroMemoryStore：user_picked=true
        // （等 Week 4 集成 store 后开启这一步）
    }
    return handled;
}
```

### 2.5 改动 5：commit 后落库（Week 4 集成点）

仅占位。Week 4 把 EktroMemoryStore 的 C++ 移植做完后，在 `OnCommitText` 调用 `store->log_commit(...)`。

---

## 3. 改动文件汇总

```
   patch 1：用户配置 YAML
     文件: %APPDATA%\Rime\default.custom.yaml （EKTRO 安装时打包提供）
     改动: 启用 inline_preedit + 关闭显示设置
     代码量: 5 行

   patch 2-4：WeaselTSF 修改
     文件: WeaselTSF/UIManager.cpp   (或 TextService_*.cpp，需 fork 后确认)
     文件: WeaselTSF/KeyEventSink.cpp
     文件: WeaselTSF/WeaselTSF.h    (加 static 状态变量)
     改动: 加一个 g_force_show_candidates flag + 两个键事件处理点
     代码量: ~50 行

   patch 5（Week 4 占位）：commit 钩子接入 MemoryStore
     代码量: ~30 行（含错误处理）
```

**总计**：~85 行 C++ + 5 行 YAML。这是非常小的 patch。

---

## 4. 测试矩阵（Week 3 实际编译后跑）

| 应用 | inline 期望 | 测试要点 |
|------|-----------|---------|
| Notepad | ✓ 显示中文+下划线 | 基本场景 |
| VSCode | ✓ 显示 + 不冲突 IDE 自动补全 | 我们的开发主战场 |
| Chrome (textarea / contenteditable) | ✓ | 网页输入 |
| Microsoft Edge (UWP) | ⚠ 沙箱限制 → 可能需 fallback 候选窗 | UWP 兼容 |
| Word | ✓ | Office 集成 |
| 微信 | ✓ | 国民应用 |
| cmd.exe / PowerShell | ⚠ 终端 composition 支持差 | 可降级 |
| Terminal | ⚠ 同上 | 可降级 |
| 游戏（DirectX 全屏） | ✗ 不支持 | 不在 v1 范围 |
| 管理员权限 cmd | ✗ TSF 进不去 | 不在 v1 范围 |

测试方法（Week 3 完成编译后）：
1. 在每个应用打 "nihaoshijie"
2. 观察 inline 显示是否流畅、字体是否正常、光标位置是否对
3. 按 Tab 验证候选切换
4. 长按 Ctrl 验证应急候选窗
5. 鼠标点击其他位置验证 composition 自动 commit

---

## 5. 编译路径建议

由于今晚 GitHub clone 跨境失败，给未来留两种方案：

### 方案 A：用 zip 下载 + 手动解压（推荐）

```powershell
# 浏览器或代理下：
# https://github.com/rime/weasel/archive/refs/heads/master.zip
# 保存到 E:\CLAUDE\EKTRO输入法\upstream\weasel.zip

cd E:\CLAUDE\EKTRO输入法\upstream
Expand-Archive weasel.zip -DestinationPath . -Force
Rename-Item weasel-master weasel

# 拉 librime submodule (weasel 依赖)
cd weasel
git submodule update --init --recursive
# 如果 submodule 也卡，单独 zip 下 librime
```

### 方案 B：用国内镜像源（如果重试也不行）

```bash
# Gitee 镜像（如果他们维护了 mirror）
git clone https://gitee.com/lotem/librime.git

# 或者直接走 codeload.github.com 但加 IP DNS 加速
echo '140.82.114.10 codeload.github.com' >> hosts
```

### 编译步骤（参考 weasel README）

```bash
cd weasel
# 1. 拉依赖
.\build.bat boost   # 第一次很慢，下载并编译 boost
.\build.bat deps    # 编译 librime + 其他依赖

# 2. 编译
.\build.bat         # Release x64

# 3. 安装 + 注册
.\WeaselSetup\bin\WeaselSetup.exe
```

**首次编译估计 30-90 分钟**（boost + librime 编译占大头）。

---

## 6. 风险登记

| # | 风险 | 等级 | 缓解 |
|---|------|------|------|
| R1 | clone weasel 跨境失败 | 中 | 用浏览器下 zip / 让用户帮忙 |
| R2 | boost / librime 依赖编译慢 | 中 | 第一次接受 1 小时，缓存后 5 分钟 |
| R3 | inline 在某些 UWP 应用不工作 | 高 | 测试矩阵记录，不工作的退回候选窗 |
| R4 | Tab 切换在已有 Tab 处理的应用冲突 | 中 | 配置允许用其他键替代（如 ; 或 '） |
| R5 | 我读源码的方法名/文件名推测不准 | 中 | fork 后第一件事是 grep 确认 |

---

## 7. Week 3 真实可执行任务清单（修订）

按 tasks.md，原 T3.1-T3.10 太抽象。修订为：

- [ ] T3-Day1 Fork weasel 仓库（zip 方式如必要）+ git submodule
- [ ] T3-Day1 验证 `.\build.bat` 跑通基线（boost + librime + weasel）
- [ ] T3-Day1 安装 baseline weasel，用 Notepad 测打字基线
- [ ] T3-Day2 grep 确认 `_ShowUI/_HideUI/_UpdateUI` 实际所在文件（推测 vs 现实）
- [ ] T3-Day2 写 patch 2 (`_ShowUI` 条件门) + 编译
- [ ] T3-Day2 验证：默认情况看不到候选窗了
- [ ] T3-Day3 写 patch 3 (长按 Ctrl 监听) + 编译 + 测应急候选窗
- [ ] T3-Day4 写 patch 4 (Tab 切换刷新 inline) + 编译
- [ ] T3-Day4 准备 default.custom.yaml (打包 inline_preedit=true 等配置)
- [ ] T3-Day5 测试矩阵跑：Notepad/VSCode/Chrome/Edge/Word/微信/cmd
- [ ] T3-Day5 写 Week 3 回顾 + 决定哪些应用退回候选窗

---

## 8. 不在 Week 3 范围（明确推迟）

- 鼠标点击自动 commit composition（这是 TSF 默认行为，不必特别处理）
- composition 长度限制（≤16 字这条 SLO 在实测中验证，不主动截断）
- inline 文字样式定制（保留 weasel 默认的 composition 下划线样式，v0.2 再改）
- DirectWrite 字体渲染（候选窗内的字体，由 WeaselUI 处理，inline 不涉及）

---

## 9. 相关 weasel 源文件清单（待 fork 确认）

| 文件 | 当前 mental model 推测内容 |
|------|---------------------------|
| WeaselTSF/WeaselTSF.h | 主类定义，含 `_ShowUI/_HideUI` 声明 ✓ 已确认 |
| WeaselTSF/Composition.cpp | `CInlinePreeditEditSession::DoEditSession` ✓ 已确认 |
| WeaselTSF/Compartment.cpp | IME 开关 / 状态 compartment 监听 ✓ 已确认 |
| WeaselTSF/UIManager.cpp 或 UI.cpp | `_ShowUI/_HideUI/_UpdateUI` 实现 (404 未直接读到) |
| WeaselTSF/KeyEventSink.cpp | 按键事件入口 (推测) |
| WeaselTSF/TextService.cpp 或 _TextService_*.cpp | 主服务实现 (推测) |
| WeaselIPC/include/*.h | IPC 数据结构（candidates / status / context） |
| WeaselUI/WeaselPanel.cpp | 候选窗渲染（EKTRO 不改，保留应急用） |

> 上面 ✓ 项已经从 raw.githubusercontent.com 读过。其他项 fork 后跑 `grep -r "_ShowUI"` 几秒就能确认。

---

## 10. 反思（为何写这份文档）

如果今晚直接尝试编译，会面临：
- clone 失败 → 30 分钟
- boost 编译失败 → 1 小时
- 不知道改哪里 → 一晚上摸索

**改成"先读源码 + 写设计"，本质上把 Week 3 的探索风险前置消化了**：
- 知道 `_ShowUI` 是改动点 → 编译完直接 grep + 改
- 知道 inline preedit 已存在 → 不重造轮子
- 知道改动量 ~85 行 → 不会陷入大规模重构

Day 1 spike 教会我们："**先理解再行动**"。今晚把这个原则应用在 Week 3 准备上。

---

*相关：[CLAUDE.md](../CLAUDE.md) · [design.md](../openspec/changes/ektro-mvp/design.md) §4 · [tasks.md](../openspec/changes/ektro-mvp/tasks.md) Week 3*
