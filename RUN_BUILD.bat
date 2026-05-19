@echo off
REM EKTRO build wrapper - uses %~dp0 (no hardcoded Chinese path, safe under GBK codepage)
chcp 65001 >nul
title EKTRO Build (Boost ~30-60 min + Weasel ~10-20 min)
cd /d "%~dp0"

set "LOG=%~dp0BUILD_LOG.txt"
set "MARKER=%~dp0BUILD_DONE_MARKER.txt"

echo === BUILD_START === %DATE% %TIME% > "%LOG%"
echo cwd=%CD% >> "%LOG%"
echo. >> "%LOG%"

call build-everything.bat >> "%LOG%" 2>&1
set BUILD_RC=%errorlevel%

echo. >> "%LOG%"
echo === BUILD_END === %DATE% %TIME% rc=%BUILD_RC% >> "%LOG%"
echo DONE_RC=%BUILD_RC% > "%MARKER%"

if %BUILD_RC% EQU 0 (
    echo BUILD SUCCESS
) else (
    echo BUILD FAILED rc=%BUILD_RC%
)
echo (Window stays open. Build log: %LOG%)
pause
