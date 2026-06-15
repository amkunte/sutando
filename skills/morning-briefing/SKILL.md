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

Collect from each source (skip any that aren't configured):

1. **Email** — Use the Gmail MCP tool `mcp__claude_ai_Gmail__search_threads` with query `is:unread in:inbox` to get the unread inbox. Summarize top 5 by priority. Flag anything urgent.

2. **Calendar** — Use the Google Calendar MCP tool `mcp__claude_ai_Google_Calendar__list_events` with `startTime`/`endTime` spanning today in `America/Los_Angeles`. Enumerate non-primary calendars first via `mcp__claude_ai_Google_Calendar__list_calendars` (Family, group rides, etc.) and query each. List meetings with times. For each: who's attending, what it's about. Flag any travel (flights, OOO).

> **Data source note:** Email + Calendar use the built-in Google MCP connectors, not the retired `gws` CLI (uninstalled 2026-05; no install source on this host). The MCP path requires the claude.ai Google connectors to be authenticated in the running session — present in this interactive session, but may be absent in a headless/cron-spawned one. If a connector tool is unavailable, skip that source gracefully and note it in the briefing rather than failing the whole run.

3. **Discord** — Read recent messages from `logs/discord-bridge.log` (tail ~100 lines). Summarize anything actionable from overnight. Reference channel ID mapping at `$SUTANDO_MEMORY_DIR/reference_discord_channels.md`. Only surface messages NOT already replied to by the bridge.

4. **Pending tasks** — Check `pending-questions.md` for unanswered items. Check `tasks/` for queued tasks.

5. **System status** — Run `python3 src/health-check.py`. Report any issues.

6. **Daily insight** — Run `python3 src/daily-insight.py --stdout-only`. If it produces an insight, include it at the end of the briefing as "💡 Insight: ..."

7. **Friction check** — Run `python3 src/friction-detector.py --stdout-only`. If friction items found, include as "⚠️ Friction: [count] items need attention" with the top 3.

8. **SutandoWIRE** — Run `python3 src/wire_briefing.py`. If it prints a line (it only does so when a NEW WIRE episode has appeared since the last briefing), include that line verbatim — it's already a fully-formed `📺 New SutandoWIRE: <title> — <url>`. Silent output = no new episode; skip the line. The script is a clean no-op without `YOUTUBE_API_KEY` (env or vault) and tracks last-seen in `state/wire-briefing.json`, so each episode is announced exactly once across both briefing paths.

> **Why `--stdout-only`:** these scripts default to writing `results/insight-*.txt` / `results/friction-*.txt`, which the Telegram/Discord bridge polls and delivers as SEPARATE DMs — fragmenting the briefing into 3 messages. `--stdout-only` prints the content (for you to fold inline here) without writing a deliverable file, so the owner gets ONE consolidated briefing. Do NOT remove the flag.

## How to deliver

Format as a concise briefing:

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
  CH=$(python3 -c "import json,os;from pathlib import Path;ws=os.environ.get('SUTANDO_WORKSPACE',str(Path.home()/'.sutando/workspace'));print(json.load(open(Path(ws)/'state/discord-config.json'))['channels']['dailybriefings'])")
  python3 src/discord_post.py "$CH" "$BRIEF_TEXT"
  ```
  This posts straight to the channel via the bot token — deterministic, NOT subject to proactive-DM routing. If `channels.dailybriefings` is missing, fall back to the proactive path below.
- Keep a record at `notes/briefings/briefing-{date}.md` (a NON-polled path). **Do NOT write `results/briefing-{date}.txt` or any `results/proactive-*` copy** — those prefixes are polled by the Telegram/Discord bridges and would double-deliver the brief to the owner's DM. The #dailybriefings channel post is the sole delivery.

**After delivering, mark today done** — touch the delivery sentinel so the proactive-loop's scheduled-catchup (`src/scheduled-catchup.py`) knows the briefing went out and does NOT re-run it:
```bash
touch "${SUTANDO_WORKSPACE:-$HOME/.sutando/workspace}/state/briefing-delivered-$(date +%F).sentinel"
```

## Scheduling

To run daily, add to the proactive loop or use `/loop`:
```
/loop 24h /morning-briefing
```

Or schedule at a specific time via cron.
