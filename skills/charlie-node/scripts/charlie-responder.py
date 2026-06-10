#!/usr/bin/env python3
"""Charlie responder — the task-processing core for the Charlie community node.

Charlie's discord-bridge writes a task file per @mention it receives in the
community server. This daemon consumes those tasks and produces replies, then
writes them to results/ for the bridge to deliver.

SAFETY MODEL (matches the bridge's "other"-tier spec):
  - Engine is `gemini --skip-trust --approval-mode plan` (read-only / plan mode)
    run with cwd=/tmp → HARD ISOLATION: no repo access, no file mutation, no shell
    side effects. Charlie answers from the model's general knowledge only.
  - Answer-only. Any message that reads as an action request (send/commit/push/
    merge/deploy/modify/buy/…) gets a canned refusal, never executed.
  - Rate-limited (bounds quota/cost + abuse from a public server).
  - Mention-gating is already enforced upstream by the bridge (non-owner tasks
    are only written for explicit @mentions), so this daemon only ever sees
    messages that deliberately summoned Charlie.

The repo-aware / PR-review / code-writing tier is intentionally NOT here — that
needs the scoped GitHub identity + trusted-contributor tier mapping (phase 2).

Run via: bash skills/charlie-node/scripts/launch-charlie-responder.sh
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

# --- Hard-set Charlie's workspace (do NOT inherit a personal SUTANDO_WORKSPACE;
#     same env-leak lesson as the launcher). Override only via CHARLIE_WORKSPACE.
WS = Path(os.environ.get("CHARLIE_WORKSPACE", str(Path.home() / ".sutando" / "workspace-charlie")))
TASKS_DIR = WS / "tasks"
RESULTS_DIR = WS / "results"
ARCHIVE_DIR = WS / "tasks" / "archive"
LOG_FILE = WS / "logs" / "responder.log"
LOCK_FILE = WS / "state" / "locks" / "charlie-responder.lock"

POLL_SECONDS = 3
RATE_MAX_PER_MIN = 6           # cap gemini calls / rolling minute
GEMINI_TIMEOUT_S = 120         # cold-start can be ~60-90s
REPLY_CHAR_CAP = 1500

# Messages that ask Charlie to DO something get a clean canned refusal. This is a
# CONVENIENCE filter, not the safety boundary — gemini runs read-only/plan from
# /tmp and cannot act regardless. So keep it CONSERVATIVE: only fire on an explicit
# imperative (bare at the start, or after a "please/can you/charlie," lead-in)
# directly followed by a high-signal mutation verb. Anything ambiguous falls
# through to gemini, which is instructed to refuse genuine action requests itself.
ACTION_RE = re.compile(
    r"(?:^|\b(?:please|can you|could you|would you|will you|go ahead and|"
    r"i need you to|charlie[,:]?)\s+)(?:to\s+)?"
    r"(push|commit|merge|deploy|delete|remove|overwrite|revert|force[-\s]?push|"
    r"open\s+(?:a\s+)?pr|create\s+(?:a\s+)?(?:pr|branch)|"
    r"send\s+(?:an?\s+)?(?:email|message|dm|reply)|transfer|wire|pay|purchase|buy|"
    r"place\s+(?:an\s+)?order|install|uninstall|execute)\b",
    re.IGNORECASE,
)
REFUSAL = ("I can only answer general questions right now — action requests "
           "(code changes, sending messages, PRs, etc.) need the owner.")

# gemini emits a few non-answer warning lines we strip from the reply.
NOISE_PREFIXES = (
    "Ripgrep is not available",
    "Approval mode overridden",
    "Gemini CLI is not running in a trusted",
    "Loaded cached credentials",
    "Data collection is",
)

CHARLIE_FRAMING = (
    "You are Charlie, a read-only assistant participating in the public Sutando "
    "open-source community Discord (the project by sonichi on GitHub). A community "
    "member mentioned you. Answer their message factually, helpfully, and concisely "
    "from general knowledge. You CANNOT take any action — no code changes, no file "
    "access, no sending messages, no PRs. You only provide information. Keep the "
    "reply under ~1200 characters and conversational. Do not include setup notes or "
    "meta-commentary. The member said:\n\n"
)


def log(msg: str) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%Y-%m-%dT%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def acquire_lock() -> bool:
    """Single-instance guard. Returns False if another responder holds the lock."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    global _lock_fp
    import fcntl
    _lock_fp = open(LOCK_FILE, "w")
    try:
        fcntl.flock(_lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fp.write(str(os.getpid()))
        _lock_fp.flush()
        return True
    except OSError:
        return False


def parse_task(path: Path) -> dict:
    """Pull id, access_tier, and the user's message out of a bridge task file.

    The user's message is the `task:` value with the bridge's `[Discord @user] `
    prefix stripped. The injected `===SUTANDO SYSTEM INSTRUCTIONS===` block lives
    on its own lines and is ignored (we run our own gemini path, not codex)."""
    fields = {"id": path.stem, "access_tier": "other", "message": ""}
    try:
        text = path.read_text()
    except OSError:
        return fields
    for line in text.splitlines():
        if line.startswith("id:"):
            fields["id"] = line.split(":", 1)[1].strip() or path.stem
        elif line.startswith("access_tier:"):
            fields["access_tier"] = line.split(":", 1)[1].strip() or "other"
        elif line.startswith("task:"):
            msg = line.split(":", 1)[1].strip()
            # strip leading "[Discord @user] " / "[<surface> @user] " prefix
            msg = re.sub(r"^\[[^\]]*\]\s*", "", msg)
            fields["message"] = msg
    return fields


def clean_gemini_output(raw: str) -> str:
    kept = []
    for line in raw.splitlines():
        s = line.strip()
        if not s:
            kept.append(line)
            continue
        if any(s.startswith(p) for p in NOISE_PREFIXES):
            continue
        kept.append(line)
    out = "\n".join(kept).strip()
    if len(out) > REPLY_CHAR_CAP:
        out = out[:REPLY_CHAR_CAP].rsplit(" ", 1)[0] + " …"
    return out


def run_gemini(message: str) -> str | None:
    """Run gemini hard-isolated from /tmp. Returns the cleaned reply, or None."""
    prompt = CHARLIE_FRAMING + message
    try:
        proc = subprocess.run(
            ["gemini", "--skip-trust", "--approval-mode", "plan", "-p", prompt],
            cwd="/tmp",
            capture_output=True,
            text=True,
            timeout=GEMINI_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        log(f"gemini failed: {e}")
        return None
    if proc.returncode != 0:
        log(f"gemini non-zero exit {proc.returncode}: {proc.stderr[:200]}")
        return None
    return clean_gemini_output(proc.stdout)


def archive(path: Path) -> None:
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    try:
        path.rename(ARCHIVE_DIR / path.name)
    except OSError:
        path.unlink(missing_ok=True)


def write_result(task_id: str, body: str) -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"task-{task_id}.txt").write_text(body)


def process(path: Path) -> None:
    t = parse_task(path)
    tid, msg = t["id"], t["message"]
    # task ids look like task-<ts>; result file keys off the bare id
    rid = tid[len("task-"):] if tid.startswith("task-") else tid

    if not msg or len(re.sub(r"[^\w]", "", msg)) < 2:
        log(f"{tid}: empty/noise — archived, no reply")
        archive(path)
        return

    if ACTION_RE.search(msg):
        log(f"{tid}: action-request refused — {msg[:80]!r}")
        write_result(rid, REFUSAL)
        archive(path)
        return

    log(f"{tid}: answering ({t['access_tier']}) — {msg[:80]!r}")
    reply = run_gemini(msg)
    if reply:
        write_result(rid, reply)
        log(f"{tid}: replied ({len(reply)} chars)")
    else:
        # Stay silent on engine failure rather than emit a broken reply.
        log(f"{tid}: engine failure — no reply written")
    archive(path)


def main() -> None:
    if not acquire_lock():
        log("another charlie-responder holds the lock — exiting cleanly.")
        sys.exit(0)
    for d in (TASKS_DIR, RESULTS_DIR, ARCHIVE_DIR, LOG_FILE.parent):
        d.mkdir(parents=True, exist_ok=True)
    log(f"charlie-responder up — workspace {WS}, engine gemini (read-only/plan, cwd=/tmp)")

    call_times: list[float] = []
    while True:
        try:
            tasks = sorted(TASKS_DIR.glob("task-*.txt"), key=lambda p: p.stat().st_mtime)
        except OSError:
            tasks = []
        for path in tasks:
            if not path.is_file():
                continue
            now = time.time()
            call_times[:] = [t for t in call_times if now - t < 60]
            if len(call_times) >= RATE_MAX_PER_MIN:
                log("rate cap reached this minute — deferring remaining tasks")
                break
            call_times.append(now)
            try:
                process(path)
            except Exception as e:  # never let one bad task kill the daemon
                log(f"{path.name}: unhandled error {e!r}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
