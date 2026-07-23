# Frontier Scan — scan-prompt (follow verbatim)

You are running **Frontier Scan** for the owner: a weekly sweep of leading AI-agent
frameworks for NEW capabilities worth adapting into Sutando. **Script does the
mechanics (fetch + dedup); you do the judgment (is this useful to us?) + delivery.**

Workspace paths resolve under `${SUTANDO_WORKSPACE:-$HOME/.sutando/workspace}`; the
skill's own state lives in `skills/frontier-scan/state/`.

## Step 1 — pull new items (deterministic)

```bash
python3 skills/frontier-scan/scripts/fetch_sources.py
```

> **⚠️ This run is destructive to the delta — never run it just to "preview."**
> `fetch_sources.py` records every returned `new_items[]` into `state/seen.json` and
> advances `last_scan` *on the same run that prints them*. So the items are surfaced
> exactly once: if you run it standalone to gauge whether there's signal (e.g. mid a
> cron burst) and don't carry through to Step 3–4 delivery, those items are now marked
> seen and the next run reports `0 new` — the delta is silently consumed and never
> reaches #skills-dev. Run it **only** as the real Step 1 of a full scan you intend to
> deliver. If you already burned a delta this way, recover the release bodies straight
> from GitHub (`gh api repos/<slug>/releases/tags/<tag>`, or `/releases` for a
> pre-release) — `seen.json` stores only keys+timestamps, not bodies — and hand-deliver.
> (Learned 2026-07-23: a gauge-run during a cron avalanche consumed the OpenClaw
> 2026.7.1/7.2 delta; recovered via `gh api` and hand-posted.)

This reads `sources.json`, pulls the latest GitHub releases/tags for each tracked
repo, diffs against `state/seen.json`, advances `last_scan`, and prints JSON:
- `new_items[]` — GitHub releases/tags not seen before (each has source, title, tag, url, published, body, why_track)
- `web_sources[]` — sources with no fixed repo (currently **OpenClaw**) that need a WebSearch
- `skipped[]` — any source that errored this run (network / rate-limit / 404). **Never drop these silently** — if a source is skipped two weeks running, note it for the owner so a dead repo slug gets fixed.

## Step 2 — cover the web sources

For each entry in `web_sources[]`, run a `WebSearch` with its `search` query (plus
`newer_than` ~10 days in spirit — you want *this week's* news). Identify any genuinely
new release / feature / notable capability. For **OpenClaw** specifically: if you find
a canonical GitHub repo, note it in the delivery so the owner can convert `sources.json`
to `kind=github` (more reliable than search). Do **not** fabricate releases — if nothing
new/credible turns up, say "no new signal."

## Step 3 — judge + write takes

For each new item (GitHub or web), write a tight 2-line entry:
- **What it is** — one line, concrete (e.g. "v0.4 adds sub-agent streaming handoffs").
- **Adopt?** — one line: does this map to something Sutando does (memory, fleet coord,
  skills, voice, task bridge)? Give a verdict — `worth a look` / `adopt` / `skip` — and why.

Keep it skimmable. Drop pure version-bump noise (dependency bumps, typo releases) — only
surface things with a *capability* delta. Quality over completeness.

## Step 4 — deliver

**Only deliver if there's at least one new capability-bearing item** (GitHub or web).
Post to the Discord **#skills-dev** channel:

```bash
CH=$(python3 -c "import json,os;from pathlib import Path;ws=os.environ.get('SUTANDO_WORKSPACE',str(Path.home()/'.sutando/workspace'));print(json.load(open(Path(ws)/'state/discord-config.json')).get('channels',{}).get('skills-dev','') or json.load(open(Path(ws)/'state/discord-config.json')).get('channels',{}).get('skillsdev',''))" 2>/dev/null)
if [ -n "$CH" ]; then python3 src/discord_post.py "$CH" "$MSG"; else printf '%s' "$MSG" > "${SUTANDO_WORKSPACE:-$HOME/.sutando/workspace}/results/proactive-frontier-scan-$(date +%s).txt"; fi
```

Post to the channel **OR** write the `results/proactive-*.txt` fallback — **never both**
(writing both double-notifies; see [[feedback_scan_post_to_channel_not_results_fallback]]).
The `#skills-dev` id is in `discord-config.json` → `channels['skills-dev']`.

Message shape:

```
🛰️ Frontier scan — N new capabilities across agent frameworks:

**<Source> — <title>** (<url>)
What: <one line>. Adopt: <worth a look/adopt/skip> — <why>.

… (repeat per item) …

⚠️ skipped this run: <source — reason>   (only if skipped[] non-empty)
```

If `new_items[]` and the web step are both empty → **stay silent** (post nothing). The
`last_scan` already advanced in Step 1, so the scan-catchup backstop stays happy.

## Notes

- Idempotent: every GitHub item is recorded in `seen.json`; rerunning the same week
  surfaces nothing new. Safe to run from cron or the scan-catchup backstop.
- Owner-tunable: edit `sources.json` to add/remove frameworks (GitHub repo slug, or a
  `kind=web` search entry). No code change needed.
- The whole reason this skill exists durably (skill + `crons.json` entry + scan-catchup)
  is that the prior version was a session-only cron that silently vanished in the
  Maverick→Goose migration. Keep it in `crons.json`.
