#!/usr/bin/env python3
"""Retention policy for a proactive result no bridge can deliver yet.

"Cannot deliver yet" is not "discard". Every bridge claims a `results/proactive-*`
(and briefing-/insight-/friction-) file by renaming it to `.sending`, then resolves
an owner to DM. When no owner resolves — TOFU never completed, `allowFrom` empty,
`SUTANDO_DM_OWNER_ID` unset — all three adapters unlinked the claim, so the file was
destroyed with no archive and no retry. Measured on a live host: three consecutive
morning briefings plus the daily insights went this way.

Shared because the rule is identical in every adapter: telegram, slack and discord
each resolved an owner and each deleted on failure. Adapters inject their resolved
directories and keep their own logging; the claim/restore mechanics live here.
"""
from __future__ import annotations

from pathlib import Path

#: Sub-directory of `results/` holding claims that could not be delivered. Outside
#: the bridges' `results/*.txt` poll glob, so a retained file is never re-claimed in
#: a 2s spin, and obvious to a human looking for a briefing that never arrived.
UNDELIVERED_DIRNAME = "undelivered"


def retain_dir_for(results_dir: Path) -> Path:
    """The retention directory a bridge should pass to retain_undeliverable()."""
    return Path(results_dir) / UNDELIVERED_DIRNAME


def retain_undeliverable(claim: Path, retain_dir: Path) -> Path | None:
    """Move a `.sending` claim into `retain_dir`, restoring its `.txt` name.

    Returns the destination, or None if the claim is gone or the move failed —
    callers must treat None as "not retained" and must NOT fall back to unlinking.
    Never overwrites: a name already taken gets a `-1`, `-2`, ... suffix, so two
    briefings claimed on the same day both survive.
    """
    claim = Path(claim)
    retain_dir = Path(retain_dir)
    try:
        retain_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    # `.sending` is the claim marker, not part of the identity — restore `.txt` so
    # the retained file is readable by the same tools that read a live result.
    name = claim.name
    if name.endswith(".sending"):
        name = name[: -len(".sending")] + ".txt"

    dest = retain_dir / name
    if dest.exists():
        stem, suffix = dest.stem, dest.suffix
        n = 1
        while dest.exists():
            dest = retain_dir / f"{stem}-{n}{suffix}"
            n += 1

    try:
        claim.rename(dest)
    except OSError:
        return None
    return dest
