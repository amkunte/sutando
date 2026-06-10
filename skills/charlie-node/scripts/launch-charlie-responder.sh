#!/usr/bin/env bash
# Launch Charlie's responder core — consumes Charlie's task files and replies via
# gemini (read-only, hard-isolated). See charlie-responder.py for the safety model.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# Hard-set Charlie's workspace (never inherit the personal SUTANDO_WORKSPACE).
export CHARLIE_WORKSPACE="${CHARLIE_WORKSPACE:-$HOME/.sutando/workspace-charlie}"
export PYTHONUNBUFFERED=1

echo "→ charlie-responder starting (workspace: $CHARLIE_WORKSPACE)"
cd "$REPO_DIR"
exec python3 skills/charlie-node/scripts/charlie-responder.py
