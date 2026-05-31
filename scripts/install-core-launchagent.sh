#!/bin/bash
# scripts/install-core-launchagent.sh — install a launchd watchdog that keeps
# the sutando-core Claude Code session alive across reboots, logouts, and
# crashes.
#
# WHY: the in-session crons (morning briefing, scans, proactive loop) only fire
# while the sutando-core Claude session is running. Those crons are session-only
# — if the session dies (reboot, ⌘Q, crash), nothing restarts it and every
# scheduled job silently stops. On 2026-05-31 the 6:57am briefing never fired
# because the session wasn't alive overnight. This agent closes that gap.
#
# HOW: a LaunchAgent (com.sutando.core) that runs the canonical, idempotent
# `scripts/start-cli.sh`:
#   - RunAtLoad      → starts the session at login/boot.
#   - StartInterval  → re-runs every 120s as a watchdog. start-cli.sh is a
#                      no-op when the session is already alive (pgrep guard),
#                      and restarts it within ~2 min if it died.
# start-cli.sh launched with no TTY starts the tmux session DETACHED and exits
# 0, so launchd never holds a long-running child — the tmux server owns the
# session. No KeepAlive needed (which would fight the immediate exit).
#
# Headless note: this keeps the *interactive* session alive rather than running
# skills headlessly — deliberately. The morning-briefing skill (and others)
# depend on the claude.ai Google MCP connectors, which only exist inside the
# authenticated session. A headless `claude -- /morning-briefing` spawned by
# launchd would lack those connectors and re-break the briefing. Keeping the
# session up means the in-session cron fires with connectors intact.
#
# Usage:
#   bash scripts/install-core-launchagent.sh            # install + load
#   bash scripts/install-core-launchagent.sh --uninstall # unload + remove
#   bash scripts/install-core-launchagent.sh --status    # show load state
#
# Idempotent: re-running re-generates the plist (picking up moved binaries)
# and reloads. Safe to run from inside the sutando-core session — it never
# kills the running session (start-cli.sh without --restart only ever attaches
# or no-ops when one is already up).

set -euo pipefail

LABEL="com.sutando.core"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="${HOME}/Library/LaunchAgents/${LABEL}.plist"
WORKSPACE="${SUTANDO_WORKSPACE:-${HOME}/.sutando/workspace}"
WORKSPACE="${WORKSPACE/#\~/$HOME}"
DOMAIN="gui/$(id -u)"

uninstall() {
  echo "Uninstalling ${LABEL}…"
  launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
  rm -f "$PLIST"
  echo "  removed $PLIST and unloaded the agent."
  echo "  (the currently-running sutando-core session is left untouched.)"
}

status() {
  echo "plist:   $([ -f "$PLIST" ] && echo "$PLIST" || echo "(not installed)")"
  echo -n "loaded:  "; launchctl print "${DOMAIN}/${LABEL}" >/dev/null 2>&1 && echo "yes" || echo "no"
  echo -n "session: "; pgrep -f "claude.*--name.*sutando-core" >/dev/null 2>&1 && echo "running" || echo "DOWN"
}

case "${1:-}" in
  --uninstall) uninstall; exit 0 ;;
  --status)    status;    exit 0 ;;
esac

# --- Resolve the runtime environment launchd will need -----------------------
# launchd agents start with a bare PATH, so claude/tmux/node won't resolve
# unless we set it explicitly. Resolve the dirs dynamically (don't hardcode the
# nvm version) so this survives a node upgrade and works on other hosts.
need() {
  command -v "$1" >/dev/null 2>&1 || { echo "ERROR: '$1' not on PATH — cannot install" >&2; exit 1; }
}
need claude
need tmux

CLAUDE_BIN_DIR="$(cd "$(dirname "$(command -v claude)")" && pwd)"   # nvm node bin
TMUX_BIN_DIR="$(cd "$(dirname "$(command -v tmux)")" && pwd)"       # homebrew bin
LAUNCH_PATH="${CLAUDE_BIN_DIR}:${TMUX_BIN_DIR}:/usr/bin:/bin:/usr/sbin:/sbin"

mkdir -p "${HOME}/Library/LaunchAgents" "${WORKSPACE}/logs"

# --- Generate the plist ------------------------------------------------------
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${REPO}/scripts/start-cli.sh</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>${LAUNCH_PATH}</string>
    <key>SUTANDO_WORKSPACE</key>
    <string>${WORKSPACE}</string>
  </dict>
  <key>WorkingDirectory</key>
  <string>${REPO}</string>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>120</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>${WORKSPACE}/logs/launchd-core.log</string>
  <key>StandardErrorPath</key>
  <string>${WORKSPACE}/logs/launchd-core.err</string>
</dict>
</plist>
PLIST_EOF

echo "Wrote $PLIST"

# --- (Re)load ----------------------------------------------------------------
# bootout first so a re-install picks up plist changes; ignore "not loaded".
launchctl bootout "${DOMAIN}/${LABEL}" 2>/dev/null || true
launchctl bootstrap "${DOMAIN}" "$PLIST"

echo "Loaded ${LABEL} into ${DOMAIN}."
echo ""
status
echo ""
echo "The watchdog re-checks every 120s and restarts sutando-core if it dies."
echo "Logs: ${WORKSPACE}/logs/launchd-core.{log,err}"
echo "Uninstall: bash scripts/install-core-launchagent.sh --uninstall"
