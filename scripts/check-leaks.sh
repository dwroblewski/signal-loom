#!/usr/bin/env bash
# Shared leak scanner for signal-loom (a PUBLIC repo). Called by the git
# pre-commit and pre-push hooks and by the Claude Code pre-push guard.
#
#   scripts/check-leaks.sh staged            # scan staged changes (pre-commit)
#   scripts/check-leaks.sh range <A>..<B>    # scan a commit range (pre-push)
#
# Exits non-zero (blocking) when gitleaks finds a secret OR a repo-specific
# personal-data / forbidden-file pattern appears in the ADDED lines.
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

MODE="${1:-staged}"
RANGE="${2:-}"

fail() { echo "🚫 leak-guard: $*" >&2; exit 1; }

# The scanner's own files legitimately contain the detection patterns below, so
# exclude them from the pattern grep (gitleaks still scans them for real secrets).
EXCLUDES=(
  ':(exclude)scripts/check-leaks.sh'
  ':(exclude)scripts/claude_push_guard.py'
  ':(exclude).gitleaks.toml'
  ':(exclude).githooks/pre-commit'
  ':(exclude).githooks/pre-push'
)

# --- 1. gitleaks (dedicated secret scanner) ---
if command -v gitleaks >/dev/null 2>&1; then
  if [ "$MODE" = "staged" ]; then
    gitleaks git --staged --no-banner --redact --config .gitleaks.toml >&2 \
      || fail "gitleaks found a secret in staged changes."
  else
    [ -n "$RANGE" ] || fail "range mode needs a <base>..<tip> argument."
    gitleaks git --log-opts="$RANGE" --no-banner --redact --config .gitleaks.toml >&2 \
      || fail "gitleaks found a secret in the commits being pushed."
  fi
else
  echo "⚠️  leak-guard: gitleaks not installed — pattern checks only. Install: brew install gitleaks" >&2
fi

# --- 2. repo-specific personal-data patterns in added lines ---
if [ "$MODE" = "staged" ]; then
  DIFF=$(git diff --cached -U0 --no-color -- . "${EXCLUDES[@]}")
  NAMES=$(git diff --cached --name-only --diff-filter=AM -- . "${EXCLUDES[@]}")
else
  DIFF=$(git diff -U0 --no-color "$RANGE" -- . "${EXCLUDES[@]}" 2>/dev/null || true)
  NAMES=$(git diff --name-only --diff-filter=AM "$RANGE" -- . "${EXCLUDES[@]}" 2>/dev/null || true)
fi

ADDED=$(printf '%s\n' "$DIFF" | grep -E '^\+' | grep -Ev '^\+\+\+' || true)

check() {
  local pattern="$1" label="$2" hits
  # Allow the public repo URL (github.com/dwroblewski) which is expected.
  hits=$(printf '%s\n' "$ADDED" | grep -nEi "$pattern" | grep -viE 'github\.com/dwroblewski' || true)
  if [ -n "$hits" ]; then
    printf '%s\n' "$hits" >&2
    fail "$label"
  fi
}

check '/Users/[a-z]'                     "personal absolute path (/Users/...) in added content."
check 'danielwroblewski'                 "personal username in added content."
check '[a-z0-9._%+-]+@gmail\.com'        "personal email address in added content."

# --- 3. forbidden files that slipped past .gitignore (e.g. via git add -f) ---
if [ -n "$NAMES" ]; then
  BAD=$(printf '%s\n' "$NAMES" \
    | grep -E '(^|/)\.env(\.|$)|(^|/)config/.*\.ya?ml$' \
    | grep -vE '\.example\.ya?ml$' || true)
  if [ -n "$BAD" ]; then
    printf '%s\n' "$BAD" >&2
    fail "a real config or .env file is being committed/pushed (only *.example.yaml belongs in the public repo)."
  fi
fi

echo "✅ leak-guard: no secrets or personal data detected ($MODE)." >&2
exit 0
