# Change: ektro-mvp

> **打造"不让你选字"的拼音输入法 v0.1**
>
> Status: `Draft` · Author: getwinccc@gmail.com · Created: 2026-05-11
> Cycle: 1 of N (6 weeks) · Risk: High (estimated 50% failure)

---

## Why

中文输入法 30 年来没有本质改变。所有商业产品（搜狗、百度、讯飞、QQ、微软）和所有开源产品（RIME、Fcitx5、libpinyin）都建立在同一个假设上：

> **"机器猜不准，所以列一排候选给你选。"**

这个假设导致一个被全行业当成"正常"的体验：

```
   每打 4 个字     →   眼睛离开光标
   每天 5000 字   →   被打断 1250 次
   30 年来        →   没人当 bug
```

**我们把它当 bug。**

### 时间窗：为什么是 2026

四股力量第一次同时到位：

| 维度 | 过去 | 现在 (2026) |
|------|------|------------|
| 端侧 0.6B 模型首 token 延迟 | 500ms+ | 30-80ms |
| 端侧模型尺寸 | GB 级 | 300MB 量化 |
| 中文输入首字准确率 | 70-80% | 95%+ (万象 + 个性化) |
| 商业输入法可信度 | 默认信任 | 隐私丑闻后破产 (Citizen Lab 2023-2024) |

**这是十年没人做过的事，第一次工程上可能。**

---

## What Changes

新建一个 Windows 11 桌面拼音输入法 **EKTRO**，v0.1 必须达成：

### 核心交互翻转

| 旧范式 | EKTRO v0.1 |
|--------|-----------|
| 候选窗弹出 → 你选 → commit | 直接 inline 渲染最可能候选 → 空格 commit |
| 错了再选另一个 | 错了按 Tab 切换 + 自动学习 |
| 续写功能藏在 AI 按钮后面 | 停顿 >300ms 自动浮现淡灰续写，Tab 接受 |
| 个性化要上传云端 | 个性化完全本地（SQLite + 端侧 GRU） |
| 输入法 = 单进程，崩溃就死 | 双进程隔离，Core 崩溃不影响 IME 前端 |

### 技术增量（相对 weasel/librime 基线）

1. 新增 `EktroRerankFilter`：基于 SQLite 输入历史 + 端侧 GRU 重排候选
2. 新增 `EktroPredictor`：停顿触发 Qwen3-0.6B 淡灰预测
3. 新增 `EktroMemoryStore`：明文 SQLite 持久化用户语料
4. 改造 `WeaselUI`：候选窗默认隐藏，提供 inline 渲染层
5. 新增 `EktroCoreService`：独立进程承载 ML 模型，与 IME 前端通过 Named Pipe 通信
6. 新增 `EktroDashboard`：用户面板，可视化/导出/删除自己的数据

---

## Impact

### 用户体验

```
   ✓ 视线不再被打断 ── 首要价值
   ✓ 个性化在每次 commit 后自然增强
   ✓ 隐私默认 100% 本地
   ✓ 长句一气呵成（淡灰预测）
   
   ⚠ 学习曲线 ── 用户需要适应"不看候选窗"的范式
   ⚠ 1-5% 错字率会成为主观感受焦点
```

### 兼容性

| 类别 | 支持 |
|------|------|
| Windows 11 (x64) | ✓ 主目标 |
| Windows 10 | ✓ 尽量 |
| Win32 应用（VSCode/Chrome/微信/Word） | ✓ |
| UWP/Edge 沙箱应用 | ⚠ Week 1 验证 |
| 管理员权限应用（cmd elevated/Task Manager） | ✗ v1 不支持 |
| 某些 DirectX 游戏 | ✗ v1 不支持 |
| Windows ARM64 | ⏳ v0.2 |
| macOS / Linux | ✗ 永不（至少 v1 范围） |

### 数据格式

- 所有用户数据在 `%APPDATA%\EKTRO\`
- 主存储 `user.db` 是明文 SQLite
- 模型在 `models/*.gguf`
- 词库在 `dict/`
- **用户可视化、可导出（JSON）、可删除（一键擦除）**

### 网络

- 默认配置：**零联网**
- v1 不提供任何云端 LLM 调用入口
- 检查更新、词库订阅等"可选联网"功能：v0.3+ 再考虑

---

## Non-Goals

明确不做（v1 期间，PR 直接拒）：

```
✗ 候选窗默认显示
✗ 桌宠 / Live2D / 任何角色动画
✗ Chat 浮窗 / AI 助手按钮
✗ 语音输入（Whisper 集成是另一个产品）
✗ 云端 LLM 调用入口
✗ 表情 / 颜文字 / 符号面板
✗ 皮肤商店
✗ 多设备同步
✗ 双拼方案（v0.2 再加，先把全拼做透）
✗ 五笔 / 仓颉 / 形码
✗ macOS / Linux / 移动端
✗ 设置面板的 60 个开关（限制 ≤6 个核心设置）
```

---

## Success Criteria

### 硬性 SLO（Week 6 评审用）

```
首屏候选 (inline 字)         ≤ 50ms   P99
GRU rerank 响应              ≤ 30ms   P99
Qwen3 首 token 延迟          ≤ 200ms  P95
内存常驻 (含 0.6B 量化)      ≤ 800MB
CPU 空闲占用                 < 1%
每次 commit 落库异步         不阻塞输入
默认配置网络请求数            = 0
```

### 主观验收

```
✓ 我能每天用它打字一整天，连续一周没切回搜狗。
✓ 它能学会我自定义的口头禅与高频用语。
✓ 没有崩溃 / 卡死 / 数据丢失。
✓ 我愿意把 zip 发给 5 个朋友自用。
```

### 不达标 → 砍范围而不是延期

按 Shape Up 铁律。Week 6 评审三个可能结局：

| 状态 | 行动 |
|------|------|
| 全部达标 | 进入 Cycle 2，议题在 6 周内现场决定 |
| 部分达标 | 砍掉未达标的非核心功能（如 Qwen3 预测），保留达标的，发 v0.1 |
| 核心不达标（inline 渲染失败） | 砍到 weasel + 万象 + 记忆三件事，发 v0.05；重新评估范式 |

---

## Alternatives Considered

| 方案 | 否决理由 |
|------|---------|
| 从零写 Rust + TSF | 死亡谷期 6-12 月，单人撑不住 |
| PIME (Python/Node 框架) | 性能上限低，分发要装 runtime，框架本身维护不积极 |
| 替换 librime 用神经引擎 | 工程量爆炸，没团队 |
| 仅做 AI 续写不动核心打字 | 同质化讯飞/百度，无差异化 |
| 沿用候选窗 + AI 续写 | 妥协；违反三戒第一条（不打断视线） |
| 走云端 LLM 路线 | 违反三戒第二条（不离开磁盘）；隐私丑闻教训太鲜 |

**最终选择**：fork weasel/librime + GRU rerank filter + Qwen3 预测器 + 默认隐藏候选窗。

> 详细技术权衡见 [design.md](./design.md)。

---

## Open Questions

需要 Week 1 探针验证：

| # | 问题 | 验证方式 | 决策影响 |
|---|------|---------|---------|
| O1 | 万象拼音词库的实测首选准确率 | 跑 1000 条自然语料 | 决定 GRU rerank 训练目标 |
| O2 | Qwen3-0.6B IQ4_XS 在目标机器的真实首 token 延迟 | llama.cpp benchmark | 不达标 → 退到更小模型或纯统计 |
| O3 | inline 渲染在 UWP 沙箱应用的兼容性 | Edge / Mail 实测 | 不通 → 这些应用退回候选窗 |
| O4 | weasel TSF 注册在最新 Win11 的稳定性 | 干净虚拟机部署 | 决定签名优先级 |

> 验证结果落到 [docs/decisions.md](../../../docs/decisions.md)。

---

## Approval

| 角色 | 决策 | 日期 |
|------|------|------|
| 产品主理人 (getwinccc) | ✓ Approved | 2026-05-11 |
| 技术评审 (Claude as PM/Architect) | ✓ Approved | 2026-05-11 |
| 安全评审 (待 design.md §11 威胁模型完成) | ⏳ Pending (Week 6 前) | — |

**Status**: `Approved` — Cycle 1 可启动。
**首要前置 Spike**: Qwen3-0.6B 延迟实测 (见 [`docs/cycle1-spike-day1.md`](../../../docs/cycle1-spike-day1.md))。

---

*Linked artifacts: [design.md](./design.md) · [tasks.md](./tasks.md)*
