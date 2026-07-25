#!/usr/bin/env bash
#
# Upstream sync — pull new commits from sonichi/sutando into amkunte/main.
#
# Rebase-model architecture (as of 2026-05-19, PR #28):
#   amkunte/main       ← mirrors upstream/main EXACTLY (clean, contributable)
#   amkunte/local-main ← deployment branch; rebased on main when owner is ready
#
# This script handles the EASY half: fast-forward `main` from `upstream/main`
# and push. Since `main` has no local commits, ff is guaranteed conflict-free.
#
# It does NOT auto-rebase `local-main` onto `main` — that's a deliberate manual
# step the owner takes when they want to bring upstream changes into deployment
# (and handle per-commit conflicts surgically rather than 10 files at once).
#
# Exits 0 on no-op or success, 1 on failure. Notifies via $WORKSPACE/results/proactive-*
# on success (with summary) or failure. Stays silent on no-op.

set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
# Canonical resolution. Was `${SUTANDO_WORKSPACE:-${HOME}/.sutando/workspace}`:
# that env var stopped being honored in v0.8 / #1440, so on a migrated host it is
# unset and the fallback pointed at the PRE-MIGRATION directory. This script's
# only user-visible output is notify() writing
# results/proactive-upstream-sync-*.txt — so every sync notification was landing
# where neither the bridges nor the cron's own "post it to #sutando-upstream"
# step would ever look. Silent: upstream pulls would happen and never be
# reported. Flagged by scripts/lint-workspace-resolution.sh, which exits 0 and
# so never blocked it.
WORKSPACE="$(bash "$REPO_DIR/scripts/sutando-config.sh" workspace)"
cd "$REPO_DIR"

ts=$(date +%s)
_notified=0
notify() {
  local msg="$1"
  printf '%s\n' "$msg" > "${WORKSPACE}/results/proactive-upstream-sync-${ts}.txt"
  _notified=1
}

# Safety net: under `set -e` the script can exit non-zero from a command that
# isn't wrapped in its own `notify` call (e.g. a failed `git checkout main`),
# leaving the owner with no notification — a silent failure (observed
# 2026-05-31: a diverged-`main` run produced exit 1 and no DM). This EXIT trap
# guarantees that ANY non-zero exit which didn't already notify still writes a
# fallback notification. The `_notified` guard prevents double-delivery when a
# path already called notify(); it no-ops on the clean (exit 0) paths.
on_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ] && [ "$_notified" -eq 0 ]; then
    printf '%s\n' "❌ Upstream sync exited with code ${rc} and no specific message — likely a failed git operation (checkout/fetch) or a diverged \`main\`. Run \`bash skills/upstream-sync/scripts/sync.sh\` manually to see the error." \
      > "${WORKSPACE}/results/proactive-upstream-sync-${ts}.txt"
  fi
}
trap on_exit EXIT

# Remember where we started so we can switch back at the end.
start_branch=$(git rev-parse --abbrev-ref HEAD)

# Working tree must be clean — switching branches mid-edit would corrupt state.
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "[upstream-sync] working tree dirty; skipping silently"
  exit 0
fi

# Fetch upstream
if ! git fetch upstream main 2>&1; then
  notify "❌ Upstream sync failed at \`git fetch upstream main\`. Check network / remote config."
  exit 1
fi

# How many commits behind upstream/main is main?
behind=$(git rev-list --count main..upstream/main 2>/dev/null || echo 0)

if [ "$behind" = "0" ]; then
  echo "[upstream-sync] main already up to date with upstream/main"
  exit 0
fi

# Defensive check: main must have no commits that aren't on upstream/main.
# If it does, the rebase model was violated (someone committed directly to main).
ahead=$(git rev-list --count upstream/main..main 2>/dev/null || echo 0)
if [ "$ahead" != "0" ]; then
  notify "🚨 Upstream sync blocked — \`main\` has **$ahead local commit(s)** that aren't on \`upstream/main\`. The rebase model expects \`main\` to mirror \`upstream/main\` exactly. Move those commits to \`local-main\` and reset \`main\`."
  exit 1
fi

old_main=$(git rev-parse main)

# Fast-forward `main` to `upstream/main` WITHOUT touching the working tree.
#
# This used to be `checkout main` → `merge --ff-only` → `checkout back`. On this
# host $REPO_DIR is the LIVE DEPLOYMENT TREE, and that dance rewrites the working
# tree twice, which caused two problems:
#
#   1. For the duration of the sync, every file differing between `main` and
#      `local-main` was swapped to upstream content underneath running bridges —
#      the exact "never checkout in the deployment tree" hazard.
#   2. checkout rewrites files, so each of those files came back with a NEW MTIME
#      despite byte-identical content. health-check.py's mtime-based staleness
#      checks then reported voice-agent/web-client stale and sutando-app
#      rebuild-needed on EVERY sync, and --notify-discord posted the transition to
#      #health. All false — a daily false alarm that trained the owner to ignore
#      a real signal.
#
# `git fetch <remote> <src>:<dst>` updates the ref only; the working tree is never
# read or written, so neither problem can occur. It still refuses a
# non-fast-forward — exit 1 with the destination ref left unchanged — so the
# guarantee `--ff-only` provided is preserved, not traded away.
#
# git refuses to fetch into a branch that is currently checked out, so when the
# tree really is on `main` we keep the merge path. That case never had either
# problem (no branch switch happens), so nothing is lost.
if [ "$start_branch" != "main" ]; then
  if ! git fetch upstream main:main 2>&1; then
    notify "❌ Upstream sync failed: \`git fetch upstream main:main\` refused the fast-forward, so \`main\` was left unchanged. It has probably diverged from \`upstream/main\` — investigate manually."
    exit 1
  fi
else
  if ! git merge --ff-only upstream/main 2>&1; then
    notify "❌ Upstream sync failed: \`git merge --ff-only upstream/main\` rejected. State unexpected — investigate manually."
    exit 1
  fi
fi

# Push to fork
if ! git push origin main 2>&1; then
  notify "⚠️ Upstream sync — fast-forwarded $behind commit(s) into local \`main\`, but \`git push origin main\` failed. Run manually to retry."
  exit 1
fi

# Summarize what came in. Uses the $old_main snapshot rather than the previous
# `HEAD@{1}..HEAD`: HEAD no longer moves (we never check out), and a reflog-relative
# range was fragile even when it did.
summary=$(git log --oneline "$old_main..main" 2>/dev/null | head -10)
total=$(git log --oneline "$old_main..main" 2>/dev/null | wc -l | tr -d ' ')

notify "🔄 Upstream sync — fast-forwarded **$behind commit(s)** from \`sonichi/sutando\` onto \`amkunte/main\`.

Top changes:
\`\`\`
$summary
\`\`\`

Total subjects: $total. **Not yet on \`local-main\`** — deployment continues from old code until you run \`git rebase main\` on local-main and resolve per-commit conflicts deliberately."

echo "[upstream-sync] success: fast-forwarded main by $behind commit(s); local-main untouched"
exit 0
