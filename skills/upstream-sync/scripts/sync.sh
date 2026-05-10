#!/usr/bin/env bash
#
# Upstream sync — pull new commits from sonichi/sutando into amkunte/sutando main.
# See SKILL.md for full behavior. Exits 0 on no-op or success, 1 on failure
# (working tree dirty, conflicts, git error). On any failure or on a successful
# pull, writes results/proactive-{ts}.txt for Telegram notification.
#
# Designed to be run from the repo root (cron sets CWD).

set -euo pipefail

REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)}"
cd "$REPO_DIR"

ts=$(date +%s)
notify() {
  # $1 = message (markdown OK)
  local msg="$1"
  printf '%s\n' "$msg" > "results/proactive-upstream-sync-${ts}.txt"
}

# Guard: must be on main
current_branch=$(git rev-parse --abbrev-ref HEAD)
if [ "$current_branch" != "main" ]; then
  echo "[upstream-sync] not on main (currently $current_branch); skipping"
  notify "⚠️ Upstream sync skipped — repo is on branch \`$current_branch\`, not \`main\`. Switch to main and run \`bash skills/upstream-sync/scripts/sync.sh\` manually."
  exit 1
fi

# Guard: working tree clean (no uncommitted changes to tracked files).
# If dirty, skip silently — the repo accumulates long-running uncommitted
# state (telegram-bridge.py, watch-tasks.sh, etc.) that owner is aware of
# and is fine with. Notifying every day would be noise. Cron stderr still
# captures the skip for forensics if needed.
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "[upstream-sync] working tree dirty; skipping silently"
  exit 0
fi

# Fetch upstream
if ! git fetch upstream main 2>&1; then
  notify "❌ Upstream sync failed at \`git fetch upstream main\`. Check network / remote config."
  exit 1
fi

# How many commits behind upstream/main?
behind=$(git rev-list --count main..upstream/main 2>/dev/null || echo 0)
ahead=$(git rev-list --count upstream/main..main 2>/dev/null || echo 0)

if [ "$behind" = "0" ]; then
  echo "[upstream-sync] up to date (ahead=$ahead, behind=0)"
  exit 0
fi

# Try the merge. Use --ff-only first so a clean ff lands without a merge commit.
# If non-ff (we have local commits not yet upstreamed), fall back to a merge.
if git merge --ff-only upstream/main 2>/dev/null; then
  echo "[upstream-sync] fast-forwarded $behind commit(s)"
else
  msg="Merge upstream/main ($behind commit$([ "$behind" = "1" ] || echo s))"
  if ! git merge --no-ff -m "$msg" upstream/main 2>&1; then
    # Conflict — abort and notify
    git merge --abort 2>/dev/null || true
    summary=$(git log --oneline main..upstream/main | head -10)
    notify "🚨 Upstream sync — **conflict** merging \`upstream/main\` into local \`main\`. Auto-merge aborted; \`main\` is unchanged.

Behind by **$behind commit(s)**, ahead by **$ahead**. Resolve manually:
\`\`\`bash
cd ~/sutando
git fetch upstream
git merge upstream/main         # then resolve conflicts
git push origin main
\`\`\`

Top of upstream changes:
\`\`\`
$summary
\`\`\`"
    exit 1
  fi
fi

# Push to fork
if ! git push origin main 2>&1; then
  notify "⚠️ Upstream sync — merged $behind commit(s) into local \`main\`, but \`git push origin main\` failed. Run manually to retry."
  exit 1
fi

# Success — summarize what came in
summary=$(git log --oneline HEAD@{1}..HEAD | head -10)
total_subjects=$(git log --oneline HEAD@{1}..HEAD | wc -l | tr -d ' ')

notify "🔄 Upstream sync — pulled **$behind commit(s)** from \`sonichi/sutando\` into your fork's \`main\` and pushed.

Top changes:
\`\`\`
$summary
\`\`\`

Total subjects: $total_subjects. \`/\`, \`/chat\`, \`/paidsubscriptions\` not affected — restart any service you'd like to re-load fresh code (or just leave them; tsx hot-reloads on next request)."

echo "[upstream-sync] success: pulled $behind commit(s)"
exit 0
