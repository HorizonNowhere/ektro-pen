@echo off
REM EKTRO deploy wrapper - schtasks-spawned, escapes Claude sandbox
chcp 65001 >nul 2>&1
title EKTRO Deploy
cd /d "%~dp0"

set "LOG=%~dp0DEPLOY_LOG.txt"
set "MARKER=%~dp0DEPLOY_DONE_MARKER.txt"

echo === DEPLOY_START === %DATE% %TIME% > "%LOG%"
call deploy-and-verify.bat >> "%LOG%" 2>&1
set DEPLOY_RC=%errorlevel%
echo === DEPLOY_END === %DATE% %TIME% rc=%DEPLOY_RC% >> "%LOG%"
echo DONE_RC=%DEPLOY_RC% > "%MARKER%"
