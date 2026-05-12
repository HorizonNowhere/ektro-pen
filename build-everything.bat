@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ──────────────────────────────────────────────────────────────────
REM EKTRO 一键完整 build (D-014)
REM
REM 自动:
REM   1. 找 VS 2022 BuildTools + 设 vcvars64
REM   2. 编译 EKTRO C++ 库 + 跑测试 (~3 分钟首次)
REM   3. 编译 Boost (如未编译, ~30-60 分钟首次)
REM   4. 编译 librime + weasel (~10-20 分钟)
REM
REM 不自动 (需用户手动做):
REM   - 安装 weasel-setup.exe (管理员权限)
REM   - 复制 default.custom.yaml 到 %APPDATA%\Rime\
REM   - 切换输入法 + Notepad 验证
REM ──────────────────────────────────────────────────────────────────

cd /d "%~dp0"
set "EKTRO_ROOT=%CD%"
set "WEASEL_ROOT=%EKTRO_ROOT%\upstream\weasel-master"
set "BOOST_ROOT=%WEASEL_ROOT%\deps\boost_1_84_0"

echo ┌────────────────────────────────────────────────────────────────┐
echo │  EKTRO 一键完整 build                                          │
echo │  预计总时间: 50-90 分钟 (首次, 含 Boost 编译)                  │
echo └────────────────────────────────────────────────────────────────┘
echo.

REM ──────────── 1. VS Developer Environment ────────────

echo [1/5] Activating Visual Studio 2022 Developer Command Prompt ...
set "VSWHERE=C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%VSWHERE%" (
    echo ERROR: vswhere not found. 请确认 VS 2022 BuildTools 已安装.
    exit /b 1
)
for /f "usebackq tokens=*" %%i in (`"%VSWHERE%" -latest -products * -property installationPath`) do (
    set "VS_INSTALL=%%i"
)
if not exist "!VS_INSTALL!\VC\Auxiliary\Build\vcvars64.bat" (
    echo ERROR: vcvars64.bat 未找到
    exit /b 1
)
call "!VS_INSTALL!\VC\Auxiliary\Build\vcvars64.bat" >nul
echo     ✓ MSVC env loaded

REM ──────────── 2. EKTRO C++ 库 ────────────

echo.
echo [2/5] Building EKTRO C++ library ...
cd /d "%EKTRO_ROOT%\src-cpp"
if not exist build (
    cmake -B build -G "Visual Studio 17 2022" -A x64
    if errorlevel 1 (
        echo ERROR: cmake configure failed
        exit /b 1
    )
)
cmake --build build --config Release --target ektro ektro_tests
if errorlevel 1 (
    echo ERROR: ektro build failed
    exit /b 1
)
echo     ✓ ektro.lib + ektro_tests.exe built

echo.
echo [2.5/5] Running EKTRO unit tests (22 cross-check)...
"build\Release\ektro_tests.exe" --gtest_brief=1
if errorlevel 1 (
    echo ERROR: ektro tests failed
    exit /b 1
)
echo     ✓ All 22 GoogleTest passed

REM ──────────── 3. Boost ────────────

echo.
echo [3/5] Checking Boost ...
if exist "%BOOST_ROOT%\stage\lib\libboost_regex*" (
    echo     ✓ Boost already built
) else (
    if not exist "%BOOST_ROOT%\bootstrap.bat" (
        echo ERROR: Boost source missing. 请先下载 boost_1_84_0.tar.gz 并解压到 deps\
        echo        URL: https://archives.boost.io/release/1.84.0/source/boost_1_84_0.tar.gz
        exit /b 1
    )
    cd /d "%BOOST_ROOT%"
    if not exist b2.exe (
        echo     Bootstrapping b2 ...
        call bootstrap.bat
        if errorlevel 1 (
            echo ERROR: bootstrap failed
            exit /b 1
        )
    )
    echo     Building Boost (~30-60 minutes first time) ...
    b2 ^
        --with-locale --with-regex --with-system --with-filesystem ^
        --with-chrono --with-thread --with-date_time ^
        toolset=msvc ^
        link=static runtime-link=static ^
        threading=multi variant=release ^
        address-model=64 -j4
    if errorlevel 1 (
        echo ERROR: boost b2 build failed
        exit /b 1
    )
    echo     ✓ Boost built
)

REM ──────────── 4. Weasel ────────────

echo.
echo [4/5] Building Weasel (含 EKTRO patch) ...
cd /d "%WEASEL_ROOT%"
call build.bat
if errorlevel 1 (
    echo ERROR: weasel build failed
    exit /b 1
)
echo     ✓ Weasel built

REM ──────────── 5. 提示用户后续 ────────────

echo.
echo ┌────────────────────────────────────────────────────────────────┐
echo │  ✅ EKTRO + Weasel 编译全部成功                                │
echo └────────────────────────────────────────────────────────────────┘
echo.
echo 接下来你需要手动 (要管理员权限):
echo.
echo   1. 安装 weasel-setup.exe (在 %WEASEL_ROOT%\output\):
echo        以管理员身份运行 weasel-setup.exe
echo.
echo   2. 复制 EKTRO 默认 yaml:
echo        copy "%EKTRO_ROOT%\config\default.custom.yaml" "%%APPDATA%%\Rime\"
echo.
echo   3. 重新部署 Rime (右键托盘图标 → 重新部署)
echo.
echo   4. 切到 EKTRO 输入法, 在 Notepad 打 nihaoshijie:
echo        ✓ "你好世界" inline 显示 (无候选窗)
echo        ✓ 按空格 commit
echo        ✓ 长按 Ctrl ≥500ms 唤起应急候选窗
echo.
echo 完成时即 EKTRO v0.1 落地。
echo.

endlocal
