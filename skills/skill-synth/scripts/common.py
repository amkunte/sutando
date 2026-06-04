"""Shared helpers for skill-synth: workspace resolution, task/result pairing,
and an index of already-installed skills (for dedup). Python 3.9-safe."""
from __future__ import annotations

import os
import re
from pathlib import Path

# --- workspace + repo roots -------------------------------------------------

def resolve_workspace() -> Path:
    ws = os.environ.get("SUTANDO_WORKSPACE", "")
    if ws:
        return Path(os.path.expanduser(ws))
    return Path.home() / ".sutando" / "workspace"


def repo_root() -> Path:
    # this file: <repo>/skills/skill-synth/scripts/common.py
    return Path(__file__).resolve().parents[3]


# --- task / result pairing --------------------------------------------------

# A "task" file is task-<id>.txt with `key: value` metadata lines and a `task:`
# line carrying the request. Its result is results/task-<id>.txt (free text).
TASK_RE = re.compile(r"^task-(?P<id>\d+)\.txt$")


def _task_dirs(ws: Path) -> list:
    base = ws / "tasks"
    dirs = [base]
    if base.exists():
        dirs += sorted(p for p in base.glob("archive*") if p.is_dir())
    # also the workspace-root archive/ some versions used
    root_arch = ws / "archive"
    if root_arch.exists():
        dirs.append(root_arch)
    return [d for d in dirs if d.exists()]


def _result_dirs(ws: Path) -> list:
    base = ws / "results"
    dirs = [base]
    if base.exists():
        dirs += sorted(p for p in base.glob("archive*") if p.is_dir())
    return [d for d in dirs if d.exists()]


def parse_task_file(path: Path) -> dict:
    """Parse a task-<id>.txt into {id, meta..., task}."""
    out = {"id": None, "task": "", "source": "", "path": str(path)}
    m = TASK_RE.match(path.name)
    if m:
        out["id"] = m.group("id")
    try:
        for line in path.read_text(errors="replace").splitlines():
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            k = k.strip().lower()
            v = v.strip()
            if k == "id":
                # The bridge writes `id: task-<ts>`; the filename regex already
                # captured the canonical numeric id. Don't let the prefixed
                # metadata value clobber it (that produced `task-task-<ts>` and
                # broke result pairing). Only use metadata id as a fallback,
                # stripping any leading "task-".
                if not out["id"]:
                    out["id"] = v[5:] if v.startswith("task-") else v
            elif k in ("source", "channel_name", "priority", "access_tier"):
                out[k] = v
            elif k == "task":
                out["task"] = v
    except OSError:
        pass
    return out


def find_result(ws: Path, task_id: str) -> str:
    for d in _result_dirs(ws):
        f = d / f"task-{task_id}.txt"
        if f.exists():
            try:
                return f.read_text(errors="replace")
            except OSError:
                return ""
    return ""


def iter_tasks(ws: Path) -> list:
    """All task files across current + archive dirs, newest first, deduped by id."""
    seen = set()
    items = []
    files = []
    for d in _task_dirs(ws):
        files += [p for p in d.glob("task-*.txt") if TASK_RE.match(p.name)]

    def _mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime  # TOCTOU-safe: file may vanish mid-scan
        except OSError:
            return 0.0
    files.sort(key=_mtime, reverse=True)
    for p in files:
        t = parse_task_file(p)
        if not t["id"] or t["id"] in seen:
            continue
        seen.add(t["id"])
        items.append(t)
    return items


# --- installed-skill index (for dedup) -------------------------------------

def existing_skill_terms() -> set:
    """Collect skill names + description words from installed + repo skills."""
    terms = set()
    roots = [Path.home() / ".claude" / "skills", repo_root() / "skills"]
    for root in roots:
        if not root.exists():
            continue
        for sk in root.iterdir():
            if not sk.is_dir():
                continue
            terms.add(sk.name.lower())
            md = sk / "SKILL.md"
            if md.exists():
                try:
                    head = md.read_text(errors="replace")[:600].lower()
                    terms.update(re.findall(r"[a-z]{4,}", head))
                except OSError:
                    pass
    return terms


def slugify(text: str, maxwords: int = 5) -> str:
    words = re.findall(r"[a-z0-9]+", text.lower())
    stop = {"the", "a", "an", "this", "that", "and", "for", "with", "from",
            "via", "let", "lets", "can", "you", "please", "discord", "abhishares"}
    words = [w for w in words if w not in stop][:maxwords]
    return "-".join(words) or "candidate"
