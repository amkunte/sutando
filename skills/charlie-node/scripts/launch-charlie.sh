#!/usr/bin/env bash
# Launch the Charlie community/dev node's Discord bridge, fully isolated from the
# personal Sutando bot. See skills/charlie-node/SETUP.md for the one-time owner
# checklist (Discord app, token, access.json, GitHub identity).
#
# Isolation is achieved purely via env overrides — no shared state with the personal
# bot:
#   - SUTANDO_WORKSPACE            → Charlie's own tasks/results/state
#   - SUTANDO_DISCORD_CHANNELS_DIR → Charlie's own token + access.json
# Both default-safe: the personal bridge (which sets neither) is unaffected.
set -euo pipefail

# Flush bridge logs in real time (Python buffers stdout when not a tty, which hides
# the "ready" line and live activity behind nohup redirection).
export PYTHONUNBUFFERED=1

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# Hard-set Charlie's workspace + channels dir. Do NOT default off an inherited
# SUTANDO_WORKSPACE / SUTANDO_DISCORD_CHANNELS_DIR — when this script is launched
# from a context that already exports those (e.g. the personal sutando-core), a
# `${VAR:-default}` would inherit the PERSONAL workspace, collide on the personal
# bridge's single-instance lock, and Charlie would exit. Override only via the
# dedicated CHARLIE_* vars, which never leak from the personal core.
export SUTANDO_WORKSPACE="${CHARLIE_WORKSPACE:-$HOME/.sutando/workspace-charlie}"
export SUTANDO_DISCORD_CHANNELS_DIR="${CHARLIE_CHANNELS_DIR:-$HOME/.claude/channels/discord-charlie}"

CHANNELS_DIR="$SUTANDO_DISCORD_CHANNELS_DIR"
WS="$SUTANDO_WORKSPACE"

# Preflight — fail loud if the owner checklist isn't done (don't silently fall back to
# the personal bot's config, which would defeat the isolation).
if [ ! -f "$CHANNELS_DIR/.env" ]; then
  echo "✗ Charlie not configured: $CHANNELS_DIR/.env missing (DISCORD_BOT_TOKEN)." >&2
  echo "  Run the owner checklist in skills/charlie-node/SETUP.md first." >&2
  exit 1
fi
if [ ! -f "$CHANNELS_DIR/access.json" ]; then
  echo "✗ Charlie not configured: $CHANNELS_DIR/access.json missing." >&2
  echo "  Create it with {\"allowFrom\": [], \"tierMap\": {}, \"guilds\": {}} (see SETUP.md)." >&2
  exit 1
fi

mkdir -p "$WS/tasks" "$WS/results" "$WS/state" "$WS/logs"

echo "→ Charlie bridge starting"
echo "    workspace:    $WS"
echo "    channels dir: $CHANNELS_DIR"
cd "$REPO_DIR"
exec python3 src/discord-bridge.py
