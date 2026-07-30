#!/usr/bin/env bash
# Regression test for skills/upstream-sync/scripts/sync.sh's summary line.
#
# The script runs under `set -euo pipefail`. It used to build its summary with:
#
#     summary=$(git log --oneline HEAD@{1}..HEAD 2>/dev/null | head -10)
#
# `head -10` closes the pipe after 10 lines. If `git log` is still writing — which happens once the
# range is big enough to exceed the pipe buffer — it dies with SIGPIPE (exit 141). `pipefail`
# propagates that, `set -e` aborts the script AT THAT LINE, skipping both the branch-restore that
# follows and the notify() after it.
#
# Observed 2026-07-25: a 345-commit fast-forward aborted here and left the LIVE deployment tree
# checked out on `main`, with no success notification. Measured: 3 commits -> ok, 11 -> ok,
# 30/100/200/345 -> exit 141. Routine syncs pass, which is why it survived review for months.
#
# These tests pin the property directly: building the summary over a LARGE range must not abort the
# shell, and must still yield at most 10 lines.
#
# Companion to the no-checkout fix in this same PR: that one stops the abort from stranding the
# tree, this one stops the abort happening at all.
#
# Standalone shell test — no node, no running Sutando service.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
SYNC="$REPO/skills/upstream-sync/scripts/sync.sh"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

fail() { echo "FAIL: $1" >&2; exit 1; }
ok()   { echo "  ok  $1"; }

git_q() { git -c user.email=t@example.com -c user.name=test "$@"; }

# --- build a repo with a deliberately large history -------------------------
REPO_T="$TMPDIR/r"
mkdir -p "$REPO_T"
git_q init -q -b main "$REPO_T"
(
  cd "$REPO_T"
  # 60 commits: comfortably past the measured ~11-30 abort boundary, fast to create.
  for i in $(seq 1 60); do
    echo "$i" > f.txt
    git_q add f.txt
    git_q commit -qm "commit $i"
  done
)
BASE="$(git_q -C "$REPO_T" rev-list --max-parents=0 HEAD)"
TIP="$(git_q -C "$REPO_T" rev-parse HEAD)"

# --- 1. the regression: `| head -10` aborts under pipefail -------------------
set +e
bash -c "set -euo pipefail; cd '$REPO_T'; s=\$(git log --oneline $BASE..$TIP 2>/dev/null | head -10); exit 0" >/dev/null 2>&1
old_rc=$?
set -e
[ "$old_rc" -eq 141 ] || echo "  note: legacy form returned $old_rc (expected 141) — buffer size differs on this host"

# --- 2. the fix: `git log -10` must NOT abort --------------------------------
set +e
out="$(bash -c "set -euo pipefail; cd '$REPO_T'; git log --oneline -10 $BASE..$TIP 2>/dev/null")"
new_rc=$?
set -e
[ "$new_rc" -eq 0 ] || fail "fixed form aborted with $new_rc over a 60-commit range"
ok "summary over a large range does not abort the shell"

lines="$(printf '%s\n' "$out" | grep -c . || true)"
[ "$lines" -eq 10 ] || fail "expected 10 summary lines, got $lines"
ok "summary is still capped at 10 lines"

# --- 3. the shipped script must not reintroduce the pipe ---------------------
if grep -qE 'summary=.*git log.*\|[[:space:]]*head' "$SYNC"; then
  fail "sync.sh builds its summary via '| head' again — SIGPIPE regression reintroduced"
fi
ok "sync.sh does not pipe its summary through head"

grep -qE 'summary=\$\(git log --oneline -10' "$SYNC" \
  || fail "sync.sh no longer uses 'git log --oneline -10' for the summary"
ok "sync.sh uses git's own -10 limit"

# --- 4. the `| wc -l` total is legitimately safe and should stay -------------
grep -qE 'total=\$\(git log --oneline .*\| wc -l' "$SYNC" \
  || fail "the total= line changed; wc -l reads to EOF and is SIGPIPE-safe, it should remain"
ok "total= still uses wc -l (safe: wc never closes the pipe early)"

echo
echo "OK — 4/4 upstream-sync summary SIGPIPE tests passed"
