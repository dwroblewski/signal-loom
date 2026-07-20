#!/usr/bin/env bash
# One-time setup: point git at the tracked .githooks/ dir so the leak-guard
# pre-commit / pre-push hooks run. Git does NOT enable hooks automatically on
# clone, so every clone must run this once.
#
#   ./scripts/setup-hooks.sh
set -euo pipefail
ROOT=$(git rev-parse --show-toplevel)
git -C "$ROOT" config core.hooksPath .githooks
echo "✅ core.hooksPath set to .githooks"
if command -v gitleaks >/dev/null 2>&1; then
  echo "✅ gitleaks $(gitleaks version) found"
else
  echo "⚠️  gitleaks not found — install it for full secret scanning: brew install gitleaks"
fi
