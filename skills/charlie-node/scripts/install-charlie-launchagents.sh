#!/usr/bin/env bash
# Install launchd supervision for the Charlie community node (bridge + responder),
# so both auto-start at login and auto-restart on crash. Idempotent: re-running
# regenerates and reloads the agents.
#
# launchd runs jobs with a minimal environment, so we pin an explicit PATH that
# includes the nvm node bin (gemini/npx live there) and homebrew (git). Without
# it the responder can't find `gemini` and the bridge can't find `npx`.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
LA_DIR="${HOME}/Library/LaunchAgents"
LOG_DIR="${HOME}/.sutando/workspace-charlie/logs"
DOMAIN="gui/$(id -u)"
# Explicit PATH for launchd's minimal env. Order matters: nvm bin first (gemini/
# npx), then /usr/bin so `python3` resolves to /usr/bin/python3 — the one with the
# `discord` module installed (homebrew/usr-local python3 lack it → ModuleNotFound),
# then brew for git.
PATH_LINE="/Users/abhi/.nvm/versions/node/v24.14.1/bin:/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin:/usr/local/bin"

mkdir -p "$LA_DIR" "$LOG_DIR"

write_plist() {
  local label="$1" script="$2" out="$3"
  cat > "$out" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${REPO_DIR}/skills/charlie-node/scripts/${script}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>15</integer>
  <key>WorkingDirectory</key><string>${REPO_DIR}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>${PATH_LINE}</string>
    <key>HOME</key><string>${HOME}</string>
  </dict>
  <key>StandardOutPath</key><string>${LOG_DIR}/${label}.out.log</string>
  <key>StandardErrorPath</key><string>${LOG_DIR}/${label}.err.log</string>
</dict>
</plist>
PLIST
  echo "  wrote ${out}"
}

reload() {
  local label="$1" plist="$2"
  launchctl bootout "${DOMAIN}/${label}" 2>/dev/null || true
  launchctl bootstrap "${DOMAIN}" "${plist}"
  launchctl enable "${DOMAIN}/${label}"
  echo "  bootstrapped ${label}"
}

BRIDGE_LABEL="com.charlie.bridge"
RESP_LABEL="com.charlie.responder"
BRIDGE_PLIST="${LA_DIR}/${BRIDGE_LABEL}.plist"
RESP_PLIST="${LA_DIR}/${RESP_LABEL}.plist"

echo "→ installing Charlie launchd agents"
write_plist "$BRIDGE_LABEL" "launch-charlie.sh" "$BRIDGE_PLIST"
write_plist "$RESP_LABEL" "launch-charlie-responder.sh" "$RESP_PLIST"
reload "$BRIDGE_LABEL" "$BRIDGE_PLIST"
reload "$RESP_LABEL" "$RESP_PLIST"
echo "✓ Charlie is now supervised by launchd (auto-start at login, auto-restart on crash)."
