# Design: ektro-mvp

> 配套 [proposal.md](./proposal.md)。回答"怎么做"。
>
> Status: `Draft` · Last updated: 2026-05-11

---

## 0. 设计原则（自上而下的决策链）

```
   宪法三戒  →  PRD 反叛主张  →  本设计的所有技术选择
   ─────────    ─────────────    ──────────────────────
   不打断视线    取消候选窗      → inline 渲染 + 异步预测
   不离开磁盘    本地 AI         → 端侧 GRU + 量化 LLM
   不解释自己    无 UI 戏剧化    → 候选窗默认隐藏 + 淡灰一种状态
```

**任何技术决策如果不能追溯到这条链，就不该出现在 EKTRO 里**。

---

## 1. 高层架构

```
┌──────────────────────────────────────────────────────────────────┐
│                  Windows 应用层                                   │
│       (VSCode, Chrome, Word, 微信, Notepad, ...)                  │
└────────────────────────┬─────────────────────────────────────────┘
                         │ TSF (主) + IMM32 (兼容)
                         │
┌────────────────────────▼─────────────────────────────────────────┐
│              EKTRO IME 前端  (Windows 输入服务进程)               │
│              ────────────────────────────────                     │
│  ┌─────────────────────┐    ┌──────────────────────────────┐    │
│  │ TSF Text Service    │    │  UI 渲染层                    │    │
│  │  • 拦截按键          │    │  • inline 候选 (默认)        │    │
│  │  • 上下文同步        │    │  • 淡灰预测 (停顿后)         │    │
│  │  • commit 到应用     │    │  • 候选窗 (长按 Ctrl 应急)   │    │
│  └──────────┬──────────┘    └───────────▲──────────────────┘    │
│             │                            │                       │
│             └──────────┬─────────────────┘                       │
│                        │                                          │
└────────────────────────┼──────────────────────────────────────────┘
                         │ Named Pipe (本地 IPC)
                         │
┌────────────────────────▼──────────────────────────────────────────┐
│         EKTRO Core 服务  (常驻后台进程，独立崩溃域)               │
│         ─────────────────────────────────────                     │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐    │
│  │             librime 引擎 (核心)                          │    │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌─────────┐ │    │
│  │  │Segmenter │→ │Translator│→ │  Filter  │→ │ Filter2 │ │    │
│  │  └──────────┘  └──────────┘  └────┬─────┘  └─────────┘ │    │
│  │                                    │                    │    │
│  │                          ★ EktroRerankFilter (新增)    │    │
│  │                          ────────────────────────       │    │
│  │                          基于 SQLite + GRU 重排         │    │
│  └──────────────────────────────────────────────────────────┘    │
│                                                                   │
│  ┌──────────────────────┐  ┌────────────────────────────────┐    │
│  │ EktroMemoryStore     │  │ EktroPredictor (子进程)         │    │
│  │ ────────────────     │  │ ───────────────                 │    │
│  │ SQLite (user.db)     │  │ Qwen3-0.6B + llama.cpp          │    │
│  │ commit_log           │  │ 停顿 >300ms 触发                │    │
│  │ word_freq            │  │ 失败/超时 → 静默降级            │    │
│  │ phrase_pair          │  │                                 │    │
│  └──────────────────────┘  └─────────────────────────────────┘    │
│                                                                   │
└───────────────────────────────────────────────────────────────────┘
                         │
                         ▼
              %APPDATA%\EKTRO\
                ├─ user.db        (SQLite，明文)
                ├─ models/        (GGUF + GRU)
                ├─ dict/          (万象拼音)
                └─ logs/          (调试日志，可关闭)
```

---

## 2. 进程模型与崩溃隔离

**为什么双进程**：输入法必须比应用更可靠。如果 ML 模块崩溃，用户不能因此打不了字。

```
┌──────────────────────────────────────────────────────────┐
│  崩溃矩阵                                                 │
│                                                          │
│   崩溃位置             用户感受             降级行为      │
│   ─────────────────────────────────────────────────────  │
│   IME 前端崩溃         几秒内系统恢复 IME    Windows 兜底│
│   Core 服务崩溃        无预测、无 rerank    纯 librime   │
│   Predictor 子进程     无淡灰续写            其余正常    │
│   librime 本身崩溃     IME 前端报错恢复     按英文输入   │
└──────────────────────────────────────────────────────────┘
```

**实现要点**：
- IME 前端永远不直接加载 ML 模型代码（避免污染崩溃域）
- IPC 调用全部加超时（默认 50ms），超时即降级
- Predictor 在 Core 内还独立 fork 子进程，加二级隔离
- 启动时 Core 服务由 IME 前端按需拉起（非 Windows 服务，无须管理员权限）

---

## 3. 候选生成管线（librime filter 插入位置）

```
        用户拼音输入
              │
              ▼
   ┌─────────────────────┐
   │   Segmenter         │  把 nihaoshijie 切成 nihao+shijie
   └──────────┬──────────┘
              │
              ▼
   ┌─────────────────────┐
   │   Translator        │  查万象词库，生成 N 个候选
   │   (万象 8-gram)     │  [你好世界, 你好十届, 泥嚎诗节, ...]
   └──────────┬──────────┘
              │
              ▼
   ┌─────────────────────┐
   │   Filter / Filter2  │  librime 内置过滤器
   └──────────┬──────────┘
              │
              ▼
   ╔═════════════════════╗
   ║ ★ EktroRerankFilter ║  ← 我们插入的新 filter
   ║                     ║  
   ║ 输入: top-N 候选     ║  N 通常 20
   ║ 上下文: 最近 commit  ║  最近 5 条
   ║ 用户词频: SQLite     ║  user.db 查询
   ║ GRU 打分: 端侧       ║  ~20M 参数
   ║ 输出: 重排序 top-N   ║  
   ║                     ║  
   ║ 耗时预算: ≤30ms P99 ║  
   ╚═════════════════════╝
              │
              ▼
   ┌─────────────────────┐
   │  IME 前端           │  取 top-1 直接 inline 渲染
   │  inline 渲染        │  其余候选缓存以备 Tab 切换
   └─────────────────────┘
```

**关键设计**：
- EktroRerankFilter 作为 librime 的 `Filter` 子类，**符合 librime 既有架构**，不是旁路
- GRU 模型加载在 Core 进程启动时，常驻内存（~80MB）
- 上下文窗口：最近 5 条 commit 的拼接，最长 256 字
- 失败时（GRU 加载失败/超时）：filter 直通，等于禁用，librime 原始结果照常返回

---

## 4. inline 渲染机制

**最难的工程问题**。TSF 有两套"渲染候选"的方式：

| 方式 | 说明 | 适用 |
|------|------|------|
| `ITfCandidateListUIElement` | 候选窗（系统绘制或自绘） | 传统输入法 |
| **Composition String** | 在光标位置直接插入"组字串"，应用渲染 | inline 候选 |

我们用 **Composition String + 自定义样式**：

```
   用户打 "nihao"
        │
        ▼
   IME 前端调用 ITfComposition::SetText("你好")
        │
        ▼
   应用（VSCode）按 IME composition 样式渲染（带下划线）
        │
        ▼
   用户按空格 → IME 前端 commit composition → "你好" 真正写入

   用户按 Tab 时：
        IME 前端取 cache 中下一个候选，重新 SetText("尼浩")
        应用更新组字串显示
```

**风险与边缘情况**：
- 不支持 Composition 的应用（极少数）→ 自动退化为候选窗
- 长 composition（句子级）在某些应用渲染怪 → 截断到合理长度（≤16 字）
- composition 期间用户点鼠标移焦 → IME 自动 commit 当前候选
- 兼容性测试矩阵：VSCode / Chrome / Edge / Word / 微信 / Notepad / cmd / PowerShell ISE

**淡灰预测**用另一种机制：
- 不进 composition（避免污染应用文本）
- 而是 IME 前端绘制一个**浮动透明窗口**贴在光标位置
- 文字用 50% 不透明度灰色
- Tab 接受 → IME 把内容当 composition 写入应用

---

## 5. 学习闭环

```
   每次用户 commit → 落库 → 影响下一次 rerank
   ─────────────────────────────────────────────

        用户 commit "你好世界"
              │
              ▼
   ┌──────────────────────┐
   │  Core 异步落库       │  不阻塞输入
   │                      │  
   │  commit_log:         │  INSERT (input='nihao...', output='你好世界',
   │                      │          app='VSCode', time=now())
   │                      │  
   │  word_freq:          │  UPDATE counter for 你/好/世/界
   │                      │  
   │  phrase_pair:        │  INCR phrase("你好", "世界")
   └──────────┬───────────┘
              │
              │ 离线/低优先级训练（每周 / 用户主动触发）
              ▼
   ┌──────────────────────┐
   │  GRU 增量训练         │  用最近 7-30 天 commit_log
   │  (端侧，CPU)         │  ~5-15 分钟一次完整训练
   │                      │  训完热切换模型，无重启
   └──────────────────────┘
```

**初始冷启动**：v0.1 首次安装时 GRU 用通用语料预训练版本（我们打包好），上线后增量学习。

**敏感数据处理**：
- 密码框检测（TSF 提供 `TF_ATTR_INPUTSCOPE` 包含 IS_PASSWORD）→ 不落库
- 银行卡号、身份证号正则识别 → 不落库
- 用户可标记应用为"私密"（任何在该应用的输入都不落库）

---

## 6. 性能预算

每次按键的 50ms 是怎么花的：

```
   用户按键
   ▼
   TSF 内核回调              ~3ms
   ▼
   IME 前端处理              ~2ms
   ▼
   IPC 到 Core (named pipe)  ~2ms
   ▼
   librime segmenter         ~1ms
   ▼
   librime translator        ~5ms     (查万象词库)
   ▼
   librime 内置 filters      ~2ms
   ▼
   ★ EktroRerankFilter      ~30ms   (GRU 前向)
     ├─ SQLite 查询上下文     ~3ms
     ├─ Tokenize              ~1ms
     ├─ GRU forward (~20M)    ~25ms
     └─ 重排                   ~1ms
   ▼
   IPC 返回前端              ~2ms
   ▼
   composition 写入应用      ~3ms
   ▼
   应用渲染                  ~?ms (应用自己负责)
   
   ─────────────────────────
   预算总计: ~50ms P99
```

**预算溢出时的应对**：rerank ≥30ms 时跳过本次 rerank，直接用 librime 原始 top-1。**永远不允许打字时卡住等模型**。

---

## 7. 失败模式与降级

| 故障 | 检测 | 降级行为 | 用户感知 |
|------|------|----------|---------|
| Core 服务未启动 | IPC 连接失败 | IME 前端按需拉起 Core | 首次输入轻微卡顿 |
| Core 崩溃 | IPC 连接断 | 切到纯 librime（无 rerank） | 候选回到通用排序 |
| GRU 加载失败 | 模型文件缺失/损坏 | filter 直通 | 个性化失效 |
| GRU 推理超时 | >30ms | 跳过本次 rerank | 偶尔候选不个性化 |
| Predictor 崩溃 | 子进程退出码 | 静默禁用淡灰预测 | 无淡灰续写 |
| Predictor 超时 | >200ms | 取消本次预测 | 偶尔无淡灰 |
| SQLite 锁/损坏 | IO 错误 | 只读模式，停止落库 | 学习暂停 |
| Composition 失败 | TSF 返回错误 | 退回候选窗 | 该应用显示候选窗 |
| 万象词库缺失 | 启动检查 | 错误提示，无法运行 | 安装失败提示 |

---

## 8. 数据模型

**SQLite Schema (user.db)**：

```sql
-- 每次 commit 一条
CREATE TABLE commit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   INTEGER NOT NULL,       -- unix ms
    input       TEXT NOT NULL,          -- 拼音原文 "nihaoshijie"
    output      TEXT NOT NULL,          -- commit 的中文 "你好世界"
    app_name    TEXT,                   -- 焦点应用 "Code.exe"
    context_id  INTEGER,                -- 上下文会话 id
    user_picked INTEGER DEFAULT 0       -- 是否用户按 Tab 选过
);
CREATE INDEX idx_commit_time ON commit_log(timestamp DESC);

-- 字/词频
CREATE TABLE word_freq (
    word        TEXT PRIMARY KEY,
    count       INTEGER NOT NULL DEFAULT 0,
    last_used   INTEGER NOT NULL        -- unix ms
);

-- 二元组（前一个词 → 当前词）
CREATE TABLE phrase_pair (
    prev        TEXT NOT NULL,
    curr        TEXT NOT NULL,
    count       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (prev, curr)
);

-- 用户标记的"私密"应用，不落库
CREATE TABLE privacy_exclude (
    app_name    TEXT PRIMARY KEY,
    reason      TEXT
);

-- 配置（≤6 项）
CREATE TABLE config (
    key         TEXT PRIMARY KEY,
    value       TEXT
);
```

**初始配置项**（≤6）：
1. `enable_rerank` (true/false)
2. `enable_predictor` (true/false)
3. `predictor_delay_ms` (default 300)
4. `learning_enabled` (true/false)
5. `excluded_apps` (JSON 数组)
6. `theme` ("light"/"dark"/"auto")

---

## 9. 关键技术决策详解

### D1: Fork weasel + librime（不重写 TSF）

**为什么**：weasel 团队过去 10 年解决的 TSF 兼容性坑（沙箱应用、DPI、IMM32、注册流程）一个人 6 个月做不完。
**代价**：继承 C++ 老代码风格；UI 古板。
**缓解**：UI 层我们重写（自己的 inline 渲染 + 淡灰预测器），核心引擎保留。

### D2: 词库用万象拼音（amzxyz）

**为什么**：32GB 自然语料 + 8-gram 模型，是 RIME 生态目前最优底座。
**代价**：成为"另一个 RIME 衍生品"的舆论压力。
**缓解**：我们的差异化在 UI 范式（inline 渲染 + 取消候选窗）和个性化（GRU rerank），不在词库本身。

### D3: Rerank 用 ~20M GRU，不是 LLM

**为什么**：
- LLM 实时打分 N 个候选 = N 次 forward，至少 200ms+。打字必死。
- GRU 是为 sequence scoring 设计的，~20M 参数足够 capture 个性化偏好。
- 端侧 CPU 推理 < 30ms。

**代价**：训练复杂度比"丢给 LLM"高。
**缓解**：初版用通用语料预训练 + 增量学习，框架不复杂。

### D4: 预测用 Qwen3-0.6B IQ4_XS

**为什么**：
- 0.6B 参数是端侧 LLM 当前最优 tradeoff（更小则中文太弱，更大则太慢）
- IQ4_XS 量化 ~350MB，能跑在普通 CPU
- 中文能力经实测能 handle 日常续写

**风险**：Week 1 必须实测首 token，不达 200ms 必须换方案。
**备选**：Qwen3-1.5B（更准但更慢） / Phi-4-mini（英文强但中文弱） / 自训 30M 小模型（最快但写出来像"语言模型 1.0"）。

### D5: 候选窗保留代码但默认隐藏

**为什么**：保留应急通道（长按 Ctrl 唤起）能：
- 用户首次学习时给安全感
- 兼容 inline 渲染失败的应用
- 调试时方便
- 不增加用户决策成本（默认不出现）

**代价**：UI 代码维护两条路径。
**缓解**：候选窗就用 weasel 原生的，不投入维护。

### D6: 双进程 + Named Pipe

**为什么**：见 §2，崩溃隔离是性命。
**代价**：IPC 增加 ~4ms 延迟，进程间内存不共享。
**缓解**：50ms 预算里 IPC 才占 4ms，可接受。

### D7: 明文 SQLite，不加密

**为什么**：
- 用户能用 SQLite 工具直接查看自己的数据（透明）
- 加密增加密钥管理复杂度，单用户场景收益低
- Windows 用户已经登录受 NTFS 权限保护

**代价**：物理访问机器的攻击者能读取。
**缓解**：用户可手动用 BitLocker / VeraCrypt 加密整个 %APPDATA%。

### D8: Apache 2.0 许可

**为什么**：
- 比 MIT 多专利授权条款，对贡献者友好
- 比 GPL 友好商业集成（未来如果想被 RIME 主流接纳）
- weasel 原始许可 BSD，兼容

**代价**：无显著代价。

---

## 10. 实施开放问题（Week 1 必须验证）

| # | 问题 | 验证 | 预案 |
|---|------|------|------|
| O1 | 万象首选准确率多少？ | 跑 1000 条 commit_log 样本 | <85% → 调整 GRU 训练目标 |
| O2 | Qwen3-0.6B IQ4_XS 实测首 token？ | llama-bench | >200ms → 换更小模型 |
| O3 | inline 在 UWP/Edge 是否工作？ | 实测 Edge + Mail | 不工作 → 这些应用默认候选窗 |
| O4 | weasel 在最新 Win11 24H2 注册是否稳？ | 干净 VM 部署 | 不稳 → 提前研究 TSF 注册细节 |
| O5 | GRU 框架选 ONNX Runtime 还是 ggml？ | benchmark | 看 O2 结果后定 |

---

## 11. 安全 / 威胁模型摘要

> 完整威胁模型在 `docs/threat-model.md`（v0.1 必须完成）。

**主要威胁**：
1. **恶意 PR 注入网络栈** → CI 静态扫描，Core 进程不允许 link winsock
2. **勒索软件读取 user.db** → 文档建议用户开启 BitLocker；密码字段不落库
3. **第三方应用读取 composition** → composition 短生命周期，commit 后立即清
4. **供应链攻击（依赖）** → 锁定依赖版本，定期 audit
5. **签名缺失被 SmartScreen 拦** → v1 后期投入 EV 签名

---

## 12. 度量与可观测

```
   ┌───────────────────────────────────────────────────┐
   │  本地度量（用户面板可见，可关）                   │
   │  ────────────────────────────                     │
   │  • 今日字数                                       │
   │  • Tab 切换次数 / 总 commit 数 = 错率代理        │
   │  • 淡灰预测接受率                                 │
   │  • 个性化命中率（rerank 改变 top-1 的比例）       │
   │                                                   │
   │  ⚠ 不收集、不上传任何度量。仅本地展示。           │
   └───────────────────────────────────────────────────┘
```

---

*相关文档：[proposal.md](./proposal.md) · [tasks.md](./tasks.md) · [CLAUDE.md](../../../CLAUDE.md)*
