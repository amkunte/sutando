#!/usr/bin/env python3
"""Guard: the owner table in skills/fleet-sync/SKILL.md must match fleet/manifest.json.

The manifest is the thing fleet_sync.py actually reads to decide which node pushes an item; the
SKILL.md table is documentation. When they disagree, the doc silently teaches the wrong thing —
and reading a doc is cheap, so the wrong answer propagates.

That is not hypothetical. On 2026-07-25 SKILL.md listed `karts-air | maverick` while the manifest
said `owner: goose`. Acting on the doc, I concluded three separate times that karts-air was
"orphaned on the wrong node" and reasoned about a 44-day gap that did not exist. The disconfirming
evidence (a `pushed=['karts-air']` from Goose, which only happens when owner == node) was sitting in
the logs the whole time and I read past it, because the doc had already told me the answer.

The manifest lives in the PRIVATE memory-sync repo, not this one, so this test SKIPS when it is not
present (CI, a fresh clone, any host without the private repo). It only asserts on a host that has
both — which is exactly where the drift can be observed and fixed.
"""
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SKILL_MD = REPO / "skills" / "fleet-sync" / "SKILL.md"

# The private clone's location is fixed by the fleet-sync skill (see reference_fleet_sync).
MANIFEST = Path(
    os.environ.get("SUTANDO_MEMORY_SYNC_DIR", Path.home() / ".sutando-memory-sync")
) / "fleet" / "manifest.json"


def main() -> int:
    if not SKILL_MD.is_file():
        print(f"SKIP: {SKILL_MD} not present")
        return 0
    if not MANIFEST.is_file():
        print(f"SKIP: private manifest not on this host ({MANIFEST})")
        return 0

    raw = json.loads(MANIFEST.read_text())
    items = raw if isinstance(raw, list) else raw.get("items", raw.get("skills", []))
    manifest_owner = {
        i["id"]: i.get("owner")
        for i in items
        if isinstance(i, dict) and i.get("id")
    }
    if not manifest_owner:
        print("SKIP: manifest has no id/owner entries to compare")
        return 0

    # Table rows look like: | karts-air | goose | personal-only skill code + criteria/state |
    row = re.compile(r"^\|\s*([A-Za-z0-9._-]+)\s*\|\s*([A-Za-z0-9._-]+)\s*\|")
    doc_owner = {}
    for line in SKILL_MD.read_text().splitlines():
        m = row.match(line.strip())
        if not m:
            continue
        item, owner = m.group(1), m.group(2)
        if item in manifest_owner:          # ignore header rows and unrelated tables
            doc_owner[item] = owner

    if not doc_owner:
        print("SKIP: no manifest items found in the SKILL.md table")
        return 0

    mismatches = [
        (item, owner, manifest_owner[item])
        for item, owner in sorted(doc_owner.items())
        if owner != manifest_owner[item]
    ]
    if mismatches:
        print("FAIL: SKILL.md owner table disagrees with fleet/manifest.json")
        for item, doc, real in mismatches:
            print(f"  {item}: SKILL.md says '{doc}', manifest says '{real}' (manifest wins)")
        print("\nThe manifest is what fleet_sync.py reads. Fix the doc, not the manifest,")
        print("unless you actually intend to move ownership between nodes.")
        return 1

    print(f"  ok  {len(doc_owner)} fleet item(s) — SKILL.md owners match the manifest")
    print(f"\nOK — fleet-sync SKILL.md owner table matches fleet/manifest.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
