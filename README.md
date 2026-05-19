# EKTRO

> **Type like you.**
> 一个不让你选字的拼音输入法。

[![License](https://img.shields.io/badge/EKTRO%20source-Apache--2.0-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2011%20x64-lightgrey.svg)]()
[![Status](https://img.shields.io/badge/status-v0.1%20alpha-orange.svg)]()

---

## 这是什么

输入法不该让你选字。候选窗是机器不够聪明的赔礼道歉。
30 年来没人把"每打 4 个字就要看一次候选窗"当成 bug —— EKTRO 把它当 bug。

EKTRO 是一个 Windows 拼音输入法，fork 自 [weasel](https://github.com/rime/weasel) /
[librime](https://github.com/rime/librime)，在其上加入端侧个性化与"无候选窗"交互。

### 三戒（不可妥协）

```
① 不打断视线 —— 默认无候选窗，中文 inline 直接显示
② 不离开磁盘 —— 你的字、你的历史，永远在你的电脑上（明文 SQLite，可导可删）
③ 不解释自己 —— 没有 AI 助手按钮、桌宠、教学浮窗
```

### 核心交互

| 场景 | 行为 |
|------|------|
| 普通打字 | 输入 → 中文 inline 直接出现，无候选窗，无中断 |
| 纠错 | 错了 → 按 `Tab` 切换替代候选 → 自动学习 |
| 长句预测 | 停顿 >300ms → 浮现淡灰续写 → `Tab` 接受（需端侧模型） |
| 应急通道 | 长按 `Ctrl` ≥500ms → 唤起传统候选窗（99% 用不到） |

---

## 快速开始（便携包，约 30 秒）

1. 从 [Releases](../../releases) 下载 `EKTRO-v0.1-portable-win64.zip`
2. 解压到任意目录
3. 右键 `安装EKTRO.bat` → **以管理员身份运行**，UAC 点「是」
4. `Win+Space` 切到「中州韵 / Rime」，打开记事本输入 `nihaoshijie`

便携包不含 522MB 的 Qwen3-0.6B 端侧模型；仅核心 inline 输入无需模型。
下一句淡灰预测需另行获取模型，详见 [`INSTALL_AND_USE.md`](INSTALL_AND_USE.md)。

---

## 从源码构建

```cmd
build-everything.bat      :: VS 2022 + EKTRO C++ 库 + 测试 + Boost + weasel
```

详见 [`src-cpp/INSTALL.md`](src-cpp/INSTALL.md) 与 [`INSTALL_AND_USE.md`](INSTALL_AND_USE.md)。
系统级 IME 注册需管理员权限，必须用户亲自完成。

---

## 仓库结构

```
src/        Python 参考层（memory / rerank / predictor / context）
src-cpp/    C++ 实施层（生产路径，22 GoogleTest 通过）
config/     Rime 用户配置（inline_preedit: true）
upstream/   对 weasel 的补丁（patches/，源码按 .gitignore 不入库）
docs/       决策日志 decisions.md + 性能基准
openspec/   OpenSpec 变更流程
```

设计与决策链见 [`docs/decisions.md`](docs/decisions.md)、[`STATUS.md`](STATUS.md)、
项目宪法 [`CLAUDE.md`](CLAUDE.md)。

---

## 许可

- **EKTRO 自有源码**：[Apache License 2.0](LICENSE)
- **便携二进制包整体**：因包含 weasel 编译产物，受 **GPL-3.0** 约束
- 第三方组件归属与许可说明见 [`NOTICE`](NOTICE)

---

## 状态

v0.1 alpha。单人 6 周 Shape Up 周期产物，端侧延迟 / 首字准确率仍在打磨。
欢迎 issue / PR —— 但任何与三戒冲突的功能一律不做。
