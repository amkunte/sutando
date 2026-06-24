---
name: frontier-scan
description: "Weekly competitive-intelligence sweep of leading AI-agent frameworks (Claude Agent SDK, Letta/MemGPT, OpenAI Swarm, Nous/Hermes, OpenClaw) for NEW capabilities worth adapting into Sutando. Script pulls + dedups GitHub releases; the agent judges relevance and posts capability candidates to #skills-dev. Silent when nothing new."
user-invocable: true
---

# Frontier Scan

Watches the frontier of agent frameworks so Sutando can cherry-pick new ideas
instead of reinventing them. **Scripts do mechanics, the agent does judgment,
delivery is silent-unless-new** — same idiom as `skill-synth` (which mines our
*own* history; this watches *external* projects).

**Usage**: `/frontier-scan` (run the weekly sweep now)

## What it does

1. `scripts/fetch_sources.py` pulls the latest GitHub releases/tags for each repo in
   `sources.json`, diffs against `state/seen.json`, and prints only NEW items (+ a
   `web_sources` list for sources with no fixed repo, currently OpenClaw).
2. The agent covers the web sources via WebSearch, writes a 2-line *what-it-is /
   should-we-adopt* take per new item, and posts to **#skills-dev** — or stays silent
   if nothing new surfaced.

Full step-by-step contract: [`scan-prompt.md`](scan-prompt.md).

## Sources (owner-tunable)

Edit `sources.json` — each entry is either `kind=github` (repo slug, pulled
deterministically) or `kind=web` (a WebSearch query, judged by the agent). Current set:
Claude Agent SDK (py + ts), Letta/MemGPT, OpenAI Swarm, Nous/Hermes, OpenClaw (web).

## Durability (why it's built this way)

The predecessor was a session-only `CronCreate` job never committed to `crons.json`,
so it silently died in the Maverick→Goose migration and nobody noticed for ~2 weeks.
This version is durable on three legs:
- **`crons.json` entry** (`frontier-scan`, weekly) → re-registered by `/schedule-crons` on every restart.
- **`scan-catchup.py` backstop** → if the cron ever lapses, the proactive loop re-fires it off `state/seen.json`'s `last_scan` (7-day cadence). Trigger is on-disk state, not a live cron, so it cannot silently stop.
- **`last_scan` advances every run** → the backstop can always tell whether the scan is alive.

## Cadence

Weekly (owner's call 2026-06-24). Cron: see `skills/schedule-crons/crons.json`.
