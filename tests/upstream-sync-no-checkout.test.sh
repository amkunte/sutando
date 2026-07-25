#!/usr/bin/env bash
# Regression test for skills/upstream-sync/scripts/sync.sh.
#
# The script fast-forwards `main` from `upstream/main` inside what is, on a
# deployed host, the LIVE checkout. It used to do that with
# `checkout main` → `merge --ff-only` → `checkout back`, which rewrites the
# working tree twice. Two consequences:
#
#   1. Files differing between `main` and the deployment branch were briefly
#      swapped to upstream content under running services.
#   2. Those files came back with a fresh mtime despite identical content, so
#      health-check.py's mtime-based staleness checks fired on every sync
#      (voice-agent stale / web-client stale / sutando-app rebuild-needed) and
#      --notify-discord posted the false transition to #health.
#
# The fix uses `git fetch upstream main:main`, which moves the ref without
# touching the working tree. These tests pin the observable properties:
# mtimes preserved, content preserved, branch never left, ff still applied,
# and a non-fast-forward still refused.
#
# Standalone shell test — no node, no running Sutando service.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
SYNC_SRC="$REPO/skills/upstream-sync/scripts/sync.sh"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

fail() { echo "FAIL: $1" >&2; exit 1; }
ok()   { echo "  ok  $1"; }

git_q() { git -c user.email=t@example.com -c user.name=test "$@"; }

# Build: bare origin + bare upstream + a fork checkout sitting on `local-main`,
# with a file whose content differs between `main` and `local-main` (that
# difference is what makes checkout rewrite — and thus re-stamp — the file).
setup_repo() {
  rm -rf "$TMPDIR/up" "$TMPDIR/origin" "$TMPDIR/fork" "$TMPDIR/ws"
  mkdir -p "$TMPDIR/ws/results"

  git_q init -q --bare -b main "$TMPDIR/up"
  git_q init -q --bare -b main "$TMPDIR/origin"

  git_q init -q -b main "$TMPDIR/seed"
  ( cd "$TMPDIR/seed"
    echo "upstream-v1" > shared.txt
    mkdir -p scripts
    # sync.sh resolves its workspace through this helper.
    printf '#!/usr/bin/env bash\necho "%s"\n' "$TMPDIR/ws" > scripts/sutando-config.sh
    git_q add -A && git_q commit -qm "c1"
    git_q push -q "$TMPDIR/up" main && git_q push -q "$TMPDIR/origin" main )
  rm -rf "$TMPDIR/seed"

  git_q clone -q "$TMPDIR/origin" "$TMPDIR/fork"
  ( cd "$TMPDIR/fork"
    git_q remote add upstream "$TMPDIR/up"
    git_q checkout -qb local-main
    echo "deployment-local-content" > shared.txt   # differs from main
    git_q commit -qam "local deployment commit" )

  # A new upstream commit for the sync to pull.
  git_q clone -q "$TMPDIR/up" "$TMPDIR/upwork"
  ( cd "$TMPDIR/upwork"
    echo "upstream-v2" > shared.txt
    git_q commit -qam "c2 upstream"
    git_q push -q origin main )
  rm -rf "$TMPDIR/upwork"
}

run_sync() {
  ( cd "$TMPDIR/fork" && REPO_DIR="$TMPDIR/fork" bash "$SYNC_SRC" ) >"$TMPDIR/out" 2>&1
}

# --- 1. the regression: a real fast-forward must not re-stamp the tree --------
setup_repo
before_mtime="$(stat -f %m "$TMPDIR/fork/shared.txt")"
before_sum="$(cat "$TMPDIR/fork/shared.txt")"
sleep 2   # coarse enough that any rewrite is visible in a 1-second-resolution mtime

set +e; run_sync; rc=$?; set -e
[ "$rc" -eq 0 ] || fail "sync exited $rc; output: $(cat "$TMPDIR/out")"
ok "sync succeeds on a real fast-forward"

after_mtime="$(stat -f %m "$TMPDIR/fork/shared.txt")"
[ "$before_mtime" = "$after_mtime" ] || \
  fail "working-tree mtime changed ($before_mtime -> $after_mtime) — the checkout dance is back, health-check will false-alarm"
ok "working-tree mtime is preserved across a sync"

[ "$(cat "$TMPDIR/fork/shared.txt")" = "$before_sum" ] || fail "working-tree content changed"
ok "working-tree content is preserved across a sync"

branch="$(cd "$TMPDIR/fork" && git rev-parse --abbrev-ref HEAD)"
[ "$branch" = "local-main" ] || fail "left the deployment branch: now on '$branch'"
ok "never leaves the deployment branch"

# The point of the script still has to happen.
m="$(cd "$TMPDIR/fork" && git rev-parse main)"
u="$(cd "$TMPDIR/fork" && git rev-parse upstream/main)"
[ "$m" = "$u" ] || fail "main was not fast-forwarded to upstream/main"
ok "main is fast-forwarded to upstream/main"

o="$(git_q --git-dir="$TMPDIR/origin" rev-parse main)"
[ "$o" = "$u" ] || fail "origin/main was not pushed ($o != $u)"
ok "the fast-forward is pushed to origin"

grep -q "fast-forwarded" "$TMPDIR/ws/results"/proactive-upstream-sync-*.txt 2>/dev/null || \
  fail "no success notification written to the workspace results dir"
ok "writes a success notification naming the fast-forward"

# --- 2. a diverged main must still be refused, ref left untouched -------------
setup_repo
( cd "$TMPDIR/fork"
  git_q checkout -q main
  echo diverged > diverged.txt
  git_q add -A && git_q commit -qm "local commit on main"
  git_q checkout -q local-main )
main_before="$(cd "$TMPDIR/fork" && git rev-parse main)"

set +e; run_sync; rc=$?; set -e
[ "$rc" -ne 0 ] || fail "diverged main: expected non-zero exit, got 0"
ok "diverged main is refused (non-zero exit)"

main_after="$(cd "$TMPDIR/fork" && git rev-parse main)"
[ "$main_before" = "$main_after" ] || fail "diverged main: ref was modified despite refusal"
ok "diverged main: the ref is left unchanged"

ls "$TMPDIR/ws/results"/proactive-upstream-sync-*.txt >/dev/null 2>&1 || \
  fail "diverged main: failure was not notified"
ok "diverged main: failure is notified, not silent"

# --- 3. already-current is a silent no-op ------------------------------------
setup_repo
( cd "$TMPDIR/fork" && git_q fetch -q upstream main:main )
before_mtime="$(stat -f %m "$TMPDIR/fork/shared.txt")"
sleep 2
set +e; run_sync; rc=$?; set -e
[ "$rc" -eq 0 ] || fail "up-to-date: expected exit 0, got $rc"
[ "$(stat -f %m "$TMPDIR/fork/shared.txt")" = "$before_mtime" ] || fail "up-to-date: tree was re-stamped"
ls "$TMPDIR/ws/results"/proactive-upstream-sync-*.txt >/dev/null 2>&1 && \
  fail "up-to-date: should stay silent but wrote a notification"
ok "already-current run is a silent, tree-preserving no-op"

echo
echo "OK — 11/11 upstream-sync tests passed"
