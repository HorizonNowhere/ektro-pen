# EKTRO

> **Type like you.**
> 一个不让你选字的拼音输入法。

[![License](https://img.shields.io/badge/EKTRO%20source-Apache--2.0-blue.svg)](LICENSE)
[![Binary](https://img.shields.io/badge/portable%20binary-GPL--3.0-orange.svg)](NOTICE)
[![Platform](https://img.shields.io/badge/platform-Windows%2011%20x64-lightgrey.svg)]()
[![Status](https://img.shields.io/badge/status-v0.1%20alpha-orange.svg)](https://github.com/HorizonNowhere/ektro-pen/releases/tag/v0.1)

---

## 一句话

**EKTRO 是一个拼音输入法，它赌的是：你根本不需要看候选窗。**

你打 `nihaoshijie`，"你好世界"直接出现在光标处 —— 不弹窗、不打断、不需要瞟一眼再按数字键。错了再纠，它顺手学走。

---

## 为什么做这个

候选窗是一句道歉。

30 年来，中文输入法的范式没变过：你打一串拼音，机器弹一个窗，列 5~9 个候选，你停下来、移开视线、找、按数字键、回到正文。每 4 个字一次。一天几千次。

没人把这当 bug —— 因为大家默认"机器就是猜不准，所以得让你选"。

EKTRO 不接受这个默认。十年成熟的引擎 + 端侧个性化模型，已经足够让"第一候选就是你要的字"成为常态。既然如此，那个窗就不该存在。它打断的是你脑子里那句还没说完的话。

> 候选窗是机器不够聪明的赔礼道歉。我们把"每打 4 个字看一次窗"当成 bug。

---

## 它是什么感觉

```
   传统输入法                          EKTRO
   ─────────                          ─────
   nihaoshijie                        nihaoshijie
   ┌─────────────────────┐
   │1 你好世界 2 你好是界  │   ←打断    你好世界          ←光标处直接是中文
   │3 拟好时节 4 …        │            （没有窗，没有停顿）
   └─────────────────────┘
   ↓ 移开视线、找、按 1                ↓ 不对？按 Tab 切一下，它记住了
```

| 场景 | 行为 |
|------|------|
| **普通打字** | 输入 → 中文 inline 直接出现在光标，无候选窗，无中断 |
| **纠错** | 第一候选错了 → 按 `Tab` 切换替代 → 它自动学习你的偏好 |
| **长句预测** | 停顿 >300ms → 浮现一行淡灰续写建议 → `Tab` 接受（需端侧模型） |
| **后台学习** | 没有"训练"按钮。最近 24h 的输入就是它理解你的上下文 |
| **应急通道** | 长按 `Ctrl` ≥500ms → 唤起传统候选窗（留给那 1% 的极端情况） |

---

## 三戒（项目不动点，不可妥协）

```
① 不打断视线 (Calm typing)
     默认无候选窗。所有 UI 服从这一条。

② 不离开磁盘 (Local first)
     你的字、你的历史，永远在你的电脑上。
     明文 SQLite，可视、可导出、可删除。零网络请求（默认配置）。

③ 不解释自己 (No theatre)
     没有 AI 助手按钮、没有桌宠、没有教学浮窗。
     它不是一个"AI 产品"。它只是一个更懂你的输入法。
```

任何与这三条冲突的功能、PR、依赖，一律拒绝 —— 这不是路线图，是地基。

---

## 它怎么变聪明的（架构）

EKTRO 不是把拼音引擎换成大模型。实时打字这条通路必须 ≤30ms，大模型太重。
它的做法是**分层**：稳的部分用成熟引擎，聪明的部分放在你打不出延迟的地方。

```
   你的按键
      │
      ▼
   ┌────────────────────┐   librime 引擎（fork 自 weasel）
   │  全拼候选生成        │   万象/RIME 词库，8-gram 语言模型
   └─────────┬──────────┘
             ▼
   ┌────────────────────┐   端侧个性化 rerank（轻量，~ms 级）
   │  按"你"重排候选       │   特征：词频 / 上下文 / 最近输入 / 习惯
   └─────────┬──────────┘
             ▼
        inline 上屏（无窗）          ←── 实时通路到此结束，零打断
             │
             ┊  （停顿 >300ms，旁路）
             ▼
   ┌────────────────────┐   Qwen3-0.6B 端侧（llama.cpp，IQ4_XS 量化）
   │  下一句淡灰预测       │   纯本地推理，不联网
   └────────────────────┘

   ┌────────────────────┐   明文 SQLite（commit_log / word_freq / phrase_pair）
   │  每次上屏异步落库     │   你的数据，你能看、能导、能删
   └────────────────────┘
```

- **双进程崩溃隔离**：IME 前端 ⇄ Core 后端。Core 挂了，输入法自动降级回纯 librime，你不会突然打不了字。
- **证据驱动**：rerank 先上 baseline 特征跑通，再谈 ML —— 不为"用了 AI"而用 AI。

---

## 性能底线（验收线，破线的 PR 拒收）

```
   首屏候选 (inline 字)        ≤ 50ms   P99
   个性化 rerank 响应           ≤ 30ms   P99
   下一句预测首 token           ≤ 200ms  P95
   内存常驻 (含 0.6B 量化)      ≤ 800MB
   CPU 空闲占用                 < 1%
   默认配置网络请求数            = 0
```

延迟是这个项目的 P0。一个让你"感觉到"输入法存在的输入法，已经输了。

---

## 隐私

第二戒是认真的：

- 输入历史存在本机 `%APPDATA%\Rime` 的 **明文 SQLite**，不是黑盒、不加密锁死 —— 你能直接打开看、导出、删除。
- 个性化模型在你的电脑上推理，**不上传任何输入**。
- 默认配置下网络请求数 = 0。
- 卸载不会删你的数据；想彻底清除，手动删 `%APPDATA%\Rime` 即可。

"更懂你"的代价不应该是"把你交出去"。

---

## 快速开始（便携包，约 30 秒）

1. 从 [Releases](https://github.com/HorizonNowhere/ektro-pen/releases/tag/v0.1) 下载 `EKTRO-v0.1-portable-win64.zip`
2. 解压到任意目录
3. 右键 `安装EKTRO.bat` → **以管理员身份运行**，UAC 点「是」
4. `Win+Space` 切到「中州韵 / Rime」，打开记事本输入 `nihaoshijie`，看中文直接出现在光标处

> 便携包 ~13MB，**不含** 522MB 的 Qwen3-0.6B 端侧模型。
> 仅核心 inline 输入 / 纠错 / 学习无需模型；下一句淡灰预测需另行获取模型，
> 详见包内 `使用说明.txt` 与 [`INSTALL_AND_USE.md`](INSTALL_AND_USE.md)。

---

## 范围（克制是功能的一部分）

**v1 做**：全拼输入 · 端侧个性化 rerank · 上下文锁定（最近 24h）· Tab 切换替代候选 · 淡灰下一句预测 · 明文可导出 SQLite · 双进程崩溃隔离

**v1 明确不做**（PR 直接拒）：默认候选窗 · 桌宠 / Live2D · Chat 浮窗 · 语音输入 · 云端 LLM · 表情/颜文字面板 · 皮肤商店 · 多设备同步 · 几十个设置开关（上限 ≤6 个）

想加功能前先回答一个问题：它跟三戒冲突吗？任何"是"都意味着不做。

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

---

## 状态

v0.1 alpha。单人 6 周 Shape Up 周期的产物 —— 能不能每天用它打一整天字而不切回搜狗，是它的退出标准。
端侧延迟、首字准确率、长期个性化效果仍在打磨。

欢迎 issue / PR。唯一的硬规则：与三戒冲突的，再好也不做。
