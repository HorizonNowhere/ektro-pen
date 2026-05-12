@echo off
chcp 65001 >nul
setlocal

REM ──────────────────────────────────────────────────────────────────
REM EKTRO 一键体验脚本
REM
REM 自动:
REM   1. 启动 llama-server (Qwen3-0.6B Q4_K_M)
REM   2. 跑全栈端到端 demo (Memory + Rerank + Predictor)
REM   3. 退出时关 llama-server
REM ──────────────────────────────────────────────────────────────────

cd /d "%~dp0"

set PYTHONIOENCODING=utf-8
set PYTHONPATH=src
set EKTRO_LOG_LEVEL=INFO

set MODEL_PATH=E:\ektro-models\qwen3-0.6b.gguf
set LLAMA_SERVER=tools\llama.cpp\llama-server.exe

REM ──────────── 检查依赖 ────────────

if not exist "%LLAMA_SERVER%" (
    echo [错误] 找不到 llama-server: %LLAMA_SERVER%
    echo 请先按 docs/cycle1-spike-day1.md 部署 llama.cpp
    exit /b 1
)

if not exist "%MODEL_PATH%" (
    echo [错误] 找不到模型: %MODEL_PATH%
    echo 请先按 docs/dev-mirrors.md 下载 Qwen3-0.6B
    exit /b 1
)

REM ──────────── 启动 llama-server ────────────

echo.
echo ┌────────────────────────────────────────────────────────────────┐
echo │  EKTRO 体验启动器                                              │
echo └────────────────────────────────────────────────────────────────┘
echo.

REM 检查 server 是否已经在跑
tasklist /FI "IMAGENAME eq llama-server.exe" 2>nul | find /I "llama-server.exe" >nul
if errorlevel 1 (
    echo [步骤 1/3] 启动 llama-server ...
    start /B "" "%LLAMA_SERVER%" -m "%MODEL_PATH%" --host 127.0.0.1 --port 8088 -t 6 --no-webui -c 4096 >NUL 2>&1
    echo            等待模型加载 ^(约 5-6 秒^) ...
    timeout /t 6 /nobreak >nul
    set STARTED_SERVER=1
) else (
    echo [步骤 1/3] llama-server 已在运行，跳过启动
    set STARTED_SERVER=0
)

REM ──────────── 健康检查 ────────────

echo [步骤 2/3] 健康检查 ...
python -c "import urllib.request; print('  ', urllib.request.urlopen('http://127.0.0.1:8088/health', timeout=3).read().decode())" 2>nul
if errorlevel 1 (
    echo [错误] llama-server 未响应。请检查 tools/llama.cpp/llama-server.exe
    if "%STARTED_SERVER%"=="1" taskkill /F /IM llama-server.exe >nul 2>&1
    exit /b 1
)

REM ──────────── 跑端到端 demo ────────────

echo.
echo [步骤 3/3] 跑全栈端到端 demo
echo.
python tests\integration\demo_full_pipeline.py
set DEMO_EXIT=%errorlevel%

REM ──────────── 收尾 ────────────

echo.
if "%STARTED_SERVER%"=="1" (
    echo [收尾] 关闭 llama-server ...
    taskkill /F /IM llama-server.exe >nul 2>&1
) else (
    echo [收尾] llama-server 之前就在运行，保留
)

echo.
echo ─────────────────────────────────────────────────────────────────
echo  体验完成。下次直接跑: experience.bat
echo  日志位置: %%LOCALAPPDATA%%\Ektro\logs\ektro.log
echo ─────────────────────────────────────────────────────────────────
echo.

exit /b %DEMO_EXIT%
