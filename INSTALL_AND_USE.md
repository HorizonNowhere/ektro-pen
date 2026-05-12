# EKTRO 安装和使用 — 用户操作手册

> 给立刻要用 EKTRO 的你。三戒优先, 物理操作清单。
> 更新于 2026-05-12.

---

## 一句话告诉你卡在哪

```
┌──────────────────────────────────────────────────────────────────────┐
│  EKTRO C++ 核心库已就绪 (22 单元测试通过)                            │
│  Boost build 在调试 (Windows toolchain auto-detect 故障)             │
│  weasel build / 装机 / Notepad 实测 — 待 boost 就绪后继续            │
└──────────────────────────────────────────────────────────────────────┘
```

**所以你 "现在能用" 的实质是**: Python 参考层 + Predictor (Qwen3-0.6B 端侧续写)。
**装系统 IME 的完整 EKTRO** 还需 weasel build 跑通, 这一步可能要等下次 session 或人工修 Boost.

---

## 路径 A: 立刻体验 (Python demo, ~30 秒)

这条路径**不需要**装 weasel, 不需要管理员权限.
你能体验:
- ✅ Memory store (打字会被异步记录 + 学习)
- ✅ Baseline reranker (4 特征 rerank 候选)
- ✅ Predictor (Qwen3-0.6B 续写下一句)

它**不能**体验:
- ❌ inline 候选 (没接 librime/TSF, 只是 Python 模拟)
- ❌ 长按 Ctrl 唤起候选窗
- ❌ Notepad 直接打字

### 跑

```cmd
cd /d E:\CLAUDE\EKTRO输入法
experience.bat
```

它会:
1. 启动 llama-server (载入 Qwen3-0.6B IQ4_XS, 522 MB)
2. 跑 Python 集成 demo (Memory + Rerank + Predictor 五阶段)
3. 你能看到 latency 数据 + 续写结果

期望终端输出:
```
Stage 1: Memory schema  ✓
Stage 2: 写入 commit log  ✓ (40 行/秒, P99 < 1ms)
Stage 3: Reranker 4 特征  ✓
Stage 4: Predictor 续写  ✓ 首 token < 200ms
Stage 5: 集成 demo  ✓
```

---

## 路径 B: 完整产品 (含 inline IME)

这条路径需要:
- ✅ VS 2022 BuildTools (你已有, 14.44.35207)
- ✅ Boost 1.84 源码 (已下载到 E:\bx)
- ✅ weasel-master 源码 (已 fork, 含 EKTRO 3 patches inline)
- ⏳ Boost build 跑通 (当前调试中)
- ⏳ weasel build 跑通 (依赖 Boost)
- ⏳ weasel-setup.exe 装机 (管理员权限, **你必须亲自做**)

### B-1. 编译 (可能要 1-3 小时, 含调试)

```cmd
cd /d E:\CLAUDE\EKTRO输入法
build-everything.bat
```

它会:
1. 找 VS 2022 BuildTools + 设 vcvars64
2. 编译 EKTRO C++ 库 + 跑 22 GoogleTest
3. 编译 Boost (~30-60 min)
4. 编译 weasel (~10-20 min, 含 3 patches)

**已知风险点**:
- Boost bootstrap 在 VS 2022 BuildTools (无 IDE) 下 toolset auto-detect 失败. 修法见 `docs/troubleshooting.md` 或在 PowerShell 装 vswhere 让其能找到 BuildTools.
- weasel 默认用 v142 (VS 2019). 我已在 `upstream/weasel-master/env.bat` 设 `PLATFORM_TOOLSET=v143 / BJAM_TOOLSET=msvc-14.3` 对齐 14.44.

### B-2. 装 weasel (你必须亲自做)

```
   ┌────────────────────────────────────────────────────────────────┐
   │ ⚠ 系统级 IME 注册必须管理员权限. Claude 无法替代.              │
   └────────────────────────────────────────────────────────────────┘
```

1. **找到 setup**: `E:\CLAUDE\EKTRO输入法\upstream\weasel-master\output\weasel-setup.exe`
2. **右键 → 以管理员身份运行** (UAC 弹窗点 "是")
3. 跟向导走完 (默认选项即可, 不要勾"始终在线"之类)
4. 装完后会有 "中州韵 (Rime)" 输入法项

### B-3. 部署 EKTRO 配置

```cmd
cd /d E:\CLAUDE\EKTRO输入法
deploy-and-verify.bat
```

它会:
1. 把 `config/default.custom.yaml` 复制到 `%APPDATA%\Rime\`
   - 关键设置: `inline_preedit: true` (候选直接显示, 不弹窗)
2. 调 WeaselDeployer.exe /deploy 触发 Rime 重新加载

### B-4. 切输入法 + Notepad 实测 (你必须亲自做)

```
Win + Space          切到 "中州韵 / Rime"
打开 Notepad         (Win + R, 输 notepad, 回车)
打 nihaoshijie       (拼音)
```

**期望** (三戒兑现):
```
✓ "你好世界" 直接 inline 显示在光标 (无候选窗弹出)
✓ 按空格 commit, 字进入文档
✓ 长按 Ctrl ≥500ms 候选窗浮现, 松开消失
✓ Tab 切换替代候选 (不打断打字)
```

---

## 故障排查

### Q1: Boost build 报 'Unknown toolset: vc143'

A: bootstrap.bat 的 toolset auto-detect 在 VS 2022 BuildTools (无 IDE) 下失败.
解决: 用一个**新的** "x64 Native Tools Command Prompt for VS 2022" 终端启动 build:

```
开始菜单 → "x64 Native Tools Command Prompt for VS 2022"
> cd /d E:\bx
> bootstrap.bat
> b2.exe ... (按 build-everything.bat 第 3 步参数)
```

这是 Microsoft 官方推荐的环境.

### Q2: 装 weasel 后 Win+Space 切不到 Rime

A: 看 控制面板 → 语言 → 中文(简体) → 选项 → 添加输入法 → 中州韵.

### Q3: inline 没工作 (还在弹候选窗)

A: 确认 `%APPDATA%\Rime\default.custom.yaml` 含:
```yaml
patch:
  "switches/@0/reset": 1     # inline 模式
  ascii_composer/good_old_caps_lock: true
```
然后 右键 WeaselDeployer 托盘图标 → 重新部署.

### Q4: 长按 Ctrl 没唤起候选

A: 这是 EKTRO patch 引入的特性. 必须用 `build-everything.bat` 编译出的
   `WeaselTSF.dll`, 不能用 weasel 官方 release 二进制 (它没这个 patch).

### Q5: Predictor 续写不工作

A:
1. 确认 `data/models/Qwen3-0.6B-from-ollama.gguf` 存在 (522 MB)
2. 确认 `tools/llama.cpp/build/bin/Release/llama-server.exe` 能跑
3. 跑 `experience.bat` 看 Predictor stage 输出

---

## 物理隔离: 哪些事必须你做

| 任务 | 谁做 | 原因 |
|------|------|------|
| 写代码 | Claude | ✓ |
| 编译 C++ | Claude | ✓ |
| 跑测试 | Claude | ✓ |
| 写配置 yaml | Claude | ✓ |
| **以管理员身份装 setup.exe** | **你** | UAC 弹窗 + 系统级 IME 注册 |
| **Win+Space 切输入法** | **你** | 物理键盘操作 |
| **Notepad 打字感受 inline** | **你** | 人眼验收 |

---

## 当前状态参考

| 组件 | 状态 |
|------|------|
| EKTRO Python 参考层 (79 测试) | ✅ |
| EKTRO C++ 实施层 (22 测试) | ✅ |
| Qwen3-0.6B 端侧模型 (522 MB) | ✅ |
| weasel-master 源码 + 3 patches inline | ✅ |
| Boost 1.84 源码 (E:\bx) | ✅ |
| Boost build 产物 (.lib) | ⏳ 待 bootstrap 修通 |
| weasel build 产物 (DLL + setup.exe) | ⏳ 待 Boost |
| 系统 IME 注册 | ❌ 用户必做 |
| Notepad 验收 | ❌ 用户必做 |

---

## 想立刻看到什么的快速参考

```
   你想:                          跑:
   ────────────────────────────────────────────────────
   "我现在就要打字"               experience.bat (Python demo)
   "我要完整产品"                 build-everything.bat 然后 B-2 起
   "我想看决策日志"               docs/decisions.md (D-001 → D-015)
   "我要 30 秒读完项目"           STATUS.md
   "我要看哲学"                   CLAUDE.md (三戒)
```

---

*三戒优先于功能. 三戒优先于交付. 三戒优先于"我现在就想要".*
*— EKTRO v0.1, 2026-05-12*
