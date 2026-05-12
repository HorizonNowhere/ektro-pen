# EKTRO 项目状态 — 实时快照

> 给 6 个月后的你 / 新接手者: 30 秒读懂当前进度。
> 更新于 D-014 (2026-05-12)

---

## 一句话状态

```
┌─────────────────────────────────────────────────────────────────────┐
│  EKTRO C++ 核心库 production-ready (22 测试通过)                    │
│  Weasel 集成 patch 已 inline                                         │
│  Boost build + weasel build + 装机器 — 留用户跑                      │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 当前完成度

```
   阶段                   状态      负责
   ──────────────────────────────────────────────────
   Cycle 1 Python 参考    ✅ 100%   Claude (D-001 → D-011)
   Cycle 2 C++ 移植       ✅ 100%   Claude (D-012 → D-014)
   ├─ C++ 实现 (~2400 行) ✅ 编译通过
   ├─ 22 GoogleTest       ✅ 全部通过
   ├─ third_party 依赖    ✅ vendored (sqlite3/httplib/json)
   └─ Weasel 3 patches    ✅ 已 inline 到源码

   Cycle 2 系统集成       ⏳ 80%    用户 (跑 build-everything.bat)
   ├─ Boost build         ⏳ 30-60 min 编译, 自动
   ├─ Weasel build        ⏳ 10-20 min, 自动
   ├─ 装 weasel-setup.exe ❌ 需管理员权限 + UI
   └─ Notepad 实测        ❌ 用户切输入法 + 打字
```

---

## 一键启动

### 第 1 步: 编译 (一条命令, 50-90 分钟)

```cmd
build-everything.bat
```

自动完成: VS env + EKTRO 库 + 22 GoogleTest + Boost build + Weasel build。

### 第 2 步: 装输入法 (要管理员权限)

右键 → 以管理员身份运行:
```
upstream\weasel-master\output\weasel-setup.exe
```

### 第 3 步: 部署 EKTRO 配置 (一条命令)

```cmd
deploy-and-verify.bat
```

### 第 4 步: 切输入法 + Notepad 测试

```
   Win+Space 切到 中州韵 / Rime
   Notepad 输入: nihaoshijie
   期望:
   ✓ "你好世界" inline 显示 (无候选窗弹出)
   ✓ 按空格 commit
   ✓ 长按 Ctrl ≥500ms 唤起应急候选窗
```

---

## 文件树

```
E:\CLAUDE\EKTRO输入法\
├── CLAUDE.md                    项目宪法 (三戒 + 10 SOP)
├── STATUS.md                    本文件
├── build-everything.bat         ★ 一键 build (50-90 min)
├── deploy-and-verify.bat        ★ 一键部署
├── experience.bat               一键体验 Python demo (无需 weasel)
│
├── docs/decisions.md            D-001 → D-014 完整决策链 + 13 反思
├── docs/cycle1-summary.md       Cycle 1 总结
│
├── src/                         Python 参考层 (79 测试, 全部通过)
│   ├── common/    logging
│   ├── shared/    context_builder + protocols
│   ├── memory/    schema + store + CLI 面板
│   ├── rerank/    baseline (4 特征)
│   └── predictor/ client + trigger + baseline
│
├── src-cpp/                     C++ 实施层 (22 测试, 编译通过)
│   ├── include/ektro/          7 个 .h (含 protocols)
│   ├── src/                    8 个 .cpp
│   ├── tests/                  3 个 GoogleTest 文件
│   ├── third_party/            sqlite3 + httplib + json (vendored)
│   ├── build/Release/          ✅ ektro.lib + ektro_tests.exe
│   ├── CMakeLists.txt          (/utf-8 + FetchContent)
│   └── INSTALL.md
│
├── config/default.custom.yaml   Rime 用户配置 (inline_preedit: true)
│
├── upstream/weasel-master/      ✅ 完整源码 (5.3 MB)
│   ├── librime/                ✅ submodule (2.7 MB)
│   ├── plum/                   ✅ submodule (25 KB)
│   ├── deps/boost_1_84_0.tar.gz ⏳ 下载中
│   └── WeaselTSF/              ✅ 3 patches inline
│       ├── Globals.h           (含 .bak)
│       ├── CandidateList.cpp   (含 .bak)
│       └── KeyEventSink.cpp    (含 .bak)
│
└── data/models/Qwen3-0.6B-from-ollama.gguf  ★ 522 MB (Predictor 用)
```

---

## 测试通过证明

### Python (79 tests, 17 秒)

```
tests/memory/test_store.py          28 测试  含隐私拦截 + 配置
tests/memory/test_boundary.py       13 测试  emoji + 零长 + SQL 注入
tests/memory/test_concurrency.py     2 测试  4 写 2 读 × 1000
tests/rerank/test_baseline.py       14 测试  含 P1.7 强断言
tests/predictor/test_client.py      15 测试  含 mock server
tests/predictor/test_trigger_race.py 4 测试  threading.Event 竞态
tests/integration/                   demo 5 阶段 (Memory+Rerank+Predictor)
```

### C++ (22 tests, 279 毫秒)

```
StoreFixture           15 测试  (schema/隐私/边界/SQL注入/并发 4W2R×500)
RerankFixture           5 测试  (4 特征强断言)
BaselinePredictor       2 测试  (空 / chain 续写)
```

---

## 决策链 (13 条)

| ID | 触发 | 关键决定 |
|----|------|---------|
| D-001 | Cycle 1 启动 | 批准三戒 + 6 周路线 |
| D-002 | Day 1 网络阻塞 | 阶段切分 + 早战略撤退 |
| D-003 | CLI Qwen3 数据 | 上下文 ≤30 token (后被 D-004 推翻) |
| D-004 | server 模式实测 | Predictor 用 llama-server + 双轨 SOP |
| D-005 | Week 2 完成 | Python 参考 + C++ 移植双语 |
| D-006 | inline 设计 | weasel 已有, 翻 flag 即可 |
| D-007 | Week 4 rerank | 先 baseline 再 ML (证据驱动) |
| D-008 | Cycle 1 总结 | 接口层完成 (后被 swarm 修正) |
| D-009 | Swarm 一轮 | P0/P1 14 项 |
| D-010 | P0/P1 清理 | 自评 A- (后被二轮 swarm 修正为 B+) |
| D-011 | Swarm 二轮 | P0.5 6 项 + Cycle 2 GO |
| D-012 | Cycle 2 Day 1-2 | fork + 4 C++ 文件 |
| D-013 | Cycle 2 Day 3-5 | 全栈 C++ + weasel patches |
| D-014 | 真实编译 | UTF-8 + sqlite + pImpl 三 bug 修复, 22/22 测试通过 |

---

## 给 6 个月后的你

如果你回到这个项目, 按这个顺序读:

1. `CLAUDE.md` — 三戒 + SOP (5 分钟)
2. `STATUS.md` (本文件) — 当前状态 (3 分钟)
3. `docs/decisions.md` 最新 3 条 D-XXX — 最近决策 (5 分钟)
4. `src-cpp/INSTALL.md` — 编译流程 (3 分钟)
5. 跑 `build-everything.bat` — 验证环境 (50-90 分钟)

整套 75 分钟内回到状态。
