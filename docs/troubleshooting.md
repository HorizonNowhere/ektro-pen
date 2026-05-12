# EKTRO 构建故障排查 (Cycle 2 Day 5+)

> 给重新尝试 build 的人 (你 / 你 6 个月后 / 接手的 AI).
> 这些坑是 2026-05-12 这一晚踩过的, 记录下来防止重复.

---

## 真凶 #1: `NoDefaultCurrentDirectoryInExePath` (D-015, 2026-05-12)

### 症状

```
> cd /d E:\bx
> bootstrap.bat vc143
'bootstrap.bat' 不是内部或外部命令
```

OR

```
> bootstrap.bat ...
Building Boost.Build engine
'config_toolset.bat' 不是内部或外部命令
###
### "Unknown toolset: vc143"
###
```

### 原因

Windows 在 Claude Code 的 sandbox / PowerShell 之类的受限 shell 中默认设置环境变量:
```
NoDefaultCurrentDirectoryInExePath=1
```

这阻止 `cmd.exe` 在当前目录搜索可执行文件 / batch 文件. Boost 的 `bootstrap.bat`
内部 `pushd tools\build\src\engine` 之后 `call .\build.bat`,
`build.bat` 内部 `call config_toolset.bat` (无 `.\` 前缀) — 在 sandbox 下找不到.

### 修复

在 batch 顶部 (vcvars64 调用之前) 注入:
```bat
set "NoDefaultCurrentDirectoryInExePath="
set "PATH=.;%PATH%"
```

且在 vcvars64.bat 后重新 inject (vcvars 会重写 PATH):
```bat
call vcvars64.bat >nul
set "PATH=.;%PATH%"
```

完整脚本见 `E:\bx\boost_full_clean_build.bat` v6.

---

## 真凶 #2: VS 2022 BuildTools (无 IDE) 不被 vswhere 识别

### 症状

```
> vswhere.exe -latest -property installationPath
(empty output)
```

但 `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools` 物理存在.

### 原因

BuildTools 是手工 / OneDrive 同步过来的, 没注册到 VS Installer 数据库.

### 修复

不依赖 vswhere, 直接 hardcode 路径:
```bat
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
```

或者在 build-everything.bat 里写 fallback: 先试 vswhere, 失败则用 hardcode.

---

## 真凶 #3: PowerShell `cmd /c` 嵌套时 `%CD%` 太早展开

### 症状

```
> cmd /c "cd /d E:\bx && echo %CD%"
cwd=E:\CLAUDE\... (原 PowerShell cwd, 不是 E:\bx)
```

但 `pushd` 实际**成功**了, 只是 `%CD%` 在 cmd /c 字符串解析时**预展开**了, 显示的是 spawning shell 的 cwd.

### 教训

不要用 `%CD%` 做诊断, 用 `dir` 找具体文件来证实 cwd.

---

## 真凶 #4: Boost 1.84 b2 缓存

### 症状

```
- has BCrypt API           : no  (cached) [2]
- x86                      : no  (cached) [2]
...skipped <pE:\bx\stage\lib>libboost_*  for lack of <pbin.v2\standalone\msvc\msvc-14.3>msvc-setup.nup...
```

### 原因

`bin.v2/project-cache.jam` 缓存了 "工具集探测失败" 状态. 即使后续修复了
vcvars64 加载, b2 还是用 cached 结果.

### 修复

```bat
rmdir /s /q E:\bx\bin.v2
del E:\bx\project-config.jam
del E:\bx\b2.exe
```

然后重 bootstrap.

---

## 真凶 #5: KeyEventSink.cpp 引用 ektro::g_force_show_candidates extern

### 症状

```
WeaselTSF.dll : unresolved external symbol
  "bool ektro::g_force_show_candidates"
```

### 原因

EKTRO patch 03-keyeventsink-ctrl-hold.patch 在 WeaselTSF 引用 `ektro` namespace
变量, 但 `WeaselTSF.vcxproj` 没 link `ektro.lib`.

### 修复

(待做): 修改 `upstream/weasel-master/WeaselTSF/WeaselTSF.vcxproj`, 加:

```xml
<ItemDefinitionGroup>
  <Link>
    <AdditionalLibraryDirectories>
      $(SolutionDir)..\..\..\src-cpp\build\Release;%(AdditionalLibraryDirectories)
    </AdditionalLibraryDirectories>
    <AdditionalDependencies>
      ektro.lib;ektro_sqlite3.lib;%(AdditionalDependencies)
    </AdditionalDependencies>
  </Link>
</ItemDefinitionGroup>
```

且在 `Globals.cpp` (或新建 ektro_glue.cpp) 加:
```cpp
namespace ektro { volatile bool g_force_show_candidates = false; }
```

— 提供 extern 的 definition.

---

## 调试技巧

### 检查 PATH 是否含 `.`

```bat
cmd /c "echo %PATH%" | findstr /B "\."
```

### 检查 cwd 真实位置

```bat
dir bootstrap.bat 2>nul && echo HIT_CWD || echo MISS_CWD
```

(不要用 `echo %CD%` — 嵌套 shell 中预展开会撒谎)

### 监控 Boost build 进度

```bat
powershell -Command "Get-ChildItem E:\bx\stage\lib\*.lib | Measure-Object | Select-Object -ExpandProperty Count"
```

— 完成时应该 ≥ 7 (locale, regex, system, filesystem, chrono, thread, date_time).

### 看 b2 真实卡哪里

```bat
type E:\bx\b2_clean_stdout.log | findstr /C:"updated" /C:"skipped" /C:"failed"
```

---

## 完整修通流程 (摘要)

1. **清缓存**:
   ```
   rmdir /s /q E:\bx\bin.v2
   del E:\bx\project-config.jam E:\bx\b2.exe
   ```

2. **跑修复版 build 脚本**:
   ```
   E:\bx\boost_full_clean_build.bat (v6)
   ```

3. **若 b2 build 成功**, stage\lib 下有 ~7 个 .lib

4. **修 WeaselTSF.vcxproj** 加 ektro.lib 依赖

5. **跑 weasel build**:
   ```
   cd /d E:\CLAUDE\EKTRO输入法\upstream\weasel-master
   build.bat (会读 env.bat 拿 BOOST_ROOT=E:\bx)
   ```

6. **产出 weasel-setup.exe**:
   ```
   E:\CLAUDE\EKTRO输入法\upstream\weasel-master\output\weasel-setup.exe
   ```

---

*三戒铁律: 不打断视线 / 不离开磁盘 / 不解释自己.*
*工具链铁律: 每个 Windows batch path 都假设它会失败一次, 把 fallback 写进去.*
