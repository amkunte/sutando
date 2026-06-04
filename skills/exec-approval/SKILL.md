# Exec Approval

A policy gate for high-risk actions. Before the core agent performs an
irreversible / outward-facing / destructive action, it classifies the action
against a declarative policy and — when the policy says `confirm` — **pauses
and requests async owner sign-off in the Discord `#approvals` channel** before
proceeding.

This makes the "confirm before irreversible actions" rule in `CLAUDE.md`
**structural** (a checked policy + an audit trail) instead of relying purely on
the agent's judgment.

## When to use

Run the gate before any action in these classes (the policy file enumerates
them): outbound **email**, **external/social messages** (X, SMS, non-owner DMs),
**file deletion / force-push**, **financial transactions / purchases**,
**upstream third-party pushes** (`sonichi/*`, `gh pr merge`), and **reading
secrets into output**. Edit `policy.json` to tune what trips the gate and the
per-tier decision.

For routine, reversible, in-workspace actions the policy returns `allow` and you
proceed normally — the gate is not a tax on everyday work.

## The protocol (follow verbatim)

1. **Classify** the action you're about to take:
   ```bash
   python3 skills/exec-approval/scripts/check_policy.py --kind <kind> --command "<cmd>" --tier <owner|team|other>
   ```
   Exit code: `0`=allow, `10`=confirm, `20`=deny (also printed as JSON).

2. **Branch on the verdict:**
   - **allow** → proceed with the action normally.
   - **deny** → do NOT perform the action. Tell the requester it's blocked by policy. (Non-owner tiers are denied for every high-risk class.)
   - **confirm** → **do NOT perform the action yet.** Request approval:
     ```bash
     ID=$(python3 skills/exec-approval/scripts/request_approval.py \
            --kind <kind> --summary "<one-line what+why>" --command "<exact cmd>")
     ```
     This posts an approval card to `#approvals` and records a pending file at
     `<workspace>/state/approvals/<id>.json`. Then **stop that action** and move
     on / tell the owner it's awaiting their OK. Do not poll-block.

3. **On the owner's reply.** The owner approves by replying in `#approvals`
   (mentioning the bot so it becomes a task): `@Maverick approve <id>` or
   `@Maverick deny <id>`. When that task arrives, run:
   ```bash
   python3 skills/exec-approval/scripts/resolve_approval.py <id> approve   # or deny
   ```
   - On **approve**, the script prints the held action spec as JSON — now
     execute the action.
   - On **deny**, drop the action and confirm to the owner.
   - It's idempotent: a duplicate "approve" reply won't double-execute (a
     non-pending id reports its prior decision and returns the same spec).

4. **Stale-pending sweep (proactive loop).** Each pass may run
   `python3 skills/exec-approval/scripts/list_approvals.py` and re-surface
   anything pending for too long.

## Wiring

- `#approvals` channel id is read from `<workspace>/state/discord-config.json`
  → `channels.approvals`. If absent, requests are still recorded to disk and a
  warning is printed (fail loud, never silently skip the gate).
- Scripts are self-contained (no `src/` imports) and Python 3.9-safe.

## Honest scope (MVP)

Enforcement today is **protocol-level**: the gate works because the agent runs
`check_policy.py` before high-risk actions, per this file. It is not yet a hard
kernel-level interlock — a future hardening step is a Claude Code `PreToolUse`
hook that calls `check_policy.py` and blocks `confirm`/`deny` verdicts
automatically (tracked as the next iteration). The policy + request/resolve +
audit trail built here are the substrate that hook would enforce.

## Files

- `policy.json` — declarative rules (kind → patterns → per-tier decision).
- `scripts/check_policy.py` — classify an action.
- `scripts/request_approval.py` — create + post a pending approval (non-blocking).
- `scripts/resolve_approval.py` — approve/deny by id; prints the held spec.
- `scripts/list_approvals.py` — list pending/all.
- `state/approvals/<id>.json` — per-request audit record (gitignored runtime).
