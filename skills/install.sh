#!/bin/bash
# Install Sutando skills into Claude Code ($CLAUDE_CONFIG_DIR/skills/).
# Creates symlinks so updates to the repo are picked up automatically.
# Resolves the target via the M0 claude-home-path helper so claude-sutando
# users get their workspace-scoped CCD honored.

set -e

SKILLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$(bash "$(cd "$SKILLS_DIR/.." && pwd)/scripts/sutando-config.sh" claude-home-path skills)"

mkdir -p "$TARGET"

# Prune symlinks whose source skill no longer exists.
#
# The loop below only ever ADDS: it iterates skills/*/, so a skill deleted
# upstream leaves its symlink behind pointing at nothing, forever. Those dead
# links accumulate across merges and keep health-check's skill-symlinks check
# permanently in `warn` — which is worse than cosmetic, because a check that
# always warns can no longer signal a NEW breakage (a skill that should load
# and doesn't). Found 2026-07-23 on Goose: ag2-relay, catchup-after-startup and
# discord-voice were all deleted upstream (#1770, #1737, #1720) and had been
# warning ever since.
#
# Scoped deliberately narrowly — a link is removed only when it is (a) a
# symlink, (b) dangling, AND (c) pointing INTO this repo's skills/ dir. That
# third condition is what makes it safe: community or externally-installed
# skills living elsewhere are never touched, even if they happen to be broken,
# because they are not ours to manage.
for link in "$TARGET"/*; do
  [ -L "$link" ] || continue
  [ -e "$link" ] && continue
  case "$(readlink "$link")" in
    "$SKILLS_DIR"/*) rm "$link"; echo "  ✗ $(basename "$link") (pruned — source skill no longer in this repo)" ;;
  esac
done

for skill_dir in "$SKILLS_DIR"/*/; do
  skill_name=$(basename "$skill_dir")
  [ "$skill_name" = "install.sh" ] && continue
  [ ! -f "$skill_dir/SKILL.md" ] && continue

  if [ -L "$TARGET/$skill_name" ] && [ ! -e "$TARGET/$skill_name" ]; then
    rm "$TARGET/$skill_name"
    ln -s "$skill_dir" "$TARGET/$skill_name"
    echo "  ✓ $skill_name (relinked — old symlink was broken)"
  elif [ -L "$TARGET/$skill_name" ]; then
    echo "  ↻ $skill_name (symlink exists)"
  elif [ -d "$TARGET/$skill_name" ]; then
    echo "  ⚠ $skill_name (directory exists, skipping — remove manually to reinstall)"
  else
    ln -s "$skill_dir" "$TARGET/$skill_name"
    echo "  ✓ $skill_name"
  fi
done

echo ""
echo "Installed. Skills available in any Claude Code session."
