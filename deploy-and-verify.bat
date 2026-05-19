@echo off
chcp 65001 >nul 2>&1
setlocal

REM ==================================================================
REM EKTRO deploy + verify (run after build-everything.bat)
REM   1. copy default.custom.yaml to %APPDATA%\Rime\
REM   2. call WeaselDeployer to redeploy
REM   3. prompt user to switch IME + test typing
REM Not automated: weasel-setup.exe install (needs admin)
REM ==================================================================

cd /d "%~dp0"
set "EKTRO_ROOT=%CD%"

echo ================================================================
echo  EKTRO deploy ^& verify
echo ================================================================
echo.

REM ---- 1. locate Weasel install ----

set "RIME_DIR=%APPDATA%\Rime"
if not exist "%RIME_DIR%" (
    echo ERROR: Rime dir not found ^(%RIME_DIR%^)
    echo Please install weasel-setup.exe first (admin).
    echo Path: %EKTRO_ROOT%\upstream\weasel-master\output\
    exit /b 1
)
echo [1/3] Rime dir: %RIME_DIR%

REM ---- 2. copy default.custom.yaml ----

set "SRC_YAML=%EKTRO_ROOT%\config\default.custom.yaml"
set "DST_YAML=%RIME_DIR%\default.custom.yaml"
if exist "%DST_YAML%" (
    echo [2/3] Backing up existing default.custom.yaml to .bak
    copy /Y "%DST_YAML%" "%DST_YAML%.bak" >nul
)
copy /Y "%SRC_YAML%" "%DST_YAML%"
echo [2/3] [OK] default.custom.yaml deployed

REM ---- 3. trigger Rime redeploy ----

set "WEASEL_INSTALL_REG=HKLM\SOFTWARE\Rime\Weasel"
for /f "tokens=2,*" %%a in ('reg query "%WEASEL_INSTALL_REG%" /v WeaselRoot 2^>nul') do (
    set "WEASEL_INSTALL=%%b"
)
if defined WEASEL_INSTALL (
    echo [3/3] Found Weasel: %WEASEL_INSTALL%
    if exist "%WEASEL_INSTALL%\WeaselDeployer.exe" (
        echo     Calling WeaselDeployer ...
        start "" "%WEASEL_INSTALL%\WeaselDeployer.exe" /deploy
        timeout /t 3 /nobreak >nul
        echo     [OK] Deployer triggered
    ) else (
        echo     WARN: WeaselDeployer.exe not found, right-click tray -^> Redeploy
    )
) else (
    echo [3/3] WARN: Weasel not installed, run weasel-setup.exe as admin first
)

echo.
echo ================================================================
echo  [OK] EKTRO deployed
echo ================================================================
echo.
echo Now test (any app that accepts text):
echo   1. Switch to Rime IME (Win+Space)
echo   2. In Notepad type: nihaoshijie
echo   3. Expected:
echo      [OK] "Ni Hao Shi Jie" inline (no candidate popup)
echo      [OK] Space commits, text enters document
echo      [OK] Hold Ctrl ^>=500ms shows emergency candidates
echo.

endlocal
