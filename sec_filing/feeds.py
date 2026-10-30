"""Non-SEC feeds: government contract awards and FDA approvals.

- dod:         Department of Defense daily contract announcements (every award >= $7.5M, posted ~5pm ET each
               business day). Fastest public source for defense awards.
- usaspending: USAspending.gov awards API for civilian agencies (NASA, DHS, HHS/BARDA, VA, DOE ...). DoD awards
               are skipped here because DoD reports them to USAspending with a 90-day delay (dod covers them).
- openfda:     Drugs@FDA via api.fda.gov - original NDA/BLA approvals (generics/ANDAs and supplements skipped).
               Updated with a lag of days, so it mostly *confirms* approvals the 8-K scan caught first.

Each source returns records {source, key, date, company, agency, amount, detail, link}; process() matches the
company to a ticker, applies the gates, stores everything and returns what should alert.
A source that fails logs a warning and returns [] - it never breaks the main loop.
"""
import re
import json
import html
import logging
import datetime as dt
import requests

from . import config as C, names
from .analysis import clean_text
from .events import amounts

log = logging.getLogger("feeds")
_s = requests.Session()


def _get(url, **kw):
    try:
        r = _s.get(url, timeout=30, headers={"User-Agent": C.WEB_USER_AGENT}, **kw)
        if r.status_code == 200:
            return r.text
        log.warning("HTTP %s for %s", r.status_code, url)
    except requests.RequestException as e:
        log.warning("request error %s: %s", url, e)
    return None


# ------------------------------------------------------------------ DoD daily contracts

# Article links, absolute or relative, on defense.gov or war.gov ("/News/..." before the 2025 rename, "/Newsroom/..." now)
DOD_ARTICLE = re.compile(r"(?:https?://www\.(?:defense|war)\.gov)?/News(?:room)?/Contracts/Contract/Article/(\d+)/?", re.I)
P_TAG = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)
AWARDEE = re.compile(
    r"^(?P<name>[A-Z0-9][^,]{1,120}?(?:,\s*(?:Inc|LLC|L\.L\.C|Corp|Co|Ltd|L\.P|LP|LLP|Corporation|Company|PLC)\.?)?),"
    r"\s+(?P<loc>[^$]{2,120}?),\s+(?:is|was|has\s+been|have\s+been|were|are|has)\s+(?:being\s+)?"
    r"(?:awarded|issued|selected|given|granted|received|won)\b")
MULTI = re.compile(r"multiple[-\s]award|each\s+(?:been\s+)?awarded|following\s+(?:\w+\s+)?(?:companies|firms|contractors)|"
                   r"(?:companies|firms|contractors)\s+(?:were|have\s+been|are)\s+awarded", re.I)
AGENCY_HDR = re.compile(r"^[A-Z][A-Z &.,'\-/]{2,60}$")


def parse_dod_article(page_html):
    """[(agency, company, amount, multi_award, paragraph)] from one 'Contracts For <date>' page."""
    out, agency = [], ""
    for raw in P_TAG.findall(page_html or ""):
        txt = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", raw))).strip()
        if not txt:
            continue
        if AGENCY_HDR.match(txt) and not txt.startswith("*"):
            agency = txt.title()
            continue
        m = AWARDEE.match(txt)
        if not m:
            continue
        amt = amounts(txt)
        out.append((agency, m.group("name").strip(), amt[0] if amt else 0.0, bool(MULTI.search(txt)), txt))
    return out


def dod(today):
    listing = _get(C.DOD_CONTRACTS_URL)
    if not listing:
        return []
    base = re.match(r"https?://[^/]+", C.DOD_CONTRACTS_URL).group(0)
    ids = []
    for m in DOD_ARTICLE.finditer(listing):
        if m.group(1) not in [i for i, _ in ids]:
            url = m.group(0) if m.group(0).startswith("http") else base + m.group(0)
            ids.append((m.group(1), url.rstrip("/") + "/"))
    if not ids:
        log.warning("DoD: no contract articles found on %s - the page format may have changed", C.DOD_CONTRACTS_URL)
        return []
    recs = []
    for art_id, url in ids[:C.DOD_MAX_ARTICLES]:
        page = _get(url)
        if not page:
            continue
        t = re.search(r"Contracts\s+For\s+([A-Z][a-z]+\.?\s+\d{1,2},\s+\d{4})", page, re.I)
        date = _parse_date(t.group(1)) if t else today.isoformat()
        for n, (agency, company, amt, multi, txt) in enumerate(parse_dod_article(page)):
            recs.append({"source": "dod", "key": f"dod:{art_id}:{n}", "date": date, "company": company,
                         "agency": agency or "DoD", "amount": amt, "multi": multi,
                         "detail": txt[:400], "link": url})
    log.info("DoD: %d article(s), %d award paragraph(s)", min(len(ids), C.DOD_MAX_ARTICLES), len(recs))
    return recs


def _parse_date(s):
    for fmt in ("%b. %d, %Y", "%b %d, %Y", "%B %d, %Y", "%B. %d, %Y"):
        try:
            return dt.datetime.strptime(s.replace("Sept.", "Sep."), fmt).date().isoformat()
        except ValueError:
            pass
    return dt.date.today().isoformat()


# ------------------------------------------------------------------ USAspending (civilian agencies)

USA_FIELDS = ["Award ID", "Recipient Name", "Award Amount", "Awarding Agency", "Awarding Sub Agency",
              "Start Date", "Description"]


def usaspending(today):
    start = (today - dt.timedelta(days=C.USASPENDING_LOOKBACK_DAYS)).isoformat()
    body = {"filters": {"award_type_codes": ["A", "B", "C", "D"],
                        "time_period": [{"start_date": start, "end_date": today.isoformat()}],
                        "award_amounts": [{"lower_bound": C.CONTRACT_MIN_USD}]},
            "fields": USA_FIELDS, "sort": "Award Amount", "order": "desc", "limit": 100, "page": 1,
            "subawards": False}
    try:
        r = _s.post("https://api.usaspending.gov/api/v2/search/spending_by_award/", json=body, timeout=60,
                    headers={"User-Agent": C.WEB_USER_AGENT})
        if r.status_code != 200:
            log.warning("USAspending HTTP %s: %s", r.status_code, r.text[:200])
            return []
        rows = r.json().get("results", [])
    except (requests.RequestException, ValueError) as e:
        log.warning("USAspending failed: %s", e)
        return []
    recs, new_since = [], (today - dt.timedelta(days=C.USASPENDING_NEW_AWARD_DAYS)).isoformat()
    for x in rows:
        agency = x.get("Awarding Agency") or ""
        if re.search(r"defense|army|navy|air force", agency, re.I):
            continue                                  # DoD -> 90-day reporting delay; the dod source covers it
        if (x.get("Start Date") or "") < new_since:
            continue                                  # an old award that only got a modification
        gid = x.get("generated_internal_id") or x.get("Award ID")
        recs.append({"source": "usaspending", "key": f"usa:{gid}", "date": x.get("Start Date") or today.isoformat(),
                     "company": x.get("Recipient Name") or "", "agency": agency, "multi": False,
                     "amount": float(x.get("Award Amount") or 0),
                     "detail": f"{x.get('Awarding Sub Agency') or agency}: {(x.get('Description') or '')[:300]}",
                     "link": f"https://www.usaspending.gov/award/{gid}" if gid else ""})
    return recs


# ------------------------------------------------------------------ openFDA approvals

def openfda(today):
    start = (today - dt.timedelta(days=C.FDA_LOOKBACK_DAYS)).strftime("%Y%m%d")
    q = (f"submissions.submission_status_date:[{start}+TO+{today:%Y%m%d}]"
         "+AND+submissions.submission_type:ORIG+AND+submissions.submission_status:AP")
    txt = _get(f"https://api.fda.gov/drug/drugsfda.json?search={q}&limit=100")
    if not txt:
        return []
    try:
        rows = json.loads(txt).get("results", [])
    except ValueError:
        return []
    recs = []
    for r in rows:
        app = r.get("application_number") or ""
        if not app.startswith(("NDA", "BLA")):
            continue                                  # ANDA = generics
        for s in r.get("submissions") or []:
            d = s.get("submission_status_date") or ""
            if s.get("submission_type") == "ORIG" and s.get("submission_status") == "AP" and d >= start:
                prods = r.get("products") or []
                brand = ", ".join(sorted({p.get("brand_name", "") for p in prods if p.get("brand_name")}))[:80]
                ingr = ", ".join(sorted({a.get("name", "") for p in prods for a in p.get("active_ingredients") or []}))[:80]
                cls = s.get("submission_class_code_description") or ""
                pri = " · priority review" if (s.get("review_priority") or "").upper() == "PRIORITY" else ""
                recs.append({"source": "openfda", "key": f"fda:{app}:{d}", "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                             "company": r.get("sponsor_name") or "", "agency": "FDA", "amount": 0.0, "multi": False,
                             "detail": f"{brand or ingr} ({ingr}) {app}. {cls}{pri}".strip(),
                             "link": f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process"
                                     f"&ApplNo={re.sub(r'[^0-9]', '', app)}"})
    return recs


SOURCES = {"dod": dod, "usaspending": usaspending, "openfda": openfda}


def due(db, name, now_ts):
    """Each source is polled on its own interval (kv timestamps), not every 15-minute cycle."""
    every = {"dod": C.DOD_POLL_MINUTES, "usaspending": C.USASPENDING_POLL_MINUTES, "openfda": C.FDA_POLL_MINUTES}[name]
    last = float(db.get_kv(f"feed_last_{name}", "0") or 0)
    return now_ts - last >= every * 60


# ------------------------------------------------------------------ matching + gates

def process(db, edgar, market_get, recs, today):
    """Match, gate, store. Returns alert dicts. Several awards to one company in one source/day become one alert."""
    if not recs:
        return []
    names.load(edgar)
    groups = {}
    for r in recs:
        if db.award_seen(r["key"]):
            continue
        hit = names.match(r["company"])
        if not hit or not hit[0]:
            db.save_award(r["source"], r["key"], r["date"], today.isoformat(), r["company"], None, None,
                          r["agency"], r["amount"], r["detail"], "unmatched")
            continue
        tk, cik, how = hit
        if r["multi"]:
            db.save_award(r["source"], r["key"], r["date"], today.isoformat(), r["company"], tk, cik, r["agency"],
                          r["amount"], r["detail"], "multi_award", "shared ceiling across several companies")
            continue
        g = groups.setdefault((r["source"], tk, r["date"]), {"ticker": tk, "cik": cik, "how": how, "recs": []})
        g["recs"].append(r)

    out = []
    for (source, tk, date), g in groups.items():
        total = sum(r["amount"] for r in g["recs"])
        m = market_get(tk)
        reason = gate(source, total, m)
        ids = [db.save_award(source, r["key"], r["date"], today.isoformat(), r["company"], tk, g["cik"], r["agency"],
                             r["amount"], r["detail"], "rejected" if reason else "pending", reason or "",
                             m["price"] if m else None) for r in g["recs"]]
        if reason:
            continue
        if source == "openfda" and _already_alerted_fda(db, tk, today):
            for i in ids:
                db.mark_feed("awards", i, "logged", "already alerted from the company's 8-K")
            continue
        out.append({"ids": ids, "source": source, "ticker": tk, "cik": g["cik"], "how": g["how"], "date": date,
                    "total": total, "recs": g["recs"], "m": m})
    log.info("feeds: %d new records, %d alert candidates", len(recs), len(out))
    return out


def gate(source, total, m):
    if not m:
        return "no market data"
    if m["price"] < C.EVENT_MIN_PRICE:
        return f"price ${m['price']:.2f} < min"
    if m["adv20"] < C.EVENT_MIN_ADV_USD:
        return f"ADV20 ${m['adv20']/1e6:.1f}M too low"
    if m["mcap"] is not None and m["mcap"] < C.EVENT_MIN_MCAP:
        return f"market cap ${m['mcap']/1e6:,.0f}M too small"
    if source == "openfda":
        if m["mcap"] is not None and C.FDA_MAX_MCAP and m["mcap"] > C.FDA_MAX_MCAP:
            return "market cap too large for one approval to matter"
        return None
    if total < C.CONTRACT_MIN_USD:
        return f"award ${total/1e6:.1f}M < min"
    if not m["mcap"]:
        return "market cap unknown (can't size the award)"
    if total / m["mcap"] < C.CONTRACT_MIN_PCT_MCAP:
        return f"award is {total / m['mcap']:.2%} of market cap (< {C.CONTRACT_MIN_PCT_MCAP:.1%})"
    return None


def _already_alerted_fda(db, ticker, today):
    since = (today - dt.timedelta(days=14)).isoformat()
    return any(e["status"] == "alerted" and any(t == "fda_approval" for t, _ in e["tags"])
               for e in db.recent_events(ticker=ticker, since_iso=since))
