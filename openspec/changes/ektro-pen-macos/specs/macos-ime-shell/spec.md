## ADDED Requirements

### Requirement: 基于 Squirrel fork 的 macOS IME 安装

ektro-pen-macos 项目 SHALL 基于 [rime/squirrel](https://github.com/rime/squirrel) 的 fork,保留 librime 静态链接与 macOS IMK Framework 集成,并通过 Apple Developer ID 签名后以 `.pkg` 形式分发。

Bundle ID MUST 设为 `org.ektroai.input.pen`(与上游 `im.rime.inputmethod.Squirrel` 区分),以允许同机两个 IME 共存。

#### Scenario: 用户安装 .pkg 后 IME 自动注册

- **WHEN** 用户双击下载的 ektro-pen-macos-vX.Y.pkg 一路下一步
- **THEN** 安装器将 .app 复制到 `~/Library/Input Methods/`,系统设置→键盘→输入源出现 "ektro-pen" 可选项

#### Scenario: 同机 Squirrel 与 ektro-pen-macos 共存

- **WHEN** 用户已安装 rime/squirrel,再装 ektro-pen-macos
- **THEN** 因 Bundle ID 不同,两个 IME 同时存在;用户可在系统设置切换;互不影响

### Requirement: 拼音→中文上屏复用 Squirrel 原生能力

候选窗渲染 / 焦点切换 / commit 上屏 / Tab 切换 / 标点符号处理等 macOS IME 基础行为 SHALL 直接继承 Squirrel 上游实现,fork 内不做底层重写。

#### Scenario: 用户打字"nihao"得到"你好"

- **WHEN** 用户在任意应用 (Notepad / Pages / Safari) 切到 ektro-pen-macos 输入法,敲 "nihao"
- **THEN** 候选窗弹出"你好"作为第一项;敲空格或回车上屏"你好"
