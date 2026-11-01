"""Pre-market press releases from FMP (/news/press-releases-latest).

Companies often put news out on the newswires (Business Wire, PR Newswire, GlobeNewswire) from 4:00 am Eastern,
before the matching 8-K reaches EDGAR at 6:00 am. This runs in its own thread so it can start at PR_START while
the main SEC loop is still asleep, and polls FMP every PR_POLL_SECONDS on open days until ACTIVE_END.

PR_MODE=log (the default) stores every release with lag_sec = first seen here - FMP's published time, and logs it,
so you can judge how fresh the feed is before turning on alerts. PR_MODE=alert also sends releases whose tags
are in PR_ALERT_TAGS to Telegram (capped by PR_MAX_ALERTS_PER_DAY, respecting /pause).

The first poll after an empty table only records a baseline, and releases already older than PR_MAX_AGE_MIN
when first seen are stored as 'stale': neither is ever alerted or counted in the timing stats.
"""
import re
import json
import html
import time
import logging
import threading
import datetime as dt
from statistics import median
from zoneinfo import ZoneInfo

import requests

from . import config as C, schedule, telegram
from .db import DB
from .events import FDA_NEG, FDA_APPROVAL, FDA_PLANNED, FDA_MILESTONE, GOV, AWARD, MERGER, TAGS, snippet

log = logging.getLogger("pr")
_s = requests.Session()

GOV_RX = re.compile(r"(?:" + GOV.pattern + r")[^.]{0,200}(?:" + AWARD.pattern + r")|(?:" + AWARD.pattern
                    + r")[^.]{0,200}(?:" + GOV.pattern + ")", re.I)
MERGER_HEAD = re.compile(r"\b(?:to\s+acquire|to\s+be\s+acquired|definitive\s+(?:merger\s+)?agreement|to\s+merge|"
                         r"merger\s+agreement|agrees?\s+to\s+(?:acquire|be\s+acquired|sell)|tender\s+offer|"
                         r"take[-\s]private)\b", re.I)
ACQ_DONE = re.compile(r"\bcomplet\w*\s+(?:the\s+|its\s+)?(?:acquisition|merger|sale|divestiture)", re.I)
EXEC_HEAD = re.compile(r"\b(?:appoint\w*|names?|named|promot\w*|resign\w*|retire\w*|depart\w*|steps?\s+down|"
                       r"success\w*)\b[^.]{0,80}\b(?:CEO|CFO|Chief\s+(?:Executive|Financial)\s+Officer)\b|"
                       r"\b(?:CEO|CFO|Chief\s+(?:Executive|Financial)\s+Officer)\b[^.]{0,60}\b(?:resign\w*|"
                       r"retire\w*|depart\w*|steps?\s+down|transition)", re.I)
BANKRUPT = re.compile(r"chapter\s+1[15]|bankruptcy|receivership", re.I)
EARNINGS = re.compile(r"\b(?:report\w*|announc\w*|releas\w*)\b[^.]{0,60}\b(?:quarter|Q[1-4]|fiscal|full[-\s]year)\b"
                      r"[^.]{0,60}\bresults\b|\b(?:first|second|third|fourth)\s+quarter[^.]{0,40}\bresults\b", re.I)


def tag(title, text):
    """Pure function: (headline, body) -> [(tag, snippet)] using the 8-K vocabulary in events.TAGS."""
    head, body = title or "", (text or "")[:4000]
    both = head + ". " + body
    tags = []

    def add(t, rx, src):
        if t not in (x for x, _ in tags):
            tags.append((t, snippet(src, rx) if rx else ""))

    if FDA_NEG.search(both):
        add("fda_negative", FDA_NEG, both)
    elif FDA_APPROVAL.search(both) and not FDA_PLANNED.search(snippet(both, FDA_APPROVAL)):
        add("fda_approval", FDA_APPROVAL, both)
    elif FDA_MILESTONE.search(both):
        add("fda_milestone", FDA_MILESTONE, both)
    if GOV_RX.search(both):
        add("gov_contract", GOV_RX, both)
    if ACQ_DONE.search(head):
        add("acquisition", ACQ_DONE, head)
    elif MERGER_HEAD.search(head) or MERGER.search(body[:1500]):
        add("merger", MERGER_HEAD if MERGER_HEAD.search(head) else MERGER, head if MERGER_HEAD.search(head) else body)
    if EXEC_HEAD.search(head):
        add("exec_change", EXEC_HEAD, head)
    if BANKRUPT.search(head):
        add("bankruptcy", BANKRUPT, head)
    if EARNINGS.search(head):
        add("earnings", None, head)
    return tags


def _key(it):
    return it.get("url") or f"{it.get('symbol', '')}|{it.get('publishedDate', '')}|{it.get('title', '')}"


def published_ts(s):
    """FMP 'YYYY-MM-DD HH:MM:SS' (zone PR_TIME_TZ) -> epoch seconds, or None."""
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=ZoneInfo(C.PR_TIME_TZ))
    return d.timestamp()


_warned = {}


def _warn_once(key, msg, *args):
    """A failing feed warns at most once an hour instead of every poll."""
    if time.time() - _warned.get(key, 0) > 3600:
        _warned[key] = time.time()
        log.warning(msg, *args)


def fetch():
    """Newest press releases as a list of dicts, or None if FMP refused or failed."""
    try:
        r = _s.get(f"{C.FMP_BASE_URL}/news/press-releases-latest",
                   params={"page": 0, "limit": C.PR_FETCH_LIMIT, "apikey": C.FMP_API_KEY}, timeout=20)
    except requests.RequestException as e:
        _warn_once("net", "press releases: FMP request failed: %s", e)
        return None
    if r.status_code in (401, 402, 403):
        _warn_once("plan", "press releases: FMP refused the request (HTTP %s). Your FMP plan may not include "
                   "press releases - check FMP_API_KEY and your plan. Body: %s", r.status_code, r.text[:200])
        return None
    if r.status_code != 200:
        _warn_once("http", "press releases: FMP HTTP %s: %s", r.status_code, r.text[:200])
        return None
    try:
        data = r.json()
    except ValueError:
        _warn_once("json", "press releases: FMP returned non-JSON: %s", r.text[:200])
        return None
    if isinstance(data, dict):
        _warn_once("plan", "press releases: FMP error (your plan may not include this endpoint): %s", str(data)[:200])
        return None
    return [it for it in data if isinstance(it, dict)]


def format_alert(r, tags):
    labels = ", ".join(TAGS[t][0] for t, _ in tags if t in TAGS) or "Press release"
    sym = html.escape(r["symbol"] or "?")
    lag = f" · seen {r['lag_sec'] / 60:.0f} min after release" if r["lag_sec"] is not None else ""
    lines = [f"📰 <b>PRESS RELEASE — {sym}</b> · {html.escape(labels)}",
             html.escape(r["title"][:300]),
             f"{html.escape(r['published'] or '?')} ET · {html.escape(r['site'] or '')}{lag}"]
    snip = next((s for _, s in tags if s), "")
    if snip:
        lines.append("“" + html.escape(snip[:300]) + "”")
    if r["url"]:
        lines.append(f'<a href="{html.escape(r["url"], quote=True)}">Read the release</a>')
    lines.append("<i>Newswire release; the 8-K, if any, follows on EDGAR.</i>")
    return "\n".join(lines)


def scan(db, items=None, now=None, send=None):
    """Store new releases; in alert mode send tagged ones. Returns the list of new (non-baseline) rows."""
    items = fetch() if items is None else items
    if items is None:
        return []
    now = now or time.time()
    send = send or (lambda text: telegram.send(text, chat_id=C.EVENTS_CHAT_ID or None))
    baseline = db.pr_count() == 0
    want = {t.strip() for t in C.PR_ALERT_TAGS.split(",") if t.strip()}
    day_start = schedule.now_local(dt.datetime.fromtimestamp(now, dt.timezone.utc)).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    new = []
    for it in sorted(items, key=lambda x: str(x.get("publishedDate", ""))):
        key = _key(it)
        if not key or db.pr_seen(key):
            continue
        pub = published_ts(it.get("publishedDate"))
        lag = round(now - pub, 1) if pub else None
        tags = tag(it.get("title", ""), it.get("text", ""))
        r = {"key": key, "symbol": (it.get("symbol") or "").upper(), "published": str(it.get("publishedDate") or ""),
             "title": (it.get("title") or "").strip(), "site": it.get("site") or it.get("publisher") or "",
             "url": it.get("url") or "", "tags": json.dumps([t for t, _ in tags]), "first_seen": now,
             "lag_sec": lag, "status": "new", "alerted_at": None}
        if baseline:
            r["status"] = "baseline"
        elif lag is not None and lag > C.PR_MAX_AGE_MIN * 60:
            r["status"] = "stale"
        elif C.PR_MODE == "alert" and want & {t for t, _ in tags} and r["symbol"]:
            if db.get_kv("paused") == "1":
                r["status"] = "paused"
            elif C.PR_MAX_ALERTS_PER_DAY and db.pr_alerts_since(day_start) >= C.PR_MAX_ALERTS_PER_DAY:
                r["status"] = "capped"
            else:
                try:
                    send(format_alert(r, tags))
                    r["status"], r["alerted_at"] = "alerted", now
                except Exception as e:
                    log.warning("press release alert for %s failed: %s", r["symbol"], e)
        db.pr_add(r)
        if r["status"] not in ("baseline", "stale"):
            new.append(r)
            log.info("PR %-6s %-8s lag=%s tags=%s: %s", r["symbol"] or "-", r["status"],
                     f"{lag:.0f}s" if lag is not None else "?", ",".join(t for t, _ in tags) or "-", r["title"][:120])
    if baseline and items:
        log.info("press releases: first poll stored %d existing release(s) as a baseline (not alerted)", len(items))
    return new


def summary(db, since_ts):
    """One line for /status: today's new releases and how fresh FMP delivered them."""
    rows = db.pr_since(since_ts)
    lags = [r["lag_sec"] for r in rows if r["lag_sec"] is not None and r["lag_sec"] >= 0]
    line = f"Press releases ({C.PR_MODE}): {len(rows)} new today"
    if lags:
        line += f" · lag median {median(lags) / 60:.1f} min, best {min(lags) / 60:.1f} min"
    alerted = sum(1 for r in rows if r["status"] == "alerted")
    if C.PR_MODE == "alert":
        line += f" · {alerted} alerted"
    return line


def _run():
    db = DB(C.DATA_DIR)
    while True:
        try:
            if C.ENFORCE_SCHEDULE and not schedule.is_active(start=C.PR_START):
                time.sleep(min(schedule.seconds_until_active(start=C.PR_START) + 5, 3600))
                continue
            scan(db)
        except Exception:
            log.exception("press release scan failed")
        time.sleep(C.PR_POLL_SECONDS)


def enabled():
    return C.PR_MODE != "off" and bool(C.FMP_API_KEY)


def start():
    if C.PR_MODE == "off":
        return
    if not C.FMP_API_KEY:
        log.warning("PR_MODE=%s but FMP_API_KEY is not set - press release scan is OFF", C.PR_MODE)
        return
    threading.Thread(target=_run, name="press-releases", daemon=True).start()
    log.info("press releases: mode=%s · FMP every %ss · %s-%s %s on open days", C.PR_MODE, C.PR_POLL_SECONDS,
             C.PR_START, C.ACTIVE_END, C.SCHEDULE_TZ)
