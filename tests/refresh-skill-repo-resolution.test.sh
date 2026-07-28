#!/usr/bin/env bash
# Regression test for skills/refresh-skill.sh's repo/SKILLS_DST resolution.
#
# The script located the repo's config helper via a hardcoded checkout path:
#
#     _cfg="${SUTANDO_REPO_DIR:-$HOME/Desktop/sutando}/scripts/sutando-config.sh"
#
# On any host that keeps the repo elsewhere (e.g. ~/sutando) that guess misses, the helper is
# unreachable, and SKILLS_DST silently falls back to the PRE-migration ~/.claude/skills. Every
# refresh then targets a dead directory: refresh_one finds no symlink there, prints
# "skip <name> (not a symlink — won't clobber a local/copy install)" and exits 0 — so the skill is
# never refreshed and nothing looks wrong. `--all` is worse: it enumerates the stale directory and
# refreshes the wrong set entirely.
#
# Measured on this host before the fix: helper probed at ~/Desktop/sutando (absent) -> SKILLS_DST
# resolved to ~/.claude/skills (76 entries, stale) while the live dir was
# $CLAUDE_CONFIG_DIR/skills (85 entries). 12+ skills existed only in the live dir — including
# context-reconstruct, which the proactive loop invokes every pass.
#
# The script lives at <repo>/skills/, so its own location is an authoritative source for the repo
# root. These tests pin that, and pin the documented precedence around it:
#     SKILLS_DST env > sutando-config helper > ~/.claude/skills
#
# Observability note: the tests read the path back out of the script's own
# "(no symlinked skills under <dir>)" line, so they exercise the real resolution path rather than
# re-implementing it here.
set -eu

REPO="$(cd "$(dirname "$0")/.." && pwd -P)"
SCRIPT="$REPO/skills/refresh-skill.sh"

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

fail() { echo "FAIL: $1" >&2; exit 1; }
ok()   { echo "  ok  $1"; }

# A fake repo whose config helper reports a known skills dir.
make_fake_repo() {  # $1 = repo dir, $2 = skills dir the helper should report
  mkdir -p "$1/scripts" "$1/skills" "$2"
  cat > "$1/scripts/sutando-config.sh" <<EOF
#!/usr/bin/env bash
[ "\${1:-}" = "claude-home-path" ] && { echo "$2"; exit 0; }
echo ""
EOF
  chmod +x "$1/scripts/sutando-config.sh"
  cp "$SCRIPT" "$1/skills/refresh-skill.sh"
}

# Run --all against an empty dst so the script prints the dir it resolved.
run_all() {  # $1 = repo dir; remaining args = env assignments
  local repo="$1"; shift
  env -u SKILLS_DST -u SUTANDO_REPO_DIR HOME="$TMPDIR/home" "$@" \
    bash "$repo/skills/refresh-skill.sh" --all 2>&1
}

mkdir -p "$TMPDIR/home"

# --- 1. the regression: resolve the repo from the script's own location -----
FAKE="$TMPDIR/somewhere/my-checkout"
WANT="$TMPDIR/want-skills"
make_fake_repo "$FAKE" "$WANT"

out="$(run_all "$FAKE")"
case "$out" in
  *"$WANT"*) ok "resolves SKILLS_DST via the helper next to the script" ;;
  *) fail "expected SKILLS_DST=$WANT, got: $out" ;;
esac

case "$out" in
  *"/.claude/skills"*) fail "fell back to the stale ~/.claude/skills despite a reachable helper" ;;
  *) ok "does not fall back to ~/.claude/skills when the helper is reachable" ;;
esac

# --- 2. the hardcoded checkout path must be gone ----------------------------
if grep -vE '^\s*#' "$SCRIPT" | grep -q 'Desktop/sutando'; then
  fail "refresh-skill.sh still hardcodes ~/Desktop/sutando outside a comment"
fi
ok "no hardcoded ~/Desktop/sutando in executable code"

# --- 3. SKILLS_DST env still wins (documented precedence) -------------------
OVERRIDE="$TMPDIR/override-skills"
mkdir -p "$OVERRIDE"
out2="$(env -u SUTANDO_REPO_DIR HOME="$TMPDIR/home" SKILLS_DST="$OVERRIDE" \
        bash "$FAKE/skills/refresh-skill.sh" --all 2>&1)"
case "$out2" in
  *"$OVERRIDE"*) ok "SKILLS_DST env overrides the helper" ;;
  *) fail "SKILLS_DST env was ignored; got: $out2" ;;
esac

# --- 4. SUTANDO_REPO_DIR still wins over the self-derived path --------------
ALT="$TMPDIR/alt-repo"; ALT_SKILLS="$TMPDIR/alt-skills"
make_fake_repo "$ALT" "$ALT_SKILLS"
out3="$(env -u SKILLS_DST HOME="$TMPDIR/home" SUTANDO_REPO_DIR="$ALT" \
        bash "$FAKE/skills/refresh-skill.sh" --all 2>&1)"
case "$out3" in
  *"$ALT_SKILLS"*) ok "SUTANDO_REPO_DIR overrides the self-derived repo" ;;
  *) fail "SUTANDO_REPO_DIR was ignored; got: $out3" ;;
esac

# --- 5. no helper anywhere -> documented ~/.claude/skills fallback ----------
BARE="$TMPDIR/bare"
mkdir -p "$BARE/skills"
cp "$SCRIPT" "$BARE/skills/refresh-skill.sh"     # no scripts/sutando-config.sh beside it
out4="$(run_all "$BARE")"
case "$out4" in
  *"$TMPDIR/home/.claude/skills"*) ok "falls back to ~/.claude/skills when no helper exists" ;;
  *) fail "expected the ~/.claude/skills fallback, got: $out4" ;;
esac

echo
echo "OK — 6/6 refresh-skill repo-resolution tests passed"
