#!/usr/bin/env bash
# Regression test for scripts/smoke-bundle.sh's load-error detection.
#
# smoke-bundle.sh is a CI gate (.github/workflows/bundle-smoke.yml, ubuntu-latest): it runs each
# bundled artifact under plain node and FAILS the build on a load-error signature. It used to decide
# with:
#
#     if printf '%s' "$out" | grep -qE "$LOAD_ERR"; then
#
# Under `set -o pipefail` that is a SILENT FALSE NEGATIVE once `$out` is large. `grep -q` exits 0 the
# moment it matches, closing the pipe while `printf` is still writing; `printf` dies of SIGPIPE (141);
# `pipefail` makes the PIPELINE status 141; so the `if` evaluates FALSE and the match is discarded.
#
# Net effect: a genuinely broken bundle PASSED the gate as long as it was chatty. Measured before the
# fix — identical breakage, only the output size differing:
#
#     short output (1 line)      -> rc=1, "SMOKE FAIL"   (correctly caught)
#     large output (20k lines)   -> rc=0, "start ok"     (silently shipped)
#
# This is the dangerous direction: no abort, no error message, detection simply inverts, and every
# small test fixture passes. The fix reads via a herestring so nothing can SIGPIPE.
#
# Standalone shell test — needs `node` on PATH, no running Sutando service.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
SMOKE="$REPO/scripts/smoke-bundle.sh"

if ! command -v node >/dev/null 2>&1; then
  echo "SKIP: node not on PATH"
  exit 0
fi

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

fail() { echo "FAIL: $1" >&2; exit 1; }
ok()   { echo "  ok  $1"; }

# A fixture repo the real script can run against: it cd's to its own dirname/.., iterates dist/*.js,
# and shells out to `npm run build:bundle` first — so give it a no-op build.
mkdir -p "$TMPDIR/fix/scripts" "$TMPDIR/fix/dist"
cp "$SMOKE" "$TMPDIR/fix/scripts/smoke-bundle.sh"
cat > "$TMPDIR/fix/package.json" <<'JSON'
{"name":"smoke-fixture","version":"1.0.0","scripts":{"build:bundle":"echo no-op build"}}
JSON

# Emit a bundle that fails to load, with a controllable output size. "SyntaxError" is one of the
# LOAD_ERR signatures the gate greps for.
make_bad_bundle() {  # $1 = number of output lines
  python3 - "$1" "$TMPDIR/fix/dist/artifact.js" <<'PY'
import sys
n, path = int(sys.argv[1]), sys.argv[2]
with open(path, "w") as f:
    for _ in range(n):
        f.write('console.log("SyntaxError: filler padding padding padding");\n')
    f.write("process.exit(1);\n")
PY
}

run_smoke() {
  ( cd "$TMPDIR/fix" && bash scripts/smoke-bundle.sh ) >"$TMPDIR/out" 2>&1
}

# --- 1. short output: the case that always worked ---------------------------
rm -f "$TMPDIR/fix/dist"/*.js
make_bad_bundle 1
set +e; run_smoke; rc_small=$?; set -e
[ "$rc_small" -ne 0 ] || fail "short-output broken bundle passed the gate (rc=0)"
grep -q "SMOKE FAIL" "$TMPDIR/out" || fail "short-output run did not report SMOKE FAIL"
ok "a broken bundle with short output is caught"

# --- 2. the regression: large output must ALSO be caught --------------------
# 20k lines is comfortably past the ~64 KiB pipe buffer where the old form inverted.
rm -f "$TMPDIR/fix/dist"/*.js
make_bad_bundle 20000
set +e; run_smoke; rc_large=$?; set -e
[ "$rc_large" -ne 0 ] \
  || fail "large-output broken bundle PASSED the gate (rc=0) — the SIGPIPE false negative is back"
grep -q "SMOKE FAIL" "$TMPDIR/out" || fail "large-output run did not report SMOKE FAIL"
ok "a broken bundle with large output is caught too (the regression)"

# --- 3. detection must not depend on output size ----------------------------
[ "$rc_small" = "$rc_large" ] \
  || fail "verdict depends on output size: short=$rc_small large=$rc_large"
ok "verdict is identical for short and large output"

# --- 4. a clean bundle still passes (no false positive) ---------------------
rm -f "$TMPDIR/fix/dist"/*.js
printf 'console.log("service listening");\nprocess.exit(0);\n' > "$TMPDIR/fix/dist/artifact.js"
set +e; run_smoke; rc_ok=$?; set -e
[ "$rc_ok" -eq 0 ] || fail "a clean bundle was failed by the gate (rc=$rc_ok)"
ok "a clean bundle still passes"

# --- 5. the source must not reintroduce the piped form ----------------------
# Strip comments first — the fix's own NOTE quotes the old form verbatim, and matching that would
# make this assertion fire on the very comment explaining why it exists.
if sed 's/[[:space:]]*#.*$//' "$SMOKE" | grep -qE "printf[^|]*\|[[:space:]]*grep[[:space:]]+-q"; then
  fail "smoke-bundle.sh pipes into 'grep -q' again — SIGPIPE false-negative reintroduced"
fi
ok "smoke-bundle.sh does not pipe into grep -q"

echo
echo "OK — 5/5 smoke-bundle large-output detection tests passed"
