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
- Write to `results/briefing-{date}.txt` so the voice agent can speak it
- Send via Discord DM if configured

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
