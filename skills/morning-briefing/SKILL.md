---
name: morning-briefing
description: "Generate a daily morning briefing: email, calendar, Discord, and news — delivered via voice or Discord DM."
user-invocable: true
---

# Morning Briefing

Generate a prioritized daily briefing from all your channels.

**Usage**: `/morning-briefing`

ARGUMENTS: $ARGUMENTS

## What to gather

**Step 0 — Base data (canonical, always run first):**

```bash
WORKSPACE="$(bash scripts/sutando-config.sh workspace)"
python3 src/morning-briefing.py
```

`src/morning-briefing.py` is the single source of truth for core briefing data: weather (Open-Meteo), macOS Calendar, macOS Reminders, overnight Discord DMs, pending questions, and system health. It writes output to `results/proactive-<ts>.txt` and sends a Discord DM directly. Review its output before composing the full briefing — do NOT re-fetch those sources manually.

**Then augment with the following if configured (skip if not available):**

1. **Email** — Use the Gmail MCP tool `mcp__claude_ai_Gmail__search_threads` with query `is:unread in:inbox` to get the unread inbox. Summarize top 5 by priority. Flag anything urgent.

2. **Calendar** — Use the Google Calendar MCP tool `mcp__claude_ai_Google_Calendar__list_events` with `startTime`/`endTime` spanning today in `America/Los_Angeles`. Enumerate non-primary calendars first via `mcp__claude_ai_Google_Calendar__list_calendars` (Family, group rides, etc.) and query each. List meetings with times. For each: who's attending, what it's about. Flag any travel (flights, OOO).

> **Data source note:** Email + Calendar use the built-in Google MCP connectors, not the retired `gws` CLI (uninstalled 2026-05; no install source on this host). The MCP path requires the claude.ai Google connectors to be authenticated in the running session — present in this interactive session, but may be absent in a headless/cron-spawned one. If a connector tool is unavailable, skip that source gracefully and note it in the briefing rather than failing the whole run.

3. **Pending tasks** — Check `pending-questions.md` for unanswered items. Check `tasks/` for queued tasks.

4. **System status** — Run `python3 src/health-check.py`. Report any issues.

5. **Daily insight** — Run `python3 src/daily-insight.py --stdout-only`. If it produces an insight, include it at the end of the briefing as "💡 Insight: ..."

6. **Friction check** — Run `python3 src/friction-detector.py --stdout-only`. If friction items found, include as "⚠️ Friction: [count] items need attention" with the top 3.

7. **SutandoWIRE** — Run `python3 src/wire_briefing.py`. If it prints a line (it only does so when a NEW WIRE episode has appeared since the last briefing), include that line verbatim — it's already a fully-formed `📺 New SutandoWIRE: <title> — <url>`. Silent output = no new episode; skip the line. The script is a clean no-op without `YOUTUBE_API_KEY` (env or vault) and tracks last-seen in `state/wire-briefing.json`, so each episode is announced exactly once across both briefing paths.

> **Why `--stdout-only`:** these scripts default to writing `results/insight-*.txt` / `results/friction-*.txt`, which the Telegram/Discord bridge polls and delivers as SEPARATE DMs — fragmenting the briefing into 3 messages. `--stdout-only` prints the content (for you to fold inline here) without writing a deliverable file, so the owner gets ONE consolidated briefing. Do NOT remove the flag.

## How to deliver

Run `python3 src/morning-briefing.py` first — it is the single source of truth for the base data (weather, calendar, reminders, overnight Discord, pending questions, health). Fold the augmentation from steps 1–8 into ONE message.

> **Delivery on this host differs from upstream's default.** Upstream's version of
> this section tells you to append a follow-up `results/proactive-<ts>.txt`. Do NOT
> do that here: `results/proactive-*` is polled by BOTH the Telegram and Discord
> bridges, so it double-delivers to the owner's DM and fragments the brief. The
> #dailybriefings channel post below is the sole delivery path.

Compose the briefing as:

```
Good morning. Here's your briefing:

📧 Email: [count] unread. [urgent summary]
📅 Calendar: [count] meetings today. [next meeting info]
💬 Discord: [summary of overnight activity]
📋 Tasks: [pending items]
🖥️ System: [health status]
💡 Insight: [behavioral pattern from daily-insight.py, if available]
```

Deliver via:
- **Primary: post to the Discord #dailybriefings channel** (owner's request 2026-06-02 — briefs live in their own channel, not DMs). Post directly with the channel id from `state/discord-config.json` → `channels.dailybriefings`:
  ```bash
  WORKSPACE="$(bash scripts/sutando-config.sh workspace)"
  CH=$(python3 -c "import json,sys;from pathlib import Path;print(json.load(open(Path(sys.argv[1])/'state/discord-config.json'))['channels']['dailybriefings'])" "$WORKSPACE")
  python3 src/discord_post.py "$CH" "$BRIEF_TEXT"
  ```
  This posts straight to the channel via the bot token — deterministic, NOT subject to proactive-DM routing. If `channels.dailybriefings` is missing, fall back to the proactive path below.
- Keep a record at `notes/briefings/briefing-{date}.md` (a NON-polled path). **Do NOT write `results/briefing-{date}.txt` or any `results/proactive-*` copy** — those prefixes are polled by the Telegram/Discord bridges and would double-deliver the brief to the owner's DM. The #dailybriefings channel post is the sole delivery.

**After delivering, mark today done** — touch the delivery sentinel so the proactive-loop's scheduled-catchup (`src/scheduled-catchup.py`) knows the briefing went out and does NOT re-run it:
```bash
touch "$(bash scripts/sutando-config.sh workspace)/state/briefing-delivered-$(date +%F).sentinel"
```

**Never touch that sentinel without an actual post** — a pre-touched sentinel silently suppresses the briefing for the whole day (observed 2026-06-17→19). It is proof-of-delivery, not a scheduling hint.

## Scheduling

The canonical daily schedule calls the script directly (same code path):

```json
{
  "name": "morning-briefing",
  "cron": "57 6 * * *",
  "prompt": "Run python3 src/morning-briefing.py to deliver the daily morning briefing (weather, calendar, reminders, overnight Discord, pending questions, health). Speak the result if voice is connected, send as Discord DM otherwise."
}
```

Calling `/morning-briefing` manually runs the same script plus the MCP email/calendar and insight augmentation in steps 1–7.
