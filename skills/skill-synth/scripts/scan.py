#!/usr/bin/env python3
"""skill-synth scan — surface skill-worthy task patterns from history.

Heuristic candidate = a task that (a) succeeded (has a non-empty, non-error
result), (b) looks like a repeatable *procedure* (action verbs / multi-step),
(c) is NOT already covered by an installed skill, and (d) ideally recurs
(same shape seen >=2x → strong signal it should be a skill).

Output: ranked JSON list (default) or --text for a human summary. This script
only RANKS; it never writes a skill. The agent decides + drafts.

Python 3.9-safe. Usage:
  python3 scan.py [--text] [--min-score 3] [--limit 10]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import resolve_workspace, iter_tasks, find_result, existing_skill_terms  # noqa: E402

# verbs that signal a repeatable procedure worth proceduralizing
PROC_VERBS = {
    "scan", "generate", "deploy", "build", "fetch", "parse", "sync", "ingest",
    "summarize", "track", "monitor", "review", "audit", "rebase", "publish",
    "render", "extract", "compile", "search", "draft", "post", "analyze",
    "check", "watch", "diff", "migrate", "backfill", "report",
}
ERROR_MARKERS = ("traceback", "error:", "exception", "❌", "failed", "no-send",
                 "[deduped", "[no-send]")


def words(text: str) -> set:
    return set(re.findall(r"[a-z]{3,}", text.lower()))


def score_task(t: dict, result: str, skill_terms: set, shape_counts: dict) -> dict:
    task = t.get("task", "") or ""
    tw = words(task)
    reasons = []
    score = 0

    # (a) success signal
    rlow = result.strip().lower()
    succeeded = bool(rlow) and not any(m in rlow for m in ERROR_MARKERS)
    if succeeded:
        score += 2
        reasons.append("succeeded")
    else:
        return {"score": 0, "reasons": ["no/failed result"], "skip": True}

    # (b) procedure-shaped: contains an action verb
    verbs = tw & PROC_VERBS
    # precision guard: a short task with no action verb is a continuation/ack
    # ("(b)", "go A", "ok..") — never skill-worthy no matter how often it recurs.
    if len(task) < 25 and not verbs:
        return {"score": 0, "reasons": ["trivial/continuation"], "skip": True}
    if verbs:
        score += 2
        reasons.append("procedural verb: " + ",".join(sorted(verbs)))
    # multi-step / substance: longer task + substantive result
    if len(task) > 60 and len(result) > 200:
        score += 1
        reasons.append("multi-step/substantive")

    # (c) not already a skill (penalize heavy overlap with existing skill terms)
    overlap = len(tw & skill_terms)
    if overlap >= 3:
        score -= 3
        reasons.append("overlaps existing skill (-3)")

    # (d) recurrence: same shape seen multiple times
    shape = "|".join(sorted(verbs)) or (sorted(tw)[0] if tw else "")
    n = shape_counts.get(shape, 0)
    if n >= 2:
        score += 2
        reasons.append("recurring pattern x%d" % n)

    return {"score": score, "reasons": reasons, "skip": False}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", action="store_true")
    ap.add_argument("--min-score", type=int, default=3)
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    ws = resolve_workspace()
    skill_terms = existing_skill_terms()
    tasks = iter_tasks(ws)

    # precompute shape recurrence
    shape_counts: dict = {}
    for t in tasks:
        tw = words(t.get("task", ""))
        verbs = tw & PROC_VERBS
        shape = "|".join(sorted(verbs)) or (sorted(tw)[0] if tw else "")
        shape_counts[shape] = shape_counts.get(shape, 0) + 1

    cands = []
    for t in tasks:
        result = find_result(ws, t["id"]) if t["id"] else ""
        sc = score_task(t, result, skill_terms, shape_counts)
        if sc["skip"] or sc["score"] < args.min_score:
            continue
        cands.append({
            "task_id": t["id"],
            "summary": (t.get("task", "")[:120]),
            "source": t.get("source", ""),
            "score": sc["score"],
            "reasons": sc["reasons"],
        })
    cands.sort(key=lambda c: c["score"], reverse=True)
    cands = cands[: args.limit]

    if args.text:
        if not cands:
            print("No skill-worthy candidates (min-score=%d)." % args.min_score)
            return 0
        print("Skill-synth candidates (top %d):" % len(cands))
        for c in cands:
            print("  [%d] task-%s — %s" % (c["score"], c["task_id"], c["summary"]))
            print("       why: " + "; ".join(c["reasons"]))
    else:
        print(json.dumps(cands, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
