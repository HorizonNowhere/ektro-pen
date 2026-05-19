# EKTRO

> **Type like you.**
> 一个会长成你数字分身的拼音输入法。

[![License](https://img.shields.io/badge/EKTRO%20source-Apache--2.0-blue.svg)](LICENSE)
[![Binary](https://img.shields.io/badge/portable%20binary-GPL--3.0-orange.svg)](NOTICE)
[![Twin](https://img.shields.io/badge/twin-ektroai.com-7c3aed.svg)](https://ektroai.com)
[![Platform](https://img.shields.io/badge/platform-Windows%2011%20x64-lightgrey.svg)]()
[![Status](https://img.shields.io/badge/status-v0.2%20alpha-orange.svg)](https://github.com/HorizonNowhere/ektro-pen/releases)

---

## 一句话

**EKTRO 是一个拼音输入法 —— 也是你 AI 数字分身的入口。**

你打 `nihaoshijie`，"你好世界"直接出现在光标处，不弹窗、不打断。
与此同时，你打下的每个字，都在喂养 [ektroai.com](https://ektroai.com) 上**你的数字分身** ——
它在学你怎么说话、怎么思考，长成一个真正懂你、能替你说话的 AI Agent。

输入法是入口。分身是终点。

---

## 为什么做这个

**两件事，一个产品。**

**其一：候选窗是一句道歉。**
30 年来中文输入法的范式没变过：打一串拼音，弹一个窗，列 5~9 个候选，你停下、移开视线、找、按数字键、回到正文。每 4 个字一次，一天几千次。没人把这当 bug —— 因为大家默认"机器猜不准，所以得让你选"。EKTRO 不接受这个默认。第一候选就该是你要的字，那个窗就不该存在。

**其二：你每天打几千个字，它们却什么都没留下。**
你的措辞、你的口头禅、你思考的节奏 —— 这是世界上最贴近"你"的数据，而它在你按下回车的瞬间就被丢弃了。EKTRO 把它接住，同步到云端你的数字分身。你用得越久，那个分身越像你 —— 直到它能替你回消息、替你起草、替你说出你会说的话。

> Type like you —— 不只是输入法替你打字，是分身最终**像你一样**说话。

---

## 它是什么感觉

```
   传统输入法                          EKTRO
   ─────────                          ─────
   nihaoshijie                        nihaoshijie
   ┌─────────────────────┐
   │1 你好世界 2 你好是界  │   ←打断    你好世界          ←光标处直接是中文
   │3 拟好时节 4 …        │            （没有窗，没有停顿）
   └─────────────────────┘                  │
   ↓ 移开视线、找、按 1                       ┊ 后台异步
                                            ▼
                                    ektroai.com · 你的分身 +1 句你
```

| 场景 | 行为 |
|------|------|
| **普通打字** | 输入 → 中文 inline 直接出现在光标，无候选窗，无中断 |
| **纠错** | 第一候选错了 → 按 `Tab` 切换替代 → 它自动学习你的偏好 |
| **长句预测** | 停顿 >300ms → 浮现一行淡灰续写（由你的分身驱动）→ `Tab` 接受 |
| **喂养分身** | 没有"训练"按钮、没有"上传"键。你打字，分身就在长 |
| **应急通道** | 长按 `Ctrl` ≥500ms → 唤起传统候选窗（留给那 1% 的极端情况） |

---

## 三戒（项目不动点，不可妥协）

```
   ┌────────────────────────────────────────────────────┐
   │                                                    │
   │   ① 不打断视线 (Calm typing)                       │
   │      默认无候选窗，中文 inline 直接显示。           │
   │      打字这件事，永远安静。                        │
   │                                                    │
   │   ② 喂养你的分身 (Feed your twin)                  │
   │      你打的每个字，都是 ektroai.com 上              │
   │      你数字分身的养料。输入是入口，分身是终点。     │
   │                                                    │
   │   ③ 越用越是你 (It becomes you)                    │
   │      EKTRO 不掩饰自己是个 AI 产品。                 │
   │      它长成一个真正懂你、能替你说话的 AI Agent。    │
   │                                                    │
   └────────────────────────────────────────────────────┘
```

任何与这三条冲突的功能、PR、依赖，一律拒绝 —— 这不是路线图，是地基。

---

## 它怎么变聪明的（架构）

实时打字这条通路必须 ≤30ms —— 所以它**100% 本地**，云端一个字都插不进来。
分身的成长走**异步旁路**，在你打不出延迟的地方进行。两条路，互不打扰。

```
   你的按键
      │
      ▼  ───────────────── 实时通路：100% 本地，≤30ms，零打断 ──────────────
   ┌────────────────────┐   librime 引擎（fork 自 weasel）
   │  全拼候选生成        │   万象/RIME 词库，8-gram 语言模型
   └─────────┬──────────┘
             ▼
   ┌────────────────────┐   端侧个性化 rerank（轻量，~ms 级）
   │  按"你"重排候选       │   特征：词频 / 上下文 / 最近输入 / 习惯
   └─────────┬──────────┘
             ▼
        inline 上屏（无窗）        ←── 实时通路到此结束
             │
      ───────┊───────────────── 异步旁路：喂养分身，不阻塞输入 ──────────────
             ▼
   ┌────────────────────┐   本地明文 SQLite（commit_log / word_freq / phrase_pair）
   │  每次上屏异步落库     │   你机器上的捕获缓冲，可视、可导出、可删除
   └─────────┬──────────┘
             ▼  后台同步
   ┌────────────────────┐   ektroai.com · 你的数字分身
   │  云端学习 → AI Agent │   持续从你的输入学习，长成能替你说话的分身
   └────────────────────┘
```

- **双进程崩溃隔离**：IME 前端 ⇄ Core 后端。Core 挂了，输入法自动降级回纯 librime，你不会突然打不了字。
- **网络中断不影响打字**：分身同步是旁路，离线时本地照常输入，恢复后补传。
- **本地仍有完整明文副本**：上传不是"交出去就没了"，你机器上那份 SQLite 始终在，能看、能导、能删。

---

## 性能底线（验收线，破线的 PR 拒收）

```
   首屏候选 (inline 字)        ≤ 50ms   P99
   个性化 rerank 响应           ≤ 30ms   P99
   下一句预测首 token           ≤ 200ms  P95
   内存常驻                     ≤ 800MB
   CPU 空闲占用                 < 1%
   实时打字通路                 100% 本地，网络抖动零影响
   分身同步                     异步旁路，永不阻塞输入
```

延迟是这个项目的 P0。一个让你"感觉到"它存在的输入法，已经输了 —— 不管它多聪明。

---

## 你的字，你的分身（说清楚数据去哪）

第二戒是认真的，所以这一段不打太极：

- 你打的字会被记录到本机 `%APPDATA%\Rime` 的**明文 SQLite**，然后**同步到 ektroai.com 上你的数字分身**。这是 EKTRO 的核心机制，**不是可选的隐藏开关** —— 没有分身，EKTRO 就只是又一个输入法。
- 这意味着：**你的输入会离开本机、上传到云端**。如果你需要的是一个永不联网的本地输入法，EKTRO 不适合你 —— 我们宁愿说清楚，也不假装。
- 本机那份明文副本始终属于你：能直接打开看、导出、删除。分身的数据治理、导出与注销，见 [ektroai.com](https://ektroai.com)。
- 实时打字通路本身不联网，云端只拿你的输入用于成长你**自己的**分身。

"更懂你"是有代价的：你得把"你"交给一个会长成你的东西。EKTRO 选择把这件事摆在明处，由你决定要不要。

---

## 快速开始（便携包，约 30 秒）

1. 从 [Releases](https://github.com/HorizonNowhere/ektro-pen/releases) 下载 `EKTRO-vX.Y-portable-win64.zip`
2. 解压到任意目录
3. 右键 `安装EKTRO.bat` → **以管理员身份运行**，UAC 点「是」
4. `Win+Space` 切到「中州韵 / Rime」，打开记事本输入 `nihaoshijie`，看中文直接出现在光标处
5. 在 [ektroai.com](https://ektroai.com) 绑定账号，开始养你的分身

> 便携包 ~13MB，**不含** 522MB 的 Qwen3-0.6B 端侧模型。
> 核心 inline 输入 / 纠错 / 喂养分身无需本地模型；本地离线淡灰预测需另行获取模型，
> 详见包内 `使用说明.txt` 与 [`INSTALL_AND_USE.md`](INSTALL_AND_USE.md)。

---

## 范围（克制是功能的一部分）

**做**：全拼输入 · 端侧个性化 rerank · 上下文锁定（最近 24h）· Tab 切换替代候选 ·
淡灰下一句预测 · 本地明文 SQLite（可导出/删除）· **云端数字分身同步（ektroai.com）** ·
**分身演化为 AI Agent** · 双进程崩溃隔离

**明确不做**（PR 直接拒）：默认候选窗 · 桌宠 / Live2D · 打字时弹 Chat 浮窗 ·
语音输入 · 表情/颜文字面板 · 皮肤商店 · 几十个设置开关（上限 ≤6 个）

> 注：「不打断视线」约束的是**打字当下的 UI**。分身、AI Agent 活在 ektroai.com，
> 不以浮窗、桌宠、教学气泡的形式打扰你打字 —— 这是 ③ 与 ① 的边界。

想加功能前先问：它跟三戒冲突吗？任何"是"都意味着不做。

---

## 从源码构建

```cmd
build-everything.bat      :: VS 2022 + EKTRO C++ 库 + 22 GoogleTest + Boost + weasel
```

详见 [`src-cpp/INSTALL.md`](src-cpp/INSTALL.md) 与 [`INSTALL_AND_USE.md`](INSTALL_AND_USE.md)。
系统级 IME 注册需管理员权限，必须由你亲自完成（这一步设计上就不交给自动化）。

### 仓库结构

```
src/        Python 参考层（memory / rerank / predictor / context）
src-cpp/    C++ 实施层（生产路径，22 GoogleTest 通过）
config/     Rime 用户配置（inline_preedit: true 是"无窗"的开关）
upstream/   对 weasel 的 3 个补丁（patches/；weasel 源码按 .gitignore 不入库）
docs/       决策日志 decisions.md（含被砍掉的）+ 性能基准
openspec/   OpenSpec 变更流程
```

设计与完整决策链见 [`docs/decisions.md`](docs/decisions.md)、当前状态见 [`STATUS.md`](STATUS.md)、
项目宪法（三戒 + SOP）见 [`CLAUDE.md`](CLAUDE.md)。

---

## 许可

| 范围 | 许可 |
|------|------|
| EKTRO 自有源码（`src/` `src-cpp/` `config/` `upstream/patches/` 等） | [Apache License 2.0](LICENSE) |
| 便携二进制包**整体**（含 weasel 编译产物） | **GPL-3.0**（你有权获取并修改对应源码） |
| 第三方组件归属（weasel / librime / RIME 词库 / Qwen3 等） | 见 [`NOTICE`](NOTICE) |
| 云端分身服务 [ektroai.com](https://ektroai.com) | 独立服务，受其自身条款约束（非本仓库代码） |

---

## 状态

v0.2 alpha。输入法核心可日常使用；云端分身（ektroai.com）演化中。
端侧延迟、首字准确率、分身个性化效果仍在打磨。

欢迎 issue / PR。唯一的硬规则：与三戒冲突的，再好也不做。
