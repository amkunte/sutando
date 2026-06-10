#!/usr/bin/env python3
"""Charlie owner-tier capability: read a thread/channel in the community server and
summarize it.

Triggered when an OWNER-tier message asks Charlie to go read + report on a
discussion (e.g. "@Charlie go read the workspace-concept discussion in Chi's
server and summarize for Maverick"). Charlie:
  1. resolves the fuzzy topic → a real channel/thread in the community guild,
  2. fetches that channel/thread's recent messages via Charlie's Discord token
     (READ-ONLY — Discord REST GET only),
  3. summarizes / extracts via gemini (read-only/plan, cwd=/tmp).

The reply is returned to the responder, which writes it to results/ → Charlie's
bridge delivers it to the origin channel (when asked from #bot2bot, that's where
Maverick sees it). Owner-gated: only callers Charlie treats as owner reach here.

This is READ-ONLY end to end: Discord GET + a sandboxed LLM summary. It never
posts, edits, or mutates anything on its own.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

# The community server Charlie reads from (Chi's "Sutando" master server).
# Overridable so the same code works if the community guild changes.
COMMUNITY_GUILD_ID = os.environ.get("CHARLIE_COMMUNITY_GUILD_ID", "1485653766404444352")

API = "https://discord.com/api/v10"
FETCH_LIMIT = 50           # messages pulled from the resolved channel/thread
GEMINI_TIMEOUT_S = 120

# Intent detection: an owner asking Charlie to go read/recap a discussion.
READ_INTENT_RE = re.compile(
    r"\b(read|summar|recap|catch\s*me?\s*up|catch\s*up|what(?:'s| is| are)?\s+"
    r"(?:they|people|folks)?\s*(?:saying|discussing)|digest|brief\s+me|tl;?dr)\b",
    re.IGNORECASE,
)
TARGET_HINT_RE = re.compile(
    r"\b(thread|discussion|channel|conversation|chat|chi'?s?\s+server|community)\b",
    re.IGNORECASE,
)

# Output mode the owner asked for.
VERBATIM_RE = re.compile(r"\b(verbatim|exact|word.for.word|raw|quote)\b", re.IGNORECASE)
OPINION_RE = re.compile(r"\b(opinion|your\s+take|what\s+do\s+you\s+think|assess)\b", re.IGNORECASE)


def is_read_command(message: str) -> bool:
    """True iff this owner message reads as 'go read/recap a discussion'."""
    return bool(READ_INTENT_RE.search(message) and TARGET_HINT_RE.search(message))


def _token() -> str:
    cdir = Path.home() / ".claude" / "channels" / "discord-charlie"
    for line in (cdir / ".env").read_text().splitlines():
        if line.startswith("DISCORD_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("Charlie token not found")


def _api_get(path: str) -> object:
    req = urllib.request.Request(
        f"{API}{path}",
        headers={"Authorization": f"Bot {_token()}", "User-Agent": "charlie-node/1.0"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read())


def extract_query(message: str) -> str:
    """Strip command/filler words to leave the topic to match on."""
    q = message
    q = re.sub(r"<@!?\d+>", "", q)                       # mention tokens
    q = re.sub(r"\b(hey|hi|charlie|please|can you|could you|go|and|then|"
               r"the|a|an|about|on|in|of|for|me|us|to|read|summar\w*|recap|"
               r"give|maverick|chi'?s?|server|community|discussion|thread|"
               r"channel|conversation|so\s+we\s+can\s+decide.*|verbatim|opinion)\b",
               " ", q, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", q).strip()


def list_targets() -> list[dict]:
    """All readable text channels + active threads in the community guild."""
    out = []
    try:
        chans = _api_get(f"/guilds/{COMMUNITY_GUILD_ID}/channels")
        for c in chans:
            if c.get("type") in (0, 5, 15):  # text, announcement, forum
                out.append({"id": c["id"], "name": c.get("name", ""),
                            "topic": c.get("topic") or "", "kind": "channel"})
    except urllib.error.HTTPError:
        pass
    try:
        active = _api_get(f"/guilds/{COMMUNITY_GUILD_ID}/threads/active")
        for t in active.get("threads", []):
            out.append({"id": t["id"], "name": t.get("name", ""),
                        "topic": "", "kind": "thread"})
    except urllib.error.HTTPError:
        pass
    return out


def resolve_target(query: str, targets: list[dict]) -> dict:
    """Pick the best channel/thread for the query.

    Returns {"match": target} for a confident hit, {"candidates": [...]} when
    ambiguous, or {"none": True} when nothing scores. Keyword scoring first
    (deterministic); gemini tie-breaks when keyword scoring is unclear."""
    words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 2]
    scored = []
    for t in targets:
        hay = (t["name"] + " " + t["topic"]).lower()
        # Weight each matched word by its length — a hit on a distinctive term
        # ("workspace") outweighs a hit on a common one ("new"), so the specific
        # channel wins cleanly instead of tying into a disambiguation prompt.
        score = sum(len(w) for w in words if w in hay)
        if score:
            scored.append((score, t))
    scored.sort(key=lambda x: x[0], reverse=True)

    if not scored:
        # keyword miss — let gemini pick from names (robust to paraphrase)
        return _gemini_pick(query, targets)
    top = scored[0][0]
    leaders = [t for s, t in scored if s == top]
    if len(leaders) == 1:
        return {"match": leaders[0]}
    return {"candidates": leaders[:5]}


def _gemini_pick(query: str, targets: list[dict]) -> dict:
    if not targets:
        return {"none": True}
    listing = "\n".join(f"{i}. {t['name']}" for i, t in enumerate(targets))
    prompt = (f"A user wants the channel/thread about: \"{query}\".\n"
              f"Here is the list:\n{listing}\n\n"
              "Reply with ONLY the single best matching number, or -1 if none clearly match.")
    raw = _gemini(prompt)
    m = re.search(r"-?\d+", raw or "")
    if not m:
        return {"none": True}
    idx = int(m.group())
    if 0 <= idx < len(targets):
        return {"match": targets[idx]}
    return {"none": True}


class NoAccess(Exception):
    """Charlie's role can't View / Read History on the target channel."""


def fetch_transcript(channel_id: str, limit: int = FETCH_LIMIT) -> str:
    try:
        msgs = _api_get(f"/channels/{channel_id}/messages?limit={limit}")
    except urllib.error.HTTPError as e:
        if e.code in (403, 401):
            raise NoAccess from e
        raise
    if not isinstance(msgs, list):
        return ""
    lines = []
    for m in reversed(msgs):  # oldest → newest
        author = (m.get("author") or {}).get("username", "unknown")
        content = (m.get("content") or "").strip()
        if content:
            lines.append(f"{author}: {content}")
    return "\n".join(lines)


def _gemini(prompt: str) -> str | None:
    try:
        proc = subprocess.run(
            ["gemini", "--skip-trust", "--approval-mode", "plan", "-p", prompt],
            cwd="/tmp", capture_output=True, text=True, timeout=GEMINI_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    if proc.returncode != 0:
        return None
    # strip gemini's non-answer warning lines
    noise = ("Ripgrep is not available", "Approval mode overridden",
             "Gemini CLI is not running", "Loaded cached", "Data collection")
    keep = [ln for ln in proc.stdout.splitlines()
            if not any(ln.strip().startswith(p) for p in noise)]
    return "\n".join(keep).strip()


def summarize(transcript: str, message: str, target_name: str) -> str:
    mode = ("Reproduce the most relevant messages VERBATIM (with author names), then a "
            "1-2 line note." if VERBATIM_RE.search(message)
            else "Give your own assessment / opinion after a short summary."
            if OPINION_RE.search(message)
            else "Summarize the discussion: key points, any decisions or open questions, "
                 "and who is driving it.")
    prompt = (f"You are Charlie, reading a discussion from the '{target_name}' channel in "
              f"the Sutando open-source community for the project owner. {mode} Be concise "
              f"(<1500 chars), factual, and do not invent anything not in the transcript.\n\n"
              f"TRANSCRIPT:\n{transcript[:12000]}")
    out = _gemini(prompt)
    return out or "(couldn't generate a summary — gemini returned nothing)"


def handle(message: str) -> str:
    """Owner-tier entry point. Returns the reply text for the responder to deliver."""
    query = extract_query(message)
    if not query:
        return ("I can read a community thread for you — tell me roughly which one "
                "(e.g. \"read the workspace-concept discussion\").")
    targets = list_targets()
    if not targets:
        return "I couldn't list channels in the community server (no read access or none found)."
    res = resolve_target(query, targets)
    if res.get("none"):
        return (f"I couldn't find a channel/thread matching \"{query}\". "
                f"Try naming it more specifically.")
    if res.get("candidates"):
        names = ", ".join(f"#{c['name']}" for c in res["candidates"])
        return f"A few could match \"{query}\": {names}. Which one?"
    target = res["match"]
    try:
        transcript = fetch_transcript(target["id"])
    except NoAccess:
        return (f"I found **#{target['name']}** but my role can't read it (Discord 403). "
                f"Ask Chi to grant the Charlie role View Channel + Read Message History there.")
    if not transcript:
        return f"Found #{target['name']} but it has no readable recent messages."
    summary = summarize(transcript, message, target["name"])
    return f"📖 **#{target['name']}** (Chi's server):\n\n{summary}"
