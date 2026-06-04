#!/usr/bin/env python3
"""skill-synth synthesize — bundle a chosen task into a synthesis BRIEF + a
SKILL.md scaffold under candidates/<slug>/, for the agent to fill in.

This is the deterministic *mechanics* half (gather context, slug, scaffold).
The *judgment* half — writing the actual procedure into SKILL.md — is done by
the agent (Claude) reading brief.md. Nothing is installed; the agent reviews,
then the owner approves and copies into ~/.claude/skills/.

Python 3.9-safe. Usage:
  python3 synthesize.py --from-task <id> [--name <slug>]
  python3 synthesize.py --from-text "<procedure desc>" --name <slug>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import resolve_workspace, parse_task_file, find_result, slugify  # noqa: E402

CAND_DIR = Path(__file__).resolve().parent.parent / "candidates"

SKILL_SCAFFOLD = """---
name: {slug}
description: "TODO(agent): one-paragraph description of what this skill does and when to use it — mirror the source task's intent."
user-invocable: true
---

# {title}

TODO(agent): 1-2 sentence summary.

**Usage**: `/{slug}`

## Procedure
TODO(agent): write the step-by-step procedure distilled from the source task +
result below. Keep steps concrete and ordered. Note any inputs, side effects,
and idempotency guards. If the task used specific tools/commands, name them.

## Source (auto-captured by skill-synth — delete before shipping)
- task-id: {task_id}
- source: {source}
"""

BRIEF = """# Synthesis brief: {slug}

skill-synth captured this candidate. AGENT: read the task + result below, then
fill in candidates/{slug}/SKILL.md (replace every TODO). Keep it tight; match
the format of other skills/. Do NOT install — leave for owner review.

## Source task (task-{task_id}, source={source})
{task}

## What happened (result)
{result}

## Drafting checklist
- [ ] name + description frontmatter reflect the real procedure
- [ ] steps are ordered, concrete, name real tools/paths
- [ ] note idempotency / side effects / "stay silent if nothing changed" if applicable
- [ ] remove the auto-captured Source block before shipping
- [ ] dedup-check: not already covered by an existing skill

## After drafting
Owner review, then install:
  cp -R candidates/{slug} ~/.claude/skills/{slug}   # (drop candidates/ scaffolding)
"""


def write_candidate(slug: str, task_id: str, source: str, task: str, result: str) -> Path:
    d = CAND_DIR / slug
    (d / "scripts").mkdir(parents=True, exist_ok=True)
    title = slug.replace("-", " ").title()
    (d / "SKILL.md").write_text(SKILL_SCAFFOLD.format(
        slug=slug, title=title, task_id=task_id or "(n/a)", source=source or "(n/a)"))
    (d / "brief.md").write_text(BRIEF.format(
        slug=slug, task_id=task_id or "(n/a)", source=source or "(n/a)",
        task=task or "(none)", result=result or "(none)"))
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-task")
    ap.add_argument("--from-text")
    ap.add_argument("--name")
    args = ap.parse_args()

    ws = resolve_workspace()

    if args.from_task:
        # locate the task file across task dirs
        from common import iter_tasks
        match = next((t for t in iter_tasks(ws) if t["id"] == args.from_task), None)
        if not match:
            print("task-%s not found in workspace task dirs" % args.from_task, file=sys.stderr)
            return 1
        task = match.get("task", "")
        source = match.get("source", "")
        result = find_result(ws, args.from_task)
        slug = args.name or slugify(task)
        d = write_candidate(slug, args.from_task, source, task, result)
    elif args.from_text:
        if not args.name:
            print("--from-text requires --name", file=sys.stderr)
            return 1
        slug = args.name
        d = write_candidate(slug, "", "manual", args.from_text, "")
    else:
        print("need --from-task <id> or --from-text <desc> --name <slug>", file=sys.stderr)
        return 1

    print("candidate scaffolded: %s" % d)
    print("  - %s/brief.md   (agent: read this)" % d)
    print("  - %s/SKILL.md   (agent: fill the TODOs)" % d)
    print("NOT installed. After drafting + owner review: cp -R %s ~/.claude/skills/%s" % (d, slug))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
