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
WORKSPACE="${SUTANDO_WORKSPACE:-${HOME}/.sutando/workspace}"
WORKSPACE="${WORKSPACE/#\~/$HOME}"
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

# Switch to main if we're not there
if [ "$start_branch" != "main" ]; then
  git checkout main 2>&1 | tail -1
fi

# Fast-forward main to upstream/main (guaranteed clean — no local commits on main)
if ! git merge --ff-only upstream/main 2>&1; then
  notify "❌ Upstream sync failed: \`git merge --ff-only upstream/main\` rejected. State unexpected — investigate manually."
  # Try to restore start branch
  [ "$start_branch" != "main" ] && git checkout "$start_branch" 2>/dev/null || true
  exit 1
fi

# Push to fork
if ! git push origin main 2>&1; then
  notify "⚠️ Upstream sync — fast-forwarded $behind commit(s) into local \`main\`, but \`git push origin main\` failed. Run manually to retry."
  [ "$start_branch" != "main" ] && git checkout "$start_branch" 2>/dev/null || true
  exit 1
fi

# Summarize what came in
summary=$(git log --oneline HEAD@{1}..HEAD 2>/dev/null | head -10)
total=$(git log --oneline HEAD@{1}..HEAD 2>/dev/null | wc -l | tr -d ' ')

# Switch back to where we started
if [ "$start_branch" != "main" ]; then
  git checkout "$start_branch" 2>&1 | tail -1
fi

notify "🔄 Upstream sync — fast-forwarded **$behind commit(s)** from \`sonichi/sutando\` onto \`amkunte/main\`.

Top changes:
\`\`\`
$summary
\`\`\`

Total subjects: $total. **Not yet on \`local-main\`** — deployment continues from old code until you run \`git rebase main\` on local-main and resolve per-commit conflicts deliberately."

echo "[upstream-sync] success: fast-forwarded main by $behind commit(s); local-main untouched"
exit 0
