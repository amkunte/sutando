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

**Step 0 — Calendar cache. CONDITIONAL: Google-calendar hosts only.**

`src/morning-briefing.py` cannot reach the owner's Google Workspace calendar; it reads a cache the
*agent* writes. Omitting this is silent: the briefing reports "couldn't read your calendar", or
falls back to a local macOS Calendar read that stalls and can miss the work account entirely. Pull
today's local-day events from the Google connector, then:

```bash
echo '[{"raw":"9:00-9:30am 1:1 w/ Sam","calendar":"work"}]' | python3 src/write_calendar_cache.py
python3 src/write_calendar_cache.py --empty   # ONLY for a genuinely empty day
```

**Hosts with no Google connector skip STEP 0 ONLY** — the reader falls back to local macOS
Calendar. Step 1 below still runs on every host. See "Calendar source (Google Workspace) —
activation" for the full contract.

**Step 1 — Base data (canonical; runs on EVERY host, including hosts that skipped Step 0):**

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

## Calendar source (Google Workspace) — activation

`src/morning-briefing.py` is a standalone script and **cannot reach the owner's Google Workspace calendar** — the Station/Composio connector is agent-only. So the briefing reads a cache that the *agent* produces:

- **Producer:** `src/write_calendar_cache.py` writes `state/calendar-today.json` — `{"date": "YYYY-MM-DD", "events": [{"raw": "...", "calendar": "..."}]}`, atomically (tmp + `os.replace`). `date` is today in local time so a stale cache is ignored, and `events: []` means a *verified-empty* day (never rendered as "clear" from a missing cache). Feed it the events you pulled from the connector:
  ```bash
  echo '[{"raw":"9:00-9:30am 1:1 w/ Sam","calendar":"work"}]' | python3 src/write_calendar_cache.py
  python3 src/write_calendar_cache.py --empty   # verified no events today
  ```
- **Reader:** `get_calendar_events()` prefers the cache. Set `MORNING_BRIEFING_CALENDAR_SOURCE=google` to make the cache the *only trusted source* — if it's missing/stale the briefing reports "couldn't read your calendar" rather than falling back to a local macOS Calendar that may not include the work account (the 2026-07-21 "falsely clear" bug, #2256).
- **Without a Google connector:** skip the producer step and the env var; the reader falls back to local macOS Calendar via AppleScript.

Nothing writes the cache automatically, so **a briefing that only runs the reader reports unread on a Google-source host.** The producer is therefore step 0 of this skill's own flow (above), which covers both `/morning-briefing` and a cron declared as `"prompt_skill": "morning-briefing"` — the natural config. The expanded cron prompt under "Scheduling" below remains valid but is no longer the only place the producer appears.

Observed on Chis-Mac-mini: `state/calendar-today.json` was last written **2026-07-30 07:28** and the host's cron was `{"name": "morning-briefing", "prompt_skill": "morning-briefing"}`, so nothing invoked the producer. Three consecutive briefings reported no calendar, and the local fallback added a ~23s AppleScript stall before returning nothing.

## Scheduling

The canonical daily schedule produces the Google-calendar cache first, then runs the briefing against it (see the activation section above):

```json
{
  "name": "morning-briefing",
  "cron": "57 6 * * *",
  "prompt": "Morning briefing. FIRST produce the calendar cache from the owner's REAL Google calendar (the standalone script can't reach the connector): pull today's events via the Google-calendar connector (e.g. sutando-station composio_exec GOOGLECALENDAR_EVENTS_LIST, calendarId=primary, today's local-day window), then pipe them as a JSON array of {raw,calendar} to `python3 src/write_calendar_cache.py` (or `--empty` if genuinely no events). THEN run `MORNING_BRIEFING_CALENDAR_SOURCE=google python3 src/morning-briefing.py` to deliver the briefing. Speak the result if voice is connected, send as Discord DM otherwise."
}
```

Calling `/morning-briefing` manually runs the same script plus the MCP email/calendar and insight augmentation in steps 1–7.
