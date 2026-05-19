@echo off
REM ==================================================================
REM EKTRO IME registration (self-elevating, ASCII-only, NON-blocking)
REM D-015: WeaselServer.exe must NOT be called synchronously (it is a
REM persistent GUI server -> blocks). Use taskkill + start instead.
REM ==================================================================
chcp 65001 >nul 2>&1
title EKTRO Register IME

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Requesting administrator privileges - click Yes on UAC...
    powershell -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b
)

set "EKTRO_ROOT=%~dp0"
if "%EKTRO_ROOT:~-1%"=="\" set "EKTRO_ROOT=%EKTRO_ROOT:~0,-1%"
set "WOUT=%EKTRO_ROOT%\upstream\weasel-master\output"
set "LOG=%EKTRO_ROOT%\REGISTER_LOG.txt"
set "MARKER=%EKTRO_ROOT%\REGISTER_MARKER.txt"

if exist "%MARKER%" del /q "%MARKER%"
echo === REGISTER_START === %DATE% %TIME% > "%LOG%"
echo running as admin >> "%LOG%"

if not exist "%WOUT%\WeaselSetup.exe" (
    echo ERROR: WeaselSetup.exe not found in %WOUT% >> "%LOG%"
    echo DONE_RC=30 > "%MARKER%"
    exit /b 1
)

echo [1/4] Killing any running Weasel processes (non-blocking) ... >> "%LOG%"
taskkill /F /IM WeaselServer.exe /T >nul 2>&1
taskkill /F /IM WeaselDeployer.exe /T >nul 2>&1
echo     done >> "%LOG%"

echo [2/4] Registering Weasel IME (Simplified, silent) ... >> "%LOG%"
cd /d "%WOUT%"
"%WOUT%\WeaselSetup.exe" /s >> "%LOG%" 2>&1
echo     WeaselSetup /s exit=%errorlevel% >> "%LOG%"

echo [3/4] Deploying EKTRO config ... >> "%LOG%"
set "RIME_DIR=%APPDATA%\Rime"
if not exist "%RIME_DIR%" mkdir "%RIME_DIR%"
if exist "%RIME_DIR%\default.custom.yaml" copy /Y "%RIME_DIR%\default.custom.yaml" "%RIME_DIR%\default.custom.yaml.bak" >nul
copy /Y "%EKTRO_ROOT%\config\default.custom.yaml" "%RIME_DIR%\default.custom.yaml" >> "%LOG%" 2>&1
echo     yaml deployed >> "%LOG%"

echo [4/4] Triggering Rime redeploy (async) ... >> "%LOG%"
set "WINST="
for /f "tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\WOW6432Node\Rime\Weasel" /v WeaselRoot 2^>nul') do set "WINST=%%b"
if not defined WINST for /f "tokens=2,*" %%a in ('reg query "HKLM\SOFTWARE\Rime\Weasel" /v WeaselRoot 2^>nul') do set "WINST=%%b"
if not defined WINST set "WINST=%WOUT%"
echo     WeaselRoot=%WINST% >> "%LOG%"
if exist "%WINST%\WeaselDeployer.exe" start "" /b "%WINST%\WeaselDeployer.exe" /deploy
if exist "%WINST%\WeaselServer.exe" start "" /b "%WINST%\WeaselServer.exe"

echo === REGISTER_END === %DATE% %TIME% rc=0 >> "%LOG%"
echo DONE_RC=0 > "%MARKER%"
exit /b 0
