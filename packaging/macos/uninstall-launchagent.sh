#!/usr/bin/env bash
# 卸载 ektro-pen sync daemon LaunchAgent.
#
# 用法:
#   ./packaging/macos/uninstall-launchagent.sh
#
# 这只卸载守护进程,不影响:
# - 本地 SQLite (commit_log 等用户数据始终归你)
# - macOS Keychain 中的链接凭证 (要彻底解绑跑: PYTHONPATH=src python3 -m auth unlink --confirm)

set -euo pipefail

PLIST_PATH="$HOME/Library/LaunchAgents/com.ektro.sync.plist"

if [[ ! -f "$PLIST_PATH" ]]; then
    echo "未安装 (找不到 $PLIST_PATH)"
    exit 0
fi

echo "→ 卸载 LaunchAgent..."
launchctl unload "$PLIST_PATH" 2>/dev/null || true

echo "→ 删除 plist..."
rm -f "$PLIST_PATH"

if launchctl list | grep -q "com.ektro.sync"; then
    echo "⚠ 服务仍在 launchctl 列表 (重启 macOS 后彻底清除)" >&2
else
    echo "✓ 已卸载"
fi

echo
echo "本地数据未动:"
echo "  - SQLite: ~/Library/Application Support/Ektro/ektro.db"
echo "  - 链接凭证: macOS Keychain (Service: ektro-pen)"
echo "  - 日志: ~/Library/Logs/Ektro/"
echo
echo "如需彻底解绑账号:"
echo "  cd \$(your-ektro-pen-repo) && PYTHONPATH=src python3 -m auth unlink --confirm"
