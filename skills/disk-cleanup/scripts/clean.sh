#!/usr/bin/env bash
# Monthly easy-wins disk cleanup. All operations are non-destructive in the sense
# that anything removed is re-downloadable / re-creatable on next use (caches,
# stale installers, regenerable build artifacts). Nothing here touches user
# data, source repos, photo libraries, or app data.
#
# Invoked monthly by skills/schedule-crons (entry: "monthly-disk-cleanup").
# Writes a proactive Telegram notification ONLY when ≥1 GB was reclaimed —
# stays silent on small reclaims so the owner isn't pinged for noise.
#
# Exit codes:
#   0  — success (whether or not anything was reclaimed)
#   1  — script error before df measurement

set -uo pipefail

SUTANDO_WORKSPACE="${SUTANDO_WORKSPACE:-$HOME/.sutando/workspace}"
RESULTS_DIR="$SUTANDO_WORKSPACE/results"
mkdir -p "$RESULTS_DIR"

# df --output-block-size handling differs Linux vs macOS; use a portable kib
# extraction. Falls back to 0 if df fails for any reason.
disk_used_kib() {
    df -k "$HOME" 2>/dev/null | awk 'NR==2 {print $3+0}'
}

BEFORE_KIB="$(disk_used_kib)"
[ -z "$BEFORE_KIB" ] && BEFORE_KIB=0

log() { echo "[disk-cleanup] $*"; }

# Each block is best-effort — a failure in one (e.g. brew not installed)
# must not stop the rest. `|| true` keeps `set -e`-style discipline elsewhere
# without aborting the whole script.

log "1/7 Downloads installers ≥50 MB older than 30 days"
find "$HOME/Downloads" -maxdepth 1 -type f \
    \( -name "*.dmg" -o -name "*.pkg" -o -name "*.zip" \) \
    -size +50M -mtime +30 -delete 2>/dev/null || true

log "2/7 npm cache"
if command -v npm >/dev/null 2>&1; then
    npm cache clean --force >/dev/null 2>&1 || true
fi

log "3/7 Homebrew cache + downloads"
if command -v brew >/dev/null 2>&1; then
    brew cleanup -s >/dev/null 2>&1 || true
fi

log "4/7 Spotify cache"
rm -rf "$HOME/Library/Caches/com.spotify.client" 2>/dev/null || true

log "5/7 Electron app cache"
rm -rf "$HOME/Library/Caches/electron" 2>/dev/null || true

log "6/7 Gradle build caches"
rm -rf "$HOME/.gradle/caches" 2>/dev/null || true

log "7/7 pip cache (regenerates on next install)"
if command -v pip3 >/dev/null 2>&1; then
    pip3 cache purge >/dev/null 2>&1 || true
fi

AFTER_KIB="$(disk_used_kib)"
[ -z "$AFTER_KIB" ] && AFTER_KIB="$BEFORE_KIB"
RECLAIMED_KIB=$((BEFORE_KIB - AFTER_KIB))
# Guard against negative reclaim (background writes during the run).
[ "$RECLAIMED_KIB" -lt 0 ] && RECLAIMED_KIB=0

RECLAIMED_MB=$((RECLAIMED_KIB / 1024))
log "Reclaimed ${RECLAIMED_MB} MB"

# Notify only on meaningful reclaim. Threshold = 1024 MB (1 GB).
THRESHOLD_MB="${DISK_CLEANUP_NOTIFY_THRESHOLD_MB:-1024}"
if [ "$RECLAIMED_MB" -ge "$THRESHOLD_MB" ]; then
    TS="$(date +%s%3N 2>/dev/null || python3 -c 'import time;print(int(time.time()*1000))')"
    NOTIFY_FILE="$RESULTS_DIR/proactive-disk-cleanup-${TS}.txt"
    AVAIL_GB="$(df -g "$HOME" 2>/dev/null | awk 'NR==2 {print $4}')"
    PCT_FULL="$(df "$HOME" 2>/dev/null | awk 'NR==2 {print $5}')"
    cat > "$NOTIFY_FILE" << EOF
Monthly disk-cleanup reclaimed $((RECLAIMED_MB / 1024)).$(( (RECLAIMED_MB % 1024) * 10 / 1024 )) GB. Disk now ${AVAIL_GB} GB free (${PCT_FULL} used).
EOF
fi

exit 0
