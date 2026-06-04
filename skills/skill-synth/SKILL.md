---
name: skill-synth
description: "Closed-loop skill synthesis — mine completed task history for repeatable, successful, not-yet-a-skill procedures, then draft a reusable skill from a chosen one for owner review. Sutando's answer to Hermes/Nous 'Jeepa' auto-skill capture. Script does the mechanics (rank candidates + bundle context); the agent does the judgment (write the SKILL.md); owner approves install."
user-invocable: true
---

# Skill Synth

Turns things Sutando *did well once* into things it can *do as a skill*. Inspired
by Nous Research's Hermes "Jeepa" auto-skill loop (video analysis 2026-06-04),
adapted to Sutando's idiom: **scripts do mechanics, the agent does judgment,
the owner approves.**

**Usage**: `/skill-synth` (scan for candidates, draft the top one for review)

## Why this split
Auto-drafting a *good* skill needs judgment, not a template. So the scripts only
do the deterministic parts (find candidates, bundle context); the agent writes
the actual procedure; nothing installs without owner review. No fragile
LLM-in-a-script dependency, fully testable.

## Flow

1. **Scan** — `python3 scripts/scan.py --text`
   Ranks completed tasks (current + archived) that look skill-worthy:
   - succeeded (non-empty, non-error result)
   - procedure-shaped (action verb: scan/deploy/generate/sync/…)
   - multi-step / substantive
   - **recurring** (same shape seen ≥2× → strong signal)
   - NOT already covered by an installed skill (dedup vs ~/.claude/skills + skills/)
   Output: ranked list with a per-candidate `why`. JSON by default, `--text` for humans.

2. **Synthesize (bundle)** — `python3 scripts/synthesize.py --from-task <id>`
   Writes `candidates/<slug>/brief.md` (task + result + drafting checklist) and a
   `candidates/<slug>/SKILL.md` scaffold full of `TODO(agent)` markers. Installs nothing.

3. **Draft (agent)** — the agent reads `brief.md` and fills every TODO in the
   candidate `SKILL.md`: real name/description, ordered concrete steps, idempotency
   notes, dedup-check. Removes the auto-captured Source block.

4. **Review + install (owner)** — owner reads the candidate; if approved:
   `cp -R candidates/<slug> ~/.claude/skills/<slug>` (drop the candidates/ scaffolding).
   Bots never auto-install — this stays owner-gated.

## Proactive-loop hook (optional)
A loop pass can run `scan.py` and, if a strong candidate exists (score ≥ 5) and
the owner is idle, run `synthesize.py` on it + draft the SKILL.md, then surface
"drafted a candidate skill for your review" — never installing. Keep it to one
candidate per pass to avoid noise.

## Scope / non-goals (v1)
- Ranks + bundles + agent-drafts. Does **not** auto-install, auto-commit, or
  run the synthesized skill.
- Candidate output under `candidates/` is gitignored (per-machine working area).
- Recurrence detection is heuristic (verb-shape); good enough to surface the
  obvious wins (the recurring cron-style tasks). Sharper clustering is a v2.
