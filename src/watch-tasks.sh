#!/bin/bash
# Watch for new task files in tasks/ directory.
# Usage: bash src/watch-tasks.sh (run_in_background: true)
#
# Uses fswatch -1 to detect one new file, then exits.
# The caller processes tasks and restarts.

TASKS_DIR="${1:-$(dirname "$0")/../tasks}"

# Ensure required directories exist
mkdir -p "$TASKS_DIR"
mkdir -p "$(dirname "$0")/../results/calls"

# Single-instance lock — prevents duplicate watchers from accumulating
# when /proactive-loop restarts the watcher while one is already running.
LOCK_DIR="/tmp/sutando-watch-tasks.lock.d"
# Clear stale lock (older than 30 min — watcher should never wait that long)
if [ -d "$LOCK_DIR" ] && find "$LOCK_DIR" -maxdepth 0 -mmin +30 2>/dev/null | grep -q .; then
  rm -rf "$LOCK_DIR"
fi
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "watch-tasks: another watcher is already running, exiting."
  exit 0
fi
trap 'rm -rf "$LOCK_DIR"' EXIT INT TERM

# Wait for a new .txt file — loop until one actually appears
while true; do
  # Check for existing files BEFORE waiting (catches files written during restarts)
  if ls "$TASKS_DIR"/*.txt >/dev/null 2>&1; then
    echo "TASK_DETECTED: Process ALL .txt files in tasks/."
    for f in "$TASKS_DIR"/*.txt; do echo "--- $(basename "$f") ---"; cat "$f"; done
    break
  fi
  # Wait for next filesystem event
  CHANGED=$(fswatch -1 -l 1 "$TASKS_DIR" 2>/dev/null)
  echo "fswatch triggered: $CHANGED"
  # Check again after event
  if ls "$TASKS_DIR"/*.txt >/dev/null 2>&1; then
    echo "TASK_DETECTED: Process ALL .txt files in tasks/."
    for f in "$TASKS_DIR"/*.txt; do echo "--- $(basename "$f") ---"; cat "$f"; done
    break
  fi
  # False trigger — brief pause before re-watching
  sleep 2
done
