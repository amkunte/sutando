#!/usr/bin/env python3
"""Phase-1 (script generation) via Gemini — a no-OpenAI alternative to the
codex step in build.sh, for the cards-only render path.

Why this exists: the original Phase-1 ran `codex exec` (OpenAI) in a
workspace-write sandbox that BOTH generated the script AND fetched asset
URLs into fetched_assets/ + wrote asset_manifest.json. That requires a paid
OpenAI account. This script replaces the *script-generation* half with a
Gemini REST call (uses GEMINI_API_KEY already in .env). It does NOT fetch
assets — it is for the **cards-only** mode where every frame is a generated
stat-card (text/number on brand background), so there are no external images
to fetch. build.sh skips Phase-2 asset validation in that mode.

Output contract (consumed by render.py):
- artifacts/final_script.md   — HOOK / SUPPORT / CLOSER sections (parse_script)
- artifacts/asset_manifest.json — [] (cards-only: no fetched assets)
- artifacts/source_table.json   — the sources/anchors we cite (informational)

Source input (Goose trap #2 — never sed-inline file *content*; pass the path,
read it here):
- --source-file <path>  : local md/text brief; contents read directly, no fetch
- --source-url <url>    : fetched with urllib (best-effort) when no file given
At least one is required; --source-file wins if both are supplied.

Env: GEMINI_API_KEY (from repo .env, exported by build.sh).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
import urllib.error
from pathlib import Path

MODEL = os.environ.get("GEMINI_PHASE1_MODEL", "gemini-2.5-flash")
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"

PROMPT = """\
You are scripting a short news-explainer video (~{duration}s, 1280x720) in the
"Mini Wire" house style. The video is **cards-only**: every frame is a generated
text/number card (no photos, no fetched images). So you write ONLY narration
text — do NOT describe images, do NOT produce an asset manifest, do NOT mention
visuals.

Topic: {topic}

Primary source (verbatim — treat every number/name as ground truth; invent
nothing, embellish nothing):
<<<SOURCE
{source}
SOURCE
>>>

Achieve **specificity-shape virality**: ONE striking moment that makes someone
share — not a comprehensive overview.

Output EXACTLY three markdown sections, in this order, with these headers:

## HOOK
One sentence, ~10-13 words. Open with the surprising element itself, NOT its
provenance. No hedging. Must be defensible from the source. Do NOT open with the
source's name as the subject.

## SUPPORT
3-5 short factual sentences (total <= {words} words) that build on the hook.
Vary sentence openings (do not start each with an attribution). At least one
transition should be a curiosity beat (a question or a contrast). Every number
must come straight from the source.

## CLOSER
One sentence, ~10-13 words. The share-moment — a pointed observation, a
surprising number, or a provocation. NOT a recap, NOT a colon-list.

Hard length contract: HOOK + SUPPORT + CLOSER combined must be <= {total_words}
words (the TTS/ffprobe gate enforces duration). Count before you finalize.
Output ONLY the three sections — no preamble, no notes, no image talk.
"""


def read_source(args) -> str:
    if args.source_file:
        p = Path(args.source_file)
        if not p.is_file():
            sys.exit(f"gemini_phase1: --source-file not found: {p}")
        return p.read_text(encoding="utf-8", errors="replace")
    if args.source_url:
        try:
            req = urllib.request.Request(args.source_url, headers={"User-Agent": "sutando-miniwire/1.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace")[:20000]
        except Exception as e:  # noqa: BLE001 — best-effort fetch; surface clearly
            sys.exit(f"gemini_phase1: failed to fetch --source-url ({e})")
    sys.exit("gemini_phase1: need --source-file or --source-url")


def call_gemini(prompt: str, key: str) -> str:
    body = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        # thinkingBudget=0 disables 2.5-flash's internal reasoning, which would
        # otherwise consume the output budget and truncate the script to bare
        # headers. This is a deterministic structured-generation task — no
        # chain-of-thought needed. maxOutputTokens generous for headroom.
        "generationConfig": {
            "temperature": 0.8,
            "maxOutputTokens": 2048,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }).encode()
    url = API.format(model=MODEL, key=key)
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:  # surface API errors verbatim
        sys.exit(f"gemini_phase1: Gemini API HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:500]}")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"gemini_phase1: Gemini call failed: {e}")
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        sys.exit(f"gemini_phase1: unexpected Gemini response: {json.dumps(data)[:500]}")


_HDR = re.compile(r"^\s*[#*]*\s*(HOOK|SUPPORT|CLOSER)\b[*:]*\s*(.*)$", re.IGNORECASE)


def normalize_script(raw: str) -> str:
    """Emit clean "## HOOK/SUPPORT/CLOSER" headers (the shape render.py's
    parse_script accepts), preserving the body text under each. Line-based so a
    header's trailing whitespace/newline never swallows the following body
    line. Drops any preamble before the first header; keeps inline header text
    (e.g. "## HOOK: foo" -> header + "foo")."""
    out: list[str] = []
    started = False
    for line in raw.strip().splitlines():
        m = _HDR.match(line)
        if m:
            started = True
            out.append(f"## {m.group(1).upper()}")
            rest = m.group(2).strip()
            if rest:
                out.append(rest)
        elif started:
            out.append(line)
    return "\n".join(out).strip() + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--source-file")
    ap.add_argument("--source-url")
    ap.add_argument("--target-duration", type=int, default=45)
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        sys.exit("gemini_phase1: GEMINI_API_KEY not set in env")

    source = read_source(args)
    total_words = int(args.target_duration * 2.0)   # ~120 wpm budget
    support_words = max(30, total_words - 26)        # leave ~13 each for hook+closer
    prompt = PROMPT.format(
        duration=args.target_duration, topic=args.topic, source=source[:16000],
        words=support_words, total_words=total_words,
    )

    print(f"[gemini-phase1] model={MODEL} source={'file' if args.source_file else 'url'} "
          f"budget<={total_words}w", file=sys.stderr)
    raw = call_gemini(prompt, key)
    script = normalize_script(raw)

    out = Path(args.out_dir)
    art = out / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    (art / "gemini-raw.txt").write_text(raw, encoding="utf-8")  # debug trace
    (art / "final_script.md").write_text(script, encoding="utf-8")
    # cards-only: no fetched assets, empty manifest so render.py composes cards.
    (art / "asset_manifest.json").write_text("[]\n", encoding="utf-8")
    # source_table is informational here (render doesn't require it in cards mode).
    (art / "source_table.json").write_text(json.dumps([{
        "source_title": args.topic,
        "url_or_path": args.source_file or args.source_url or "",
        "source_type": "official",
        "reliability_level": "high",
    }], indent=2) + "\n", encoding="utf-8")

    print("[gemini-phase1] wrote final_script.md (cards-only, empty manifest)", file=sys.stderr)
    sections = [ln for ln in script.splitlines() if ln.startswith("## ")]
    print(f"[gemini-phase1] sections: {sections}", file=sys.stderr)


if __name__ == "__main__":
    main()
