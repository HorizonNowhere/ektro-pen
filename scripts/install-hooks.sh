#!/usr/bin/env bash
# 启用本仓的 git hooks (密钥泄露提交前扫描)。
# 克隆后跑一次即可：  bash scripts/install-hooks.sh
# 原理：把 git 的 hooks 目录指向版本控制的 .githooks/ (而非 .git/hooks/)。
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
git config core.hooksPath .githooks
chmod +x .githooks/* 2>/dev/null || true

echo "✓ git hooks 已启用 (core.hooksPath = .githooks)"
if command -v gitleaks >/dev/null 2>&1; then
  echo "✓ gitleaks 已安装 ($(gitleaks version)) — 提交前会扫描暂存区"
else
  echo "⚠ gitleaks 未安装 — hook 会 soft-fail 放行。装上以启用防护:  brew install gitleaks"
fi
