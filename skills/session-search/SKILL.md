---
name: session-search
description: "Full-text search (SQLite FTS5) over Sutando's own session history — Claude Code conversation transcripts + project docs (build_log, notes). Turns 'when did we discuss X?' / 'what did the owner decide about Y?' into a sub-second ranked query instead of grepping hundreds of MB of JSONL. Use when you need to recall a past decision, a prior answer, or when/why something was built."
user-invocable: true
---

# Session Search

An FTS5 index over this machine's Sutando session history. The conversation
transcripts (`~/.claude/projects/<repo>/*.jsonl`) hold every past decision,
answer, and rationale — but they're hundreds of MB of JSONL, too big to grep
casually and far too big to re-read. This skill indexes them (plus `build_log.md`
and `notes/**/*.md`) into a local SQLite FTS5 database and gives you ranked,
snippet-highlighted search in milliseconds.

## When to use

- "When did we discuss / decide / build X?"
- "What did the owner say about Y?" / "Did I already answer Z?"
- Recovering context that rolled off — the transcript remembers what the live
  buffer forgot.

## Usage

**Search:**
```bash
python3 skills/session-search/scripts/search.py "twilio webhook signature"
python3 skills/session-search/scripts/search.py --role user "did I ask about FTS5"
python3 skills/session-search/scripts/search.py --role assistant "what did I recommend"
python3 skills/session-search/scripts/search.py --kind doc "exec approval policy"
python3 skills/session-search/scripts/search.py -n 20 --json "skill synth"
```
Query is FTS5 syntax: bare words AND-match; `OR`, `NOT`, `"quoted phrase"`,
`prefix*` all work. Results are bm25-ranked with a `«highlighted»` snippet,
the role (user/assistant/doc), timestamp, and line number.

**Index (build / update):**
```bash
python3 skills/session-search/scripts/index.py            # incremental — fast
python3 skills/session-search/scripts/index.py --stats    # counts only
python3 skills/session-search/scripts/index.py --rebuild  # from scratch
```
Incremental is the default and cheap: transcripts are append-only JSONL, so the
indexer remembers the byte offset already read per file and only parses the new
tail (full 16-file/220MB build ≈ 11s; a no-change re-run ≈ 0.2s). Run it before
searching if the session has moved on, or wire it to a cron (e.g. every 15 min)
to keep the index warm.

## What's indexed

- **Transcripts** → `user` and `assistant` **text** only. `thinking` blocks and
  tool_use/tool_result noise are deliberately excluded — they bloat the index
  and rarely hold the answer you're looking for.
- **Docs** → `build_log.md` + `notes/**/*.md`, chunked by paragraph.

## Layout

- DB: `<workspace>/state/session-search.db` (runtime; not committed).
- `scripts/common.py` — paths, schema (external-content FTS5 + sync triggers).
- `scripts/index.py` — incremental indexer.
- `scripts/search.py` — query CLI.
- Self-contained, stdlib-only, Python 3.9-safe. No `src/` imports.

## Notes

- Transcript dir auto-resolves to `~/.claude/projects/<mangled-repo-path>`;
  override with `SESSION_SEARCH_TRANSCRIPT_DIR`.
- If a transcript is rewritten/compacted (shrinks), that file is re-indexed
  from scratch automatically; everything else stays incremental.
