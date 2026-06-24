#!/usr/bin/env python3
"""Frontier-scan mechanics: pull latest GitHub releases + tags for each tracked
agent framework, diff against state/seen.json, emit only NEW items as JSON.

Deterministic half of the skill (script does mechanics, agent does judgment —
mirrors skill-synth). The agent reads this output, writes a 1-line "what it is /
should we adopt it" take per item, handles the kind=web sources via WebSearch,
and delivers to #skills-dev. See scan-prompt.md.

Design notes:
- No auth needed: api.github.com public endpoints, low volume (≤ ~3 calls/repo).
  A GITHUB_TOKEN env var is used if present (raises rate limit) but never required.
- Fail-soft per source: a network error / rate-limit / 404 on one repo is recorded
  as a `skipped` note and does NOT abort the others (one dead repo must never make
  the whole weekly scan silent — that's the failure mode this skill exists to avoid).
- Idempotent: every emitted item is keyed and recorded in seen.json; a second run
  the same week emits nothing new. last_scan advances every run so the
  scan-catchup backstop can tell the scan is alive.
- Py3.9-safe (no PEP 604 unions, no datetime.UTC) — runs on either fleet interpreter.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent          # skills/frontier-scan/
SOURCES = HERE / "sources.json"
STATE = HERE / "state" / "seen.json"
UA = "sutando-frontier-scan/1.0 (+https://github.com/amkunte/sutando)"
TIMEOUT = 15


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_json(url):
    headers = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        headers["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _load_state():
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {"seen": {}, "last_scan": None, "scan_history": []}


def _save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2) + "\n")


def _fetch_github(repo):
    """Return (items, note). items = list of dicts for releases + latest tag."""
    items = []
    # Releases (preferred — carries notes + date).
    try:
        rels = _get_json("https://api.github.com/repos/%s/releases?per_page=5" % repo)
        for rel in rels:
            if rel.get("draft"):
                continue
            tag = rel.get("tag_name") or rel.get("name") or ""
            items.append({
                "key": "%s@release@%s" % (repo, tag),
                "type": "release",
                "title": rel.get("name") or tag,
                "tag": tag,
                "url": rel.get("html_url"),
                "published": rel.get("published_at") or rel.get("created_at"),
                "prerelease": bool(rel.get("prerelease")),
                "body": (rel.get("body") or "").strip()[:600],
            })
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return items, "releases HTTP %s" % e.code
    except Exception as e:
        return items, "%s: %s" % (type(e).__name__, str(e)[:80])

    # Tags fallback / supplement (repos that tag but don't cut GitHub releases).
    try:
        tags = _get_json("https://api.github.com/repos/%s/tags?per_page=3" % repo)
        for t in tags:
            name = t.get("name") or ""
            key = "%s@tag@%s" % (repo, name)
            if any(it["key"] == "%s@release@%s" % (repo, name) for it in items):
                continue  # already have it as a release
            items.append({
                "key": key,
                "type": "tag",
                "title": name,
                "tag": name,
                "url": "https://github.com/%s/releases/tag/%s" % (repo, name),
                "published": None,
                "prerelease": False,
                "body": "",
            })
    except Exception:
        pass  # tags are best-effort

    return items, None


def main():
    cfg = json.loads(SOURCES.read_text())
    state = _load_state()
    seen = state.get("seen", {})

    new_items = []
    skipped = []
    web_sources = []

    for src in cfg.get("sources", []):
        name = src.get("name", "?")
        if src.get("kind") == "github" and src.get("repo"):
            items, note = _fetch_github(src["repo"])
            if note:
                skipped.append({"source": name, "repo": src["repo"], "reason": note})
            for it in items:
                if it["key"] in seen:
                    continue
                it["source"] = name
                it["why_track"] = src.get("why_track", "")
                new_items.append(it)
                seen[it["key"]] = _now_iso()
        elif src.get("kind") == "web":
            # Left to the agent's WebSearch step — surface the query + rationale.
            web_sources.append({
                "source": name,
                "search": src.get("search", name),
                "why_track": src.get("why_track", ""),
            })

    # Advance state regardless of whether anything was new (keeps last_scan fresh
    # for the scan-catchup backstop, and records the run).
    state["seen"] = seen
    state["last_scan"] = _now_iso()
    hist = state.get("scan_history", [])
    hist.append({
        "ts": state["last_scan"],
        "new_count": len(new_items),
        "skipped": skipped,
    })
    state["scan_history"] = hist[-30:]
    _save_state(state)

    print(json.dumps({
        "last_scan": state["last_scan"],
        "new_items": new_items,
        "web_sources": web_sources,
        "skipped": skipped,
    }, indent=2))


if __name__ == "__main__":
    sys.exit(main())
