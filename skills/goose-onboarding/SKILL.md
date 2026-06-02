---
name: goose-onboarding
description: "Setup-day runbook for bringing the second Sutando node online — Goose, a Mac mini that becomes the always-on HOME BASE (hosts the web-client + Cloudflare tunnel + all recurring crons), while Maverick (the MacBook Air) becomes the roaming/interactive node. Run /goose-onboarding on the mini once it's provisioned."
user-invocable: true
---

# Goose Onboarding

Bring the second node online and shift the always-on duties to it. Run this **on the Mac mini (Goose)** once Sutando is cloned there.

**Target topology (decided 2026-06-02):**
- **Goose** (Mac mini, always-on at home) = HOME BASE — serves the web-client, hosts the Cloudflare Tunnel + Access, runs ALL recurring crons, owns the live skill state.
- **Maverick** (MacBook Air) = roaming/interactive — dev + conversations, travels, sleeps freely, NO always-on duties.
- Memory syncs node↔node via the private repo. bot2bot = Maverick ↔ Goose.

Walk these in order. Where a step needs the owner (secrets, a Discord token, a Cloudflare login), pause and ask — never invent secrets.

## 0. Pre-flight (owner + check)
- [ ] Mac mini on the home LAN, will stay powered + awake (System Settings → Energy → "prevent sleep when display off"; disable auto-sleep).
- [ ] Toolchain: `git`, Node (nvm), `python3` (+ `pip`), `tsx`. Install what's missing.
- [ ] GitHub auth for `amkunte/sutando` (gh auth / SSH key).

## 1. Provision the code
- [ ] Clone the deployment branch: `git clone -b local-main git@github.com:amkunte/sutando.git ~/sutando` (or owner's preferred path; note `SUTANDO_REPO_DIR`).
- [ ] Install deps: `npm install`; `/usr/bin/python3 -m pip install --user discord.py` (+ anything `src/startup.sh` preflight flags).

## 2. Secrets + workspace + identity
- [ ] Workspace: `~/.sutando/workspace` (default). Confirm `resolve_workspace()` resolves there.
- [ ] **Owner provides** `.env` (Twilio/ngrok/GEMINI/etc.) and `~/.claude/channels/*/.env` tokens — copy securely (AirDrop / 1Password), do NOT commit. Set `SUTANDO_DEFAULT_PROACTIVE_CHANNEL=telegram` (or per preference).
- [ ] Node identity = **Goose**: in `state/discord-config.json` set `node_name: "Goose"`. (Maverick stays "Maverick".)

## 3. Make Goose the daemon host (the cutover)
- [ ] Recurring crons move HERE: morning-briefing, parcel/trip/amazon/karts-air scans, Siemens drip, memory-sync, upstream-sync, subscription/disk. They live in `skills/schedule-crons/crons.json` — run `/schedule-crons` on Goose.
- [ ] **On Maverick, STOP the always-on crons** so they don't double-run (remove from Maverick's crons.json or rely on a per-node guard). Maverick keeps only interactive use.
- [ ] **Skill state** (trips/parcels/orders/karts-air json) must live on Goose: the scans regenerate it from Gmail/Drive, so just run each scan once on Goose to repopulate — OR rsync the current `skills/*/state/` from Maverick for continuity (preserves manual marks / suggestions). Memory (`$SUTANDO_MEMORY_DIR`) comes via the sync repo (step 6).

## 4. Web-client on Goose
- [ ] Start the web-client on Goose (`src/startup.sh` handles it). It now serves karts-air / trips / parcels / amazon / siemens-prep from Goose's live state.

## 5. Remote access — Cloudflare Tunnel + Access + PWA
- [ ] Install `cloudflared`; `cloudflared tunnel login` (owner's Cloudflare account), create a named tunnel → `http://localhost:8080`, map a hostname (e.g. `maverick.<owner-domain>` — keep the friendly name).
- [ ] **Cloudflare Access** policy: allow ONLY the owner's Google email (a.kunte@gmail.com). This is the real security boundary — see ⚠️ below.
- [ ] Add a **PWA manifest** + icons to the web-client pages so the site installs to the phone home screen (icon, standalone display). Add `<link rel="manifest">` + a minimal `manifest.webmanifest`.
- [ ] Run `cloudflared` under launchd so the tunnel survives reboot.
- ⚠️ **Security:** the `/*/scan`, `/parcels/mark-delivered`, `/amazon/mark-delivered`, etc. endpoints are gated to `127.0.0.1`. A tunnel makes external requests arrive AS localhost, so that gate no longer protects them — **Cloudflare Access (owner-email-only) becomes the boundary.** Add a shared-secret header that `cloudflared`/Access injects and the web-client verifies, as defense-in-depth, before exposing write endpoints.

## 6. Always-on + memory sync
- [ ] launchd watchdog: install `com.sutando.core` on Goose (`scripts/install-core-launchagent.sh`) — RunAtLoad + keepalive so Sutando survives reboot/crash.
- [ ] Memory sync: clone `~/.sutando-memory-sync` (private repo `github.com/amkunte/sutando-memory.git`) and schedule `sync-memory.sh` — Goose pulls Maverick's memory + pushes its own. Both nodes converge.

## 7. bot2bot wiring  ← DECISION REQUIRED
- [ ] **Does Goose run its own Discord bot, or stay headless?** For true two-way bot2bot (claims / reviews / coordination posts), Goose needs its OWN Discord app+token ("Goose"), invited to the same server. Alternative: Goose is headless infra and Maverick stays the sole Discord face (simpler, but no Goose-voice in chat). RECOMMEND: give Goose its own bot for real Maverick↔Goose coordination.
- [ ] If dual-bot: create the "Goose" Discord app + token (Developer Portal), invite to the server, enroll owner, set Goose's `discord-config`. Configure `bot2bot-post` so each node @-mentions the other in a coord channel (e.g. a `#bot2bot` or reuse `#sutando-upstream`).
- [ ] **Avoid double-processing:** ensure the two bots don't both handle the same owner DM (distinct bot tokens + each only processes its own mentions/DMs; the file-bridge task dedup + per-node claim logic must be sane). Verify no echo loops before leaving it running.

## 8. Cutover verification
- [ ] From your phone (off home wifi): open the Cloudflare hostname → Google-login → confirm karts-air/trips/parcels load live. Install as PWA.
- [ ] Trigger a scan on Goose → confirm its page updates + (if delivery configured) the Discord channel post lands.
- [ ] Confirm Maverick can sleep/leave and the tunnel + pages + crons keep running on Goose.
- [ ] bot2bot: post a `ping:` from one node, confirm the other sees it — no duplicate owner DMs.
- [ ] Update memory `project_sutando_status.md`: Goose LIVE as home base; Maverick interactive.

## Notes
- Keep all upstream-contributable work flowing through `/red-team` → `sonichi:main`.
- This runbook is the plan, not gospel — re-judge each step against the actual mini state on setup day.
