#!/bin/bash
# Shared resolver for a Claude Code hook's transcript path. Source, don't exec.
#
# Claude Code passes transcript_path via stdin JSON ONLY — there is no
# $TRANSCRIPT_PATH env var. Two hooks need that fact (session-handoff.sh and
# archive-transcript.sh), and each carried its own copy of the parse until
# #4001; the next change to hook-payload parsing would have landed on one
# reader and missed the other silently.
#
# This owns RESOLUTION only. Each caller keeps its own policy for an empty
# result — session-handoff falls through to --latest, archive-transcript exits
# loud — so behaviour is unchanged by centralising the parse.
#
#   resolve_hook_transcript_path "$explicit"   # echoes the path, possibly empty
#
# stdin is consumed when read, so call this at most once per process.

resolve_hook_transcript_path() {
  explicit="${1:-}"
  if [ -n "$explicit" ]; then          # manual invocation wins; stdin untouched
    printf '%s' "$explicit"
    return 0
  fi
  # `[ ! -t 0 ]` is true for ANY non-tty stdin, not just a hook's JSON pipe — it
  # is equally true for an inherited-but-idle pipe (cron, nohup, a parent whose
  # stdin nobody writes to). A bare json.load() then blocks forever waiting for
  # an EOF that never comes. Poll with select() first; uses select rather than
  # `timeout(1)`, which is not present on stock macOS.
  if [ ! -t 0 ]; then
    SH_STDIN_WAIT="${SH_STDIN_WAIT:-2}" python3 -c '
import json, os, select, sys
try:
    wait = float(os.environ.get("SH_STDIN_WAIT", "2"))
except ValueError:
    wait = 2.0
if select.select([sys.stdin], [], [], wait)[0]:
    print(json.load(sys.stdin).get("transcript_path") or "")
else:
    print("")
' 2>/dev/null || true
  fi
}
