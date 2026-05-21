#!/usr/bin/env bash
# 安装 ektro-pen sync daemon 为 macOS LaunchAgent.
#
# 用法:
#   ./packaging/macos/install-launchagent.sh
#
# 安装后:
#   - 服务自动注册: ~/Library/LaunchAgents/com.ektro.sync.plist
#   - 立即启动 + 登录时自动起
#   - 日志: ~/Library/Logs/Ektro/sync.{out,err}.log
#
# 卸载: ./packaging/macos/uninstall-launchagent.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PYTHON="$(command -v python3)"
LOG_DIR="$HOME/Library/Logs/Ektro"
PLIST_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$PLIST_DIR/com.ektro.sync.plist"
TEMPLATE="$REPO_ROOT/packaging/macos/com.ektro.sync.plist.template"

# ── 检查环境 ──
if [[ "$(uname)" != "Darwin" ]]; then
    echo "❌ 这是 macOS 安装脚本; 当前: $(uname)" >&2
    exit 1
fi

if [[ -z "$PYTHON" ]]; then
    echo "❌ 找不到 python3" >&2
    exit 1
fi

PY_VERSION=$($PYTHON --version 2>&1)
echo "📍 REPO_ROOT: $REPO_ROOT"
echo "📍 PYTHON:    $PYTHON ($PY_VERSION)"
echo "📍 LOG_DIR:   $LOG_DIR"
echo "📍 PLIST:     $PLIST_PATH"
echo

# ── 检查 ektro-pen 已链接 ──
if ! EKTRO_DB_PATH="$EKTRO_DB_PATH" PYTHONPATH="$REPO_ROOT/src" "$PYTHON" -m auth status 2>&1 | grep -q "已链接$"; then
    echo "⚠ 警告: 当前未链接到 ektroai.com 账号"
    echo "  建议先跑: cd $REPO_ROOT && PYTHONPATH=src python3 -m auth link"
    echo "  否则 daemon 会立即退出 (没东西可 sync)"
    echo
    read -p "继续安装? [y/N] " yn
    [[ "$yn" =~ ^[Yy]$ ]] || { echo "已取消"; exit 0; }
fi

# ── 创建目录 ──
mkdir -p "$LOG_DIR"
mkdir -p "$PLIST_DIR"

# ── 渲染模板 ──
# 用 | 作为 sed 分隔符避免路径里的 / 冲突
sed -e "s|{{REPO_ROOT}}|$REPO_ROOT|g" \
    -e "s|{{PYTHON}}|$PYTHON|g" \
    -e "s|{{LOG_DIR}}|$LOG_DIR|g" \
    "$TEMPLATE" > "$PLIST_PATH"

echo "✓ plist 已写入: $PLIST_PATH"

# ── 卸载旧版 (如果已装) ──
if launchctl list | grep -q "com.ektro.sync"; then
    echo "→ 卸载旧版..."
    launchctl unload "$PLIST_PATH" 2>/dev/null || true
fi

# ── 装载 ──
echo "→ 装载 LaunchAgent..."
launchctl load "$PLIST_PATH"

# ── 验证 ──
sleep 1
if launchctl list | grep -q "com.ektro.sync"; then
    PID=$(launchctl list | grep "com.ektro.sync" | awk '{print $1}')
    if [[ "$PID" == "-" ]]; then
        echo "⚠ 服务已注册但未启动 (可能因未链接导致 daemon 退出 — 这是正常的)"
        echo "  查日志: tail -f $LOG_DIR/sync.err.log"
    else
        echo "✓ 服务运行中 (PID $PID)"
    fi
else
    echo "❌ 装载失败,请查 launchctl 状态" >&2
    exit 1
fi

echo
echo "✅ 安装完成"
echo
echo "管理命令:"
echo "  状态: launchctl list | grep ektro.sync"
echo "  停:   launchctl unload $PLIST_PATH"
echo "  起:   launchctl load $PLIST_PATH"
echo "  日志: tail -f $LOG_DIR/sync.{out,err}.log"
echo "  卸载: ./packaging/macos/uninstall-launchagent.sh"
