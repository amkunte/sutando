# Charlie — community / dev node

**Charlie** is a *separate, isolated* Sutando bot that lives in a public community
Discord server (Chi's master Sutando server) to read threads, participate in
discussions, and coordinate with other contributors' bots on PR reviews and code —
**without any access to the owner's personal core** (memory, finances, calendar,
credentials).

Charlie is a third node in the multi-node model (Maverick = personal/interactive,
Goose = home base, **Charlie = public/community**), but unlike the others it treats
*everyone* — humans and peer bots alike — as untrusted.

---

## Why a separate bot (not the personal core in the public server)

The personal Sutando core has your whole life wired to it. An agent that writes code
and reviews PRs while coordinating with strangers' bots is the last thing you want one
token-leak or prompt-injection away from a public room. Charlie is its own Discord
application, its own workspace, and its own GitHub identity. Compromise stops at
Charlie; it never reaches your core.

## Isolation boundaries (the whole point)

1. **Own Discord app + token** — separate identity, separate blast radius.
2. **Own workspace** — `~/.sutando/workspace-charlie` (set via `SUTANDO_WORKSPACE`).
   Zero overlap with the personal `~/.sutando/workspace`.
3. **Own channels-config dir** — `~/.claude/channels/discord-charlie/` (set via
   `SUTANDO_DISCORD_CHANNELS_DIR`, added to `discord-bridge.py` for exactly this).
   Keeps Charlie's token + `access.json` off the personal bot's config.
4. **Own GitHub identity** — a dedicated bot account *or* a fine-grained PAT scoped to
   the public repos only. **Never** let Charlie open PRs under `amkunte`. Default it to
   PR-against-its-own-fork; anything reaching `sonichi/sutando` is human-gated.
5. **Sandboxed code execution** — Charlie reviews untrusted diffs and runs peers'
   suggestions; that work happens in a sandbox (reuse the `codex exec --sandbox
   read-only` pattern the bridge already uses for non-owner tiers), never with host
   privileges.

**The trap:** there must be **no automated pipe from Charlie back into the personal
core.** If Charlie ever needs something only the personal agent has, that's a
you-in-the-loop handoff — not a bot2bot message the core auto-executes. Treat anything
from Charlie as `other`-tier.

## Coordination — reuse the existing bot2bot protocol

The `#bot2bot` conventions already in Sutando (`claim:` / `blocked:` / `done:` /
`opinion-requested:` / `nack:`, first-PR-opened-wins, **no merge authority for bots**)
were built for multi-agent PR negotiation. Charlie speaks that same protocol in the
community's coordination channel. Nothing new to invent.

---

## Owner checklist (the parts only you can do)

These are web-portal actions — ~5 minutes total. Do them, drop the secrets in the two
files noted, and the launch script does the rest.

- [ ] **1. Create the Discord application "Charlie".**
  - https://discord.com/developers/applications → New Application → name `Charlie`.
  - Bot tab → **enable "Message Content Intent"** (so it can read thread text).
  - Bot tab → **enable "Public Bot"** (so Chi/an admin can add it to the server).
  - Copy the **bot token**.
- [ ] **2. Drop the token** into `~/.claude/channels/discord-charlie/.env`:
  ```
  DISCORD_BOT_TOKEN=<charlie-token>
  ```
- [ ] **3. Create Charlie's `access.json`** at `~/.claude/channels/discord-charlie/access.json`:
  ```json
  { "allowFrom": [], "tierMap": {}, "guilds": {} }
  ```
  Empty `allowFrom` = **no one is owner** → every message is non-owner → sandboxed by
  default. That is the safety posture you want for a public bot. (Add your own user id
  to `allowFrom` later only if you want to drive Charlie directly.)
- [ ] **4. GitHub identity for code/PR work** — pick one:
  - *Preferred:* a dedicated GitHub bot account, added as a collaborator on your fork.
  - *Quick:* a fine-grained PAT scoped to **public-repo fork only**, stored in
    `~/.claude/channels/discord-charlie/.env` as `GITHUB_TOKEN=…`. Never a classic
    token with broad scope.
- [ ] **5. Invite Charlie to Chi's server** — generate the invite link (client_id = the
  Charlie bot's user id) and either click it (if you have Manage Server there) or send
  it to Chi:
  `https://discord.com/oauth2/authorize?client_id=<CHARLIE_CLIENT_ID>&scope=bot+applications.commands&permissions=117760`
- [ ] **6. Host decision** — recommended: run Charlie on **Goose** once it's stable
  (separate home = isolation for free). Until then it can co-host on Maverick via the
  env overrides below; the `SUTANDO_DISCORD_CHANNELS_DIR` patch makes that safe.

## Launch

Once the checklist is done:
```bash
bash skills/charlie-node/scripts/launch-charlie.sh
```
This starts Charlie's discord-bridge against the isolated workspace + channels dir. A
Charlie **core** (a Claude Code session pointed at `~/.sutando/workspace-charlie`) must
also run to process Charlie's tasks — see "Core" below.

## Core (processing Charlie's tasks)

The bridge only writes task files; a core consumes them. Charlie's core should boot
with the same env overrides (so it resolves Charlie's workspace) and a **restricted
system posture**: all inbound is `other`-tier, code work is sandboxed, PRs are
human-gated. This is the heavier piece and is intentionally left as the next build step
after the bot is live and reading threads — see the project tracker.

## Status

Scaffolded 2026-06-10 (branch `feat/charlie-community-node`). Live bot pending the
owner checklist above + a host decision. The `SUTANDO_DISCORD_CHANNELS_DIR` override in
`src/discord-bridge.py` is the only core change required for isolated co-hosting.
