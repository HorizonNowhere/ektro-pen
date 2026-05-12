@echo off
chcp 65001 >nul
setlocal

REM ──────────────────────────────────────────────────────────────────
REM EKTRO 部署 + 验证 (build-everything.bat 之后跑)
REM
REM 自动:
REM   1. 复制 default.custom.yaml 到 %APPDATA%\Rime\
REM   2. 调 WeaselDeployer 重新部署
REM   3. 提示用户切输入法 + 测打字
REM
REM 不自动:
REM   - 安装 weasel-setup.exe (需管理员权限)
REM ──────────────────────────────────────────────────────────────────

cd /d "%~dp0"
set "EKTRO_ROOT=%CD%"

echo ┌────────────────────────────────────────────────────────────────┐
echo │  EKTRO 部署 ^& 验证                                              │
echo └────────────────────────────────────────────────────────────────┘
echo.

REM ──── 1. 找 Weasel 安装位置 ────

set "RIME_DIR=%APPDATA%\Rime"
if not exist "%RIME_DIR%" (
    echo ERROR: Rime 目录不存在 ^(%RIME_DIR%^)
    echo 请先安装 weasel-setup.exe (管理员权限)
    echo 路径: %EKTRO_ROOT%\upstream\weasel-master\output\
    exit /b 1
)
echo [1/3] Rime 目录: %RIME_DIR%

REM ──── 2. 复制 default.custom.yaml ────

set "SRC_YAML=%EKTRO_ROOT%\config\default.custom.yaml"
set "DST_YAML=%RIME_DIR%\default.custom.yaml"
if exist "%DST_YAML%" (
    echo [2/3] 备份现有 default.custom.yaml 为 .bak
    copy /Y "%DST_YAML%" "%DST_YAML%.bak" >nul
)
copy /Y "%SRC_YAML%" "%DST_YAML%"
echo [2/3] ✓ default.custom.yaml 已部署

REM ──── 3. 触发 Rime 重新部署 ────

set "WEASEL_INSTALL_REG=HKLM\SOFTWARE\Rime\Weasel"
for /f "tokens=2,*" %%a in ('reg query "%WEASEL_INSTALL_REG%" /v WeaselRoot 2^>nul') do (
    set "WEASEL_INSTALL=%%b"
)
if defined WEASEL_INSTALL (
    echo [3/3] 找到 Weasel: %WEASEL_INSTALL%
    if exist "%WEASEL_INSTALL%\WeaselDeployer.exe" (
        echo     调用 WeaselDeployer ^(可能弹窗^)...
        start "" "%WEASEL_INSTALL%\WeaselDeployer.exe" /deploy
        timeout /t 3 /nobreak >nul
        echo     ✓ Deployer 已触发
    ) else (
        echo     WARN: WeaselDeployer.exe 未找到, 请手动右键托盘 → 重新部署
    )
) else (
    echo [3/3] WARN: Weasel 未安装到系统, 请先以管理员身份跑 weasel-setup.exe
)

echo.
echo ┌────────────────────────────────────────────────────────────────┐
echo │  ✅ EKTRO 已部署                                                │
echo └────────────────────────────────────────────────────────────────┘
echo.
echo 现在测试 (任何能打字的应用):
echo   1. 切到 中州韵 / Rime 输入法 (Win+Space)
echo   2. 在 Notepad 输入: nihaoshijie
echo   3. 期望:
echo      ✓ "你好世界" 直接 inline 显示 (无候选窗)
echo      ✓ 按空格 commit, 字进入文档
echo      ✓ 长按 Ctrl ≥500ms 候选窗浮现, 松开消失
echo.

endlocal
