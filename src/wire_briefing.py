#!/usr/bin/env python3
"""SutandoWIRE → daily-briefing hook.

Checks the SutandoWIRE YouTube playlist for the newest episode and, when it's
newer than the one we last surfaced, returns a one-line briefing snippet so the
morning briefing can announce it. Tracks the last-seen video in
``state/wire-briefing.json`` so each episode is announced exactly once.

Designed to be a clean no-op (returns "" / prints nothing, exit 0) whenever
``YOUTUBE_API_KEY`` is unavailable or the YouTube API errors — it must never
break the briefing.

Env:
  YOUTUBE_API_KEY   API key with YouTube Data API v3 access. Read from the
                    environment first, then the macOS-Keychain vault. Absent
                    on both → no-op.
  WIRE_PLAYLIST_ID  Optional; defaults to the canonical SutandoWIRE playlist.
  WIRE_ANNOUNCE_FIRST  If "1"/"true", announce the current newest episode on
                    the very first run (default: seed state silently so we only
                    announce episodes that appear *after* wiring this up).

CLI:
  python3 src/wire_briefing.py          # prints the briefing line (or nothing),
                                        # updating last-seen state
  python3 src/wire_briefing.py --peek   # same, but do NOT update state
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DEFAULT_PLAYLIST = "PLoEaHbP1bU5FDWAyeLDL9J9i7Iblp3_m_"
API_BASE = "https://www.googleapis.com/youtube/v3"


def _workspace() -> Path:
    ws = os.environ.get("SUTANDO_WORKSPACE")
    return Path(ws).expanduser() if ws else Path.home() / ".sutando" / "workspace"


def _state_path() -> Path:
    return _workspace() / "state" / "wire-briefing.json"


def _api_key() -> str | None:
    """YOUTUBE_API_KEY from env, falling back to the vault (Keychain)."""
    key = os.environ.get("YOUTUBE_API_KEY")
    if key:
        return key
    try:
        # vault_intercept lives in this same src/ dir.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from vault_intercept import get_vault_key  # type: ignore

        return get_vault_key("YOUTUBE_API_KEY")
    except Exception:
        return None


def _api_get(endpoint: str, params: dict) -> dict | None:
    key = _api_key()
    if not key:
        return None
    qs = urllib.parse.urlencode({**params, "key": key})
    url = f"{API_BASE}/{endpoint}?{qs}"
    with urllib.request.urlopen(url, timeout=15) as resp:  # noqa: S310 (https only)
        return json.loads(resp.read().decode("utf-8"))


def latest_episode() -> dict | None:
    """Newest non-private episode in the WIRE playlist.

    Returns ``{videoId, title, publishedAt, url}`` or ``None`` on any failure
    (including a missing API key).
    """
    playlist_id = os.environ.get("WIRE_PLAYLIST_ID", DEFAULT_PLAYLIST)
    videos: list[dict] = []
    # Paginate the whole playlist: playlistItems returns in *playlist order*
    # (curated WIRE playlist is oldest-first), so the newest episode can sit on
    # a later page once the list exceeds maxResults. Fetching every page and
    # sorting by publishedAt is the only way to reliably find the newest. The
    # page cap (10 * 50 = 500 items) is a runaway guard, far above any real size.
    page_token: str | None = None
    try:
        for _ in range(10):
            params = {
                "part": "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": 50,
            }
            if page_token:
                params["pageToken"] = page_token
            data = _api_get("playlistItems", params)
            if not data:
                break
            for item in data.get("items", []):
                cd = item.get("contentDetails", {})
                sn = item.get("snippet", {})
                vid = cd.get("videoId")
                title = (sn.get("title") or "").strip()
                if not vid or title in ("Private video", "Deleted video"):
                    continue
                published = cd.get("videoPublishedAt") or sn.get("publishedAt") or ""
                videos.append(
                    {
                        "videoId": vid,
                        "title": title,
                        "publishedAt": published,
                        "url": f"https://youtu.be/{vid}",
                    }
                )
            page_token = data.get("nextPageToken")
            if not page_token:
                break
    except Exception:
        # Partial pages could omit the true newest (later page) -> a stale
        # "newest" would mis-announce. Safer to no-op on any error, matching the
        # never-break-the-briefing contract.
        return None
    if not videos:
        return None
    videos.sort(key=lambda v: v["publishedAt"], reverse=True)
    return videos[0]


def _load_state() -> dict:
    try:
        return json.loads(_state_path().read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    p = _state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


def briefing_line(mark_seen: bool = True) -> str:
    """One-line briefing snippet for a *new* WIRE episode, else ``""``.

    "New" means the newest playlist episode differs from the last one we
    surfaced. On the very first run (no prior state) we seed state silently and
    return ``""`` — so we only announce episodes published *after* this hook was
    wired up — unless ``WIRE_ANNOUNCE_FIRST`` is set.
    """
    ep = latest_episode()
    if not ep:
        return ""

    state = _load_state()
    last_id = state.get("last_video_id")
    if ep["videoId"] == last_id:
        return ""

    announce_first = os.environ.get("WIRE_ANNOUNCE_FIRST", "").lower() in ("1", "true", "yes", "on")
    silent_seed = last_id is None and not announce_first

    if mark_seen:
        _save_state(
            {
                "last_video_id": ep["videoId"],
                "last_title": ep["title"],
                "last_published": ep["publishedAt"],
            }
        )

    if silent_seed:
        return ""
    return f"📺 New SutandoWIRE: {ep['title']} — {ep['url']}"


if __name__ == "__main__":
    line = briefing_line(mark_seen="--peek" not in sys.argv)
    if line:
        print(line)
