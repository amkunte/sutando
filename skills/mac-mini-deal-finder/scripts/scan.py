#!/usr/bin/env python3
"""
Mac Mini deal finder — scrape Craigslist SF Bay, filter, dedupe, notify.

Run from cron (every 15 min) or manually:
    python3 scripts/scan.py [--dry-run] [--reset] [--verbose]

Notifies on the first sight of any listing matching criteria.json:
- SMS via Twilio REST (TWILIO_* + OWNER_NUMBER from .env)
- Telegram via results/proactive-{ts}.txt (telegram-bridge picks it up)
"""
import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
WORKSPACE = SKILL_DIR.parents[1]
STATE_DIR = SKILL_DIR / "state"
SEEN_PATH = STATE_DIR / "seen.json"
CRITERIA_PATH = STATE_DIR / "criteria.json"
RESULTS_DIR = WORKSPACE / "results"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Safari/605.1.15"
)
HDRS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://sfbay.craigslist.org/",
}


def load_env():
    env = {}
    env_path = WORKSPACE / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def http_get(url: str, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers=HDRS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def search_url(criteria: dict) -> str:
    qs = urllib.parse.urlencode({
        "query": criteria["search_query"],
        "max_price": criteria["max_price_usd"],
        "postal": criteria["zip"],
        "search_distance": criteria["search_radius_miles"],
        "hasPic": 1,
        "sort": "date",
    })
    return f"https://sfbay.craigslist.org/search/sss?{qs}"


# Craigslist's modern result page renders listings inside <li class="cl-static-search-result">
# with an inner <a> to the listing and <div class="title">/<div class="price">.
# This parser matches the current (2026) markup; if Craigslist changes layout,
# the regex below is the only thing to update.
LISTING_RE = re.compile(
    r'<li class="cl-static-search-result"[^>]*>.*?'
    r'<a href="(?P<url>https://sfbay\.craigslist\.org/[^"]+)"[^>]*>.*?'
    r'<div class="title">(?P<title>[^<]+)</div>.*?'
    r'(?:<div class="price">(?P<price>\$[\d,]+)</div>)?'
    r'(?:.*?<div class="location">(?P<loc>[^<]*)</div>)?'
    r'.*?</li>',
    re.DOTALL,
)


def parse_search_page(html_text: str) -> list[dict]:
    items = []
    for m in LISTING_RE.finditer(html_text):
        items.append({
            "url": m.group("url"),
            "title": html.unescape(m.group("title").strip()),
            "price_str": (m.group("price") or "").strip(),
            "location": html.unescape((m.group("loc") or "").strip()),
        })
    return items


def parse_price(price_str: str) -> int | None:
    if not price_str:
        return None
    digits = re.sub(r"[^\d]", "", price_str)
    return int(digits) if digits else None


# Apple chip family: word boundary on both sides, optional pro/max/ultra suffix.
# Whitelist single-digit M1–M9 only; reject things like 'M475' or 'M9340' (model numbers).
CHIP_RE = re.compile(r"(?<![A-Za-z0-9])M([1-9])(?:\s*(?:pro|max|ultra))?(?![A-Za-z0-9])", re.IGNORECASE)
RAM_RE = re.compile(r"(\d+)\s*(?:gb|gig|g)\s*(?:ram|memory|unified|of ram|of memory)?", re.IGNORECASE)
SSD_RE = re.compile(r"(\d+)\s*(?:gb|tb)\s*(?:ssd|nvme|storage|hard drive|hd|hdd|disk)", re.IGNORECASE)
TB_HINT = re.compile(r"(\d+(?:\.\d+)?)\s*tb\b", re.IGNORECASE)

# Reject obvious accessory / replacement-parts listings — even if the title says
# "mac mini", we don't want power cords, remotes, cables, adapters, hubs, replacement
# parts (covers, fans, antennas, screws, logic boards alone).
ACCESSORY_RE = re.compile(
    r"\b(power\s*cord|cord|cable|adapter|adaptor|dongle|hub|stand|case|cover|housing|"
    r"oem\b|replacement|antenna|cooling\s*fan|fan\b|speaker\b|screws?\b|logic\s*board|"
    r"power\s*supply|psu\b|"
    r"remote\s*control|remote\b|keyboard|mouse|trackpad|magic\s*(?:mouse|trackpad|keyboard)|"
    r"display\s*port|hdmi|usb-c|thunderbolt\s*cable|"
    r"ram\b|memory\s*stick|ddr[2-5]?\b|ssd\s*only|hard\s*drive\s*only|sata)\b",
    re.IGNORECASE,
)
# Mac minis on the M2+ have Apple Silicon — Intel-era listings (2010–2018) won't qualify.
INTEL_CHIP_RE = re.compile(r"\b(i[3-9])[\s-]*\d{2,4}", re.IGNORECASE)


def extract_chip(text: str) -> str | None:
    """Return the highest M-series number found, e.g. 'M2'. None if no match."""
    nums = [int(m.group(1)) for m in CHIP_RE.finditer(text)]
    if not nums:
        return None
    return f"M{max(nums)}"


def extract_ram_gb(text: str) -> int | None:
    """Find the most plausible RAM size mentioned. Heuristic: prefer matches near 'ram'/'memory'."""
    candidates = []
    for m in RAM_RE.finditer(text):
        n = int(m.group(1))
        # Reject silly values (1, 2 GB likely refers to something else; >256 likely SSD).
        if 4 <= n <= 256:
            candidates.append(n)
    if not candidates:
        return None
    # Prefer the lowest plausible value mentioned with "ram"/"memory" context — but parsing
    # explicit context per match is fragile, so fall back to max() of plausible candidates.
    return max(candidates)


def extract_storage_gb(text: str) -> int | None:
    """Find storage in GB. Recognise 'TB' → multiply."""
    # TB hint dominates if present
    tb = TB_HINT.search(text)
    if tb:
        try:
            return int(float(tb.group(1)) * 1024)
        except ValueError:
            pass
    candidates = []
    for m in SSD_RE.finditer(text):
        n = int(m.group(1))
        unit = m.group(0).lower()
        if "tb" in unit:
            n *= 1024
        if 64 <= n <= 16384:
            candidates.append(n)
    if not candidates:
        return None
    return max(candidates)


def passes(criteria: dict, listing: dict) -> tuple[bool, str]:
    """Return (matches, reason) for a parsed listing detail."""
    title = listing.get("title", "")
    body = listing.get("body", "")
    text = (title + " \n " + body).lower()
    title_l = title.lower()

    # Title MUST mention 'mac mini' — body-only mentions catch too many accessory
    # listings ("Mac mini Apple TV power cord" is a cord, not a Mac mini).
    if "mac mini" not in title_l and "macmini" not in title_l:
        return False, "title does not mention 'mac mini'"

    # Reject accessory-only listings even if title says "mac mini".
    if ACCESSORY_RE.search(title_l):
        return False, f"title looks like accessory: {ACCESSORY_RE.search(title_l).group(0)}"

    # Reject Intel-era Mac minis (the user wants M2+).
    if INTEL_CHIP_RE.search(text):
        return False, f"Intel chip detected ({INTEL_CHIP_RE.search(text).group(0)})"

    # Chip family MUST be explicit and in the wanted set. Soft-match only applies
    # to RAM/storage, not chip — a listing without a chip is too ambiguous to flag
    # given how much accessory noise exists on Craigslist.
    excluded = {c.lower() for c in criteria.get("exclude_chips", [])}
    chip = extract_chip(text)
    if chip is None:
        return False, "no chip family detected (require explicit M2/M3/M4)"
    chip_l = chip.lower()
    if chip_l in excluded:
        return False, f"excluded chip {chip}"
    wanted = {c.lower() for c in criteria.get("chips", [])}
    if wanted and chip_l not in wanted:
        return False, f"chip {chip} not in {sorted(wanted)}"
    soft = False

    ram = extract_ram_gb(text)
    if ram is None:
        soft = soft or criteria.get("soft_match_when_specs_missing", True)
    elif ram < criteria["min_ram_gb"]:
        return False, f"RAM {ram}GB < {criteria['min_ram_gb']}GB"

    storage = extract_storage_gb(text)
    if storage is None:
        soft = soft or criteria.get("soft_match_when_specs_missing", True)
    elif storage < criteria["min_storage_gb"]:
        return False, f"storage {storage}GB < {criteria['min_storage_gb']}GB"

    price = listing.get("price_int")
    if price is not None and price > criteria["max_price_usd"]:
        return False, f"price ${price} > ${criteria['max_price_usd']}"

    return True, ("soft match (specs missing — review manually)" if soft else "match")


def fetch_listing_body(url: str) -> str:
    """Best-effort fetch of the listing description text."""
    try:
        page = http_get(url, timeout=15)
    except Exception:
        return ""
    # The post body is in <section id="postingbody">…</section>.
    m = re.search(r'<section id="postingbody"[^>]*>(.*?)</section>', page, re.DOTALL)
    if not m:
        return ""
    body = re.sub(r"<[^>]+>", " ", m.group(1))
    body = html.unescape(body)
    body = re.sub(r"\s+", " ", body).strip()
    return body


def post_age_str(post_html: str, now: dt.datetime) -> str:
    m = re.search(r'<time[^>]+datetime="([^"]+)"', post_html)
    if not m:
        return "unknown"
    try:
        when = dt.datetime.fromisoformat(m.group(1))
    except ValueError:
        return "unknown"
    delta = now - when.replace(tzinfo=None)
    h = int(delta.total_seconds() // 3600)
    if h < 1:
        return f"{int(delta.total_seconds()//60)}m ago"
    if h < 48:
        return f"{h}h ago"
    return f"{h//24}d ago"


def send_sms(env: dict, body: str) -> tuple[bool, str]:
    sid = env.get("TWILIO_ACCOUNT_SID")
    token = env.get("TWILIO_AUTH_TOKEN")
    from_num = env.get("TWILIO_PHONE_NUMBER")
    to_num = env.get("OWNER_NUMBER", "").split(",")[0].strip()
    if not (sid and token and from_num and to_num):
        return False, "missing TWILIO_* / OWNER_NUMBER"
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = urllib.parse.urlencode({
        "From": from_num,
        "To": to_num,
        "Body": body[:1500],
    }).encode()
    req = urllib.request.Request(url, data=data)
    auth = (sid + ":" + token).encode()
    import base64
    req.add_header("Authorization", "Basic " + base64.b64encode(auth).decode())
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read())
            return True, payload.get("sid", "")
    except Exception as e:
        return False, str(e)


def send_telegram(body: str) -> bool:
    """Drop a proactive- file for the telegram-bridge to pick up."""
    try:
        RESULTS_DIR.mkdir(exist_ok=True)
        ts = int(time.time() * 1000)
        path = RESULTS_DIR / f"proactive-mac-mini-{ts}.txt"
        path.write_text(body)
        return True
    except Exception:
        return False


def format_message(listing: dict, match_reason: str, age: str) -> str:
    parts = [
        f"[Mac Mini Deal] {listing.get('price_str') or '?'} — {listing.get('title','?')}"
    ]
    loc = listing.get("location") or ""
    if loc:
        parts.append(f"Location: {loc}")
    parts.append(f"Posted: {age}")
    if match_reason and match_reason != "match":
        parts.append(f"Note: {match_reason}")
    parts.append(listing["url"])
    return "\n".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="don't notify, just print")
    ap.add_argument("--reset", action="store_true", help="clear seen.json before scan")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    env = load_env()
    criteria = json.loads(CRITERIA_PATH.read_text())

    if args.reset:
        SEEN_PATH.write_text(json.dumps({"urls": []}))
        print("Cleared seen.json")

    seen = set(json.loads(SEEN_PATH.read_text()).get("urls", []))
    url = search_url(criteria)
    if args.verbose:
        print(f"Fetching: {url}")
    try:
        page = http_get(url)
    except Exception as e:
        print(f"ERROR fetching search page: {e}", file=sys.stderr)
        sys.exit(1)

    listings = parse_search_page(page)
    if args.verbose:
        print(f"Found {len(listings)} listings on page")

    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    matches = 0
    new_urls = []
    for li in listings:
        if li["url"] in seen:
            continue
        new_urls.append(li["url"])
        # quick title-only price filter — but body fetch is needed for chip/RAM/storage
        li["price_int"] = parse_price(li["price_str"])
        if li["price_int"] is not None and li["price_int"] > criteria["max_price_usd"]:
            if args.verbose:
                print(f"  skip (price ${li['price_int']}): {li['title']}")
            continue
        # Listing detail
        body = fetch_listing_body(li["url"])
        li["body"] = body
        ok, reason = passes(criteria, li)
        if not ok:
            if args.verbose:
                print(f"  skip ({reason}): {li['title']}")
            continue
        # build age — fetch posting page once more is wasteful, reuse body? body call already fetched it.
        # We can't recover the full HTML for the time tag without re-fetch; keep "unknown" if missing.
        age = "recent"
        msg = format_message(li, reason, age)
        print("MATCH:")
        print(msg)
        print()
        matches += 1
        if not args.dry_run:
            ok_sms, sid_or_err = send_sms(env, msg)
            ok_tg = send_telegram(msg)
            print(f"  notify: sms={ok_sms} ({sid_or_err}) telegram_proactive={ok_tg}")

    if not args.dry_run and new_urls:
        seen.update(new_urls)
        # Trim to last 1000
        seen_list = list(seen)[-1000:]
        SEEN_PATH.write_text(json.dumps({"urls": seen_list}))

    print(f"Done. {len(listings)} scanned, {matches} matches, {len(new_urls)} newly seen.")


if __name__ == "__main__":
    main()
