#!/bin/bash
# refresh-skill.sh — make an UPDATED symlinked skill live in the CURRENT Claude Code
# session WITHOUT restarting it.
#
# WHY: Claude Code's skill live-watcher picks up skill add/edit/remove "within the
# current session without restarting" — BUT it does NOT follow symlinks (verified
# 2026-05-07; memory feedback_skill_watcher_no_symlink_follow). Our skills are symlinks
# into the synced repo, so `git pull` updates the file *behind* the symlink and the
# watcher never gets an fs-event → the running session keeps the old skill.
#
# THE FIX (cp-then-swap): briefly replace the symlink with a REAL directory under the
# watched path — that fires the fs-event the watcher needs, so it re-reads the skill
# mid-session — then restore the clean symlink (registration persists by skill name).
# Only the skill DEFINITION needs to be present for detection, so heavy/transient dirs
# (workspace, node_modules, generated, render scratch) are excluded — copying them is
# slow and can choke on dangling symlinks (the make-wire-episode foot-gun).
#
# Usage:
#   bash skills/refresh-skill.sh <name> [<name> ...]   # refresh specific skills
#   bash skills/refresh-skill.sh --all                 # every symlinked skill
set -uo pipefail   # NOT -e: one skill's hiccup must not strand a half-swapped symlink

# Resolve the live skills dir. Pre-revamp: ~/.claude/skills. The workspace-revamp
# relocates it under claude-home, resolved by the main repo's config helper.
# Precedence: SKILLS_DST env > sutando-config helper (if reachable) > ~/.claude/skills.
# The repo is derived from THIS SCRIPT'S OWN LOCATION (it lives at <repo>/skills/),
# not guessed. It used to read `${SUTANDO_REPO_DIR:-$HOME/Desktop/sutando}` — a
# hardcoded checkout path that is wrong on any host that keeps the repo elsewhere
# (e.g. ~/sutando). When that guess misses, the helper is unreachable, SKILLS_DST
# silently falls back to the PRE-migration ~/.claude/skills, and every refresh
# targets a dead directory: `refresh_one` finds no symlink there and prints
# "skip <name> (not a symlink ...)" while exiting 0, so the skill is never
# refreshed and nothing looks wrong. `--all` is worse — it enumerates the stale
# directory and refreshes the wrong set entirely.
_self_repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
SKILLS_DST="${SKILLS_DST:-}"
if [ -z "$SKILLS_DST" ]; then
  _cfg="${SUTANDO_REPO_DIR:-$_self_repo}/scripts/sutando-config.sh"
  [ -x "$_cfg" ] && SKILLS_DST="$(bash "$_cfg" claude-home-path skills 2>/dev/null || true)"
  SKILLS_DST="${SKILLS_DST:-$HOME/.claude/skills}"
fi
SETTLE_S="${REFRESH_SKILL_SETTLE_S:-1}"
EXCLUDES=(--exclude='workspace' --exclude='node_modules' --exclude='generated'
          --exclude='__pycache__' --exclude='.git' --exclude='*.mp4' --exclude='*.png'
          --exclude='*.wav' --exclude='.venv')

refresh_one() {
  local name="$1"
  local link="$SKILLS_DST/$name"
  if [ ! -L "$link" ]; then
    echo "  skip $name (not a symlink — won't clobber a local/copy install)"; return 0
  fi
  local target; target="$(readlink "$link")"
  if [ ! -d "$target" ]; then
    echo "  skip $name (symlink target missing: $target)"; return 0
  fi
  # cp-then-swap, but ALWAYS restore the symlink even if the copy step errors.
  rm -f "$link"
  rsync -a "${EXCLUDES[@]}" "$target"/ "$link"/ 2>/dev/null || cp -R "$target" "$link" 2>/dev/null
  sync; sleep "$SETTLE_S"
  rm -rf "$link"
  ln -s "$target" "$link"
  if [ -L "$link" ]; then echo "  refreshed $name"; else echo "  ERROR restoring $name symlink!"; fi
}

main() {
  mkdir -p "$SKILLS_DST"
  [ "$#" -ge 1 ] || { echo "usage: refresh-skill.sh <name> [<name> ...] | --all" >&2; exit 2; }
  if [ "$1" = "--all" ]; then
    local any=0
    for link in "$SKILLS_DST"/*; do
      [ -L "$link" ] || continue
      refresh_one "$(basename "$link")"; any=1
    done
    [ "$any" = 1 ] || echo "  (no symlinked skills under $SKILLS_DST)"
  else
    for name in "$@"; do refresh_one "$name"; done
  fi
  echo "done — updated skills are live in the running session (no restart)."
}
main "$@"
