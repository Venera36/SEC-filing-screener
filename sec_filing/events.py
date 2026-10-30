"""8-K screening: material agreements, government contracts, FDA news, acquisitions, bankruptcies,
non-reliance on financials, CEO/CFO changes and earnings.

Flow per 8-K: live feed / daily index -> listed company with a known ticker -> item numbers worth reading ->
filing text (main document, plus the press-release exhibit for items 7.01/8.01) -> keyword classifier ->
market gates -> alert. Every classified filing is stored, so insider alerts can mention recent 8-K events
(and the 'events' signal can add points for them).
"""
import html
import json
import re
import logging
import datetime as dt

from . import config as C, names
from .analysis import clean_text

log = logging.getLogger("events")

READ_ITEMS = {"1.01", "1.03", "2.01", "2.02", "4.02", "5.02", "7.01", "8.01"}

# tag -> (label, sentiment, points). Points feed the 'events' signal on insider alerts.
TAGS = {
    "fda_approval":   ("FDA approval", "+", 15),
    "fda_milestone":  ("FDA milestone", "+", 6),
    "fda_negative":   ("FDA setback", "-", 0),
    "gov_contract":   ("Government contract", "+", 10),
    "merger":         ("Merger / acquisition agreement", "", 5),
    "agreement":      ("Material agreement", "+", 5),
    "acquisition":    ("Acquisition / disposal completed", "", 3),
    "earnings":       ("Earnings release", "", 0),
    "exec_change":    ("CEO/CFO change", "", 0),
    "bankruptcy":     ("Bankruptcy / receivership", "-", 0),
    "non_reliance":   ("Past financials can't be relied on", "-", 0),
    "dilution":       ("Equity financing / dilution", "-", 0),
    "financing":      ("Debt / credit agreement", "", 0),
}
# Tags shown as warnings on insider alerts (a CEO/CFO change isn't bad news, but it matters next to insider buying)
NEGATIVE = {t for t, v in TAGS.items() if v[1] == "-"} | {"exec_change"}

FDA = r"(?:FDA|Food\s+and\s+Drug\s+Administration)"
FDA_NEG = re.compile(r"complete\s+response\s+letter|\bCRL\b|clinical\s+hold|refus\w*\s+to\s+file|"
                     + FDA + r"[^.]{0,150}\b(?:declined|did\s+not\s+approve|not\s+approv)", re.I)
FDA_APPROVAL = re.compile(
    FDA + r"[^.]{0,150}\b(?:approv(?:ed|al)|grant(?:ed)?\s+(?:accelerated\s+|full\s+)?approval|clear(?:ed|ance))\b|"
    r"\b(?:approv(?:ed|al)|clear(?:ed|ance))\b[^.]{0,80}\b(?:by|from)\s+the\s+(?:U\.?S\.?\s+)?" + FDA, re.I)
FDA_MILESTONE = re.compile(r"breakthrough\s+therapy|fast\s+track\s+designation|priority\s+review|"
                           r"accelerated\s+approval\s+pathway|orphan\s+drug\s+designation|PDUFA|"
                           + FDA + r"[^.]{0,120}accept\w*[^.]{0,60}(?:NDA|BLA|application|filing)", re.I)
FDA_PLANNED = re.compile(r"\b(?:expect|anticipat|plan|intend|seek|will|may|could|potential|if\s+approved)\w*"
                         r"[^.]{0,60}" + FDA + r"[^.]{0,60}approv", re.I)

GOV = re.compile(r"Department\s+of\s+(?:Defense|the\s+(?:Army|Navy|Air\s+Force)|War|Energy|Homeland\s+Security|"
                 r"Health\s+and\s+Human\s+Services|Veterans\s+Affairs|Transportation|State)|U\.?S\.?\s+(?:Army|Navy|"
                 r"Air\s+Force|Space\s+Force|Marine\s+Corps|Coast\s+Guard|Government|government)|\bDoD\b|\bDARPA\b|"
                 r"\bNASA\b|\bBARDA\b|\bDHS\b|\bDLA\b|\bGSA\b|Missile\s+Defense\s+Agency|federal\s+government|"
                 r"Space\s+Development\s+Agency|National\s+Institutes\s+of\s+Health|\bNIH\b", re.I)
AWARD = re.compile(r"\b(?:award(?:ed|s)?|contract|task\s+order|delivery\s+order|IDIQ|other\s+transaction|"
                   r"production\s+order|purchase\s+order)\b", re.I)
DILUTION = re.compile(r"underwriting\s+agreement|securities\s+purchase\s+agreement|equity\s+distribution\s+agreement|"
                      r"at[-\s]the[-\s]market|sales\s+agreement|registered\s+direct|private\s+placement|"
                      r"public\s+offering|pre-funded\s+warrants", re.I)
FINANCING = re.compile(r"credit\s+agreement|credit\s+facility|loan\s+agreement|indenture|senior\s+(?:secured\s+)?notes|"
                       r"term\s+loan|revolving|note\s+purchase\s+agreement|convertible\s+notes", re.I)
ROLE = (r"(?:the\s+|its\s+|our\s+)?(?:Company['’]s\s+)?(?:new\s+|interim\s+|incoming\s+|permanent\s+)?"
        r"(?:President\s+and\s+)?(?:Chief\s+Executive\s+Officer|Chief\s+Financial\s+Officer|Principal\s+Executive\s+"
        r"Officer|Principal\s+Financial\s+Officer|CEO|CFO)\b")
# The CEO/CFO must be the subject of the change, e.g. "resigned as CFO", "appointed ... to serve as CEO",
# "Transition of Chief Financial Officer" - not just mentioned near a word like "appointed".
EXEC_EVENT = re.compile(
    r"\b(?:resign|retire|step(?:ped|ping|s)?\s+down|depart|terminat|separat|leav)\w*[^.]{0,80}?\b(?:as|from\s+(?:the\s+)?"
    r"(?:position|role|office)s?\s+of|from\s+(?:his|her|their)\s+(?:position|role)s?\s+as)\s+" + ROLE + "|"
    r"\b(?:appoint|elect|nam|promot|hir|select)\w*[^.]{0,120}?\b(?:as|to\s+serve\s+as|to\s+(?:the\s+)?(?:position|role|"
    r"office)\s+of|to\s+succeed[^.]{0,60}?\s+as)\s+" + ROLE + "|"
    r"\b(?:transition|succession|departure|retirement|resignation|termination)\s+of\s+" + ROLE + "|"
    + ROLE + r"[^.]{0,80}?\b(?:will|has\s+decided\s+to|intends\s+to|plans\s+to|notified[^.]{0,40}?of\s+(?:his|her|"
    r"their)\s+(?:intention|decision)\s+to|announced\s+(?:his|her|their)\s+(?:intention|decision)\s+to)\s+"
    r"(?:resign|retire|step\s+down|depart|leave)", re.I)
MERGER = re.compile(r"agreement\s+and\s+plan\s+of\s+(?:merger|reorganization)|merger\s+agreement|plan\s+of\s+merger|"
                    r"business\s+combination\s+agreement|arrangement\s+agreement|definitive\s+agreement\s+to\s+(?:acquire|"
                    r"be\s+acquired|merge|sell)|(?:share|stock|equity|membership\s+interest|asset)\s+purchase\s+agreement"
                    r"[^.]{0,200}\bacqui", re.I)
ENTERED = re.compile(r"entered\s+into[^.]{0,250}", re.I)
EXHIBIT = re.compile(r"ex-?\d|_ex\d|exhibit|ex\d{2}", re.I)
EX99 = re.compile(r"ex(?:hibit)?[-_]?99", re.I)
ITEM_HEAD = re.compile(r"Item\s*(\d\.\d{2})", re.I)
USD = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)\s*(billion|million|thousand)?", re.I)


def amounts(text):
    """Dollar amounts mentioned in text, in USD."""
    out = []
    for num, unit in USD.findall(text or ""):
        try:
            v = float(num.replace(",", ""))
        except ValueError:
            continue
        v *= {"billion": 1e9, "million": 1e6, "thousand": 1e3}.get((unit or "").lower(), 1)
        out.append(v)
    return out


def award_amount(text):
    """Largest dollar amount in a sentence that talks about an award/contract/agreement value."""
    best = 0.0
    for sent in re.split(r"(?<=[.;])\s+", text or ""):
        if "$" in sent and (AWARD.search(sent) or re.search(r"agreement|value|worth", sent, re.I)):
            best = max([best] + [a for a in amounts(sent) if a >= 1e6])   # skip par values like $0.001
    return best


def snippet(text, rx, width=260):
    """The sentence around the first match - so the alert shows *why* it was tagged."""
    m = rx.search(text or "")
    if not m:
        return ""
    a = max(text.rfind(". ", 0, m.start()) + 2, m.start() - width // 2, 0)
    b = text.find(". ", m.end())
    b = min(b + 1 if b != -1 else len(text), a + width)
    return text[a:b].replace("☐", "").replace("☒", "").strip()


def section(body, item):
    """Text of one Item in the 8-K body ("Item 5.02 ... " up to the next Item heading). The longest occurrence wins,
    which skips one-line mentions. Falls back to the whole body if the heading isn't found."""
    heads = list(ITEM_HEAD.finditer(body or ""))
    best = ""
    for i, h in enumerate(heads):
        if h.group(1) != item:
            continue
        end = next((x.start() for x in heads[i + 1:] if x.group(1) != item), len(body))
        if end - h.start() > len(best):
            best = body[h.start():end]
    return best if len(best) > 60 else body


def classify(items, body, exhibit=""):
    """Pure function: (item numbers, cleaned 8-K body, cleaned press-release exhibit) -> [(tag, snippet)].
    FDA and government-contract news is looked for in the body and the press release. Agreements and CEO/CFO
    changes are judged only from their own Item section of the body: earnings press releases quote the CEO and
    mention old departures, which caused false CEO/CFO-change alerts."""
    items = set(items or [])
    text = (body + " " + exhibit).strip()
    tags = []

    def add(tag, rx, src=None):
        if tag not in (t for t, _ in tags):
            tags.append((tag, snippet(src if src is not None else text, rx) if rx else ""))

    if "1.03" in items:
        add("bankruptcy", re.compile(r"bankruptcy|chapter\s+1[15]|receiver", re.I))
    if "4.02" in items:
        add("non_reliance", re.compile(r"should\s+no\s+longer\s+be\s+relied|restat", re.I))

    if items & {"7.01", "8.01", "1.01", "2.02"}:
        if FDA_NEG.search(text):
            add("fda_negative", FDA_NEG)
        elif FDA_APPROVAL.search(text) and not FDA_PLANNED.search(snippet(text, FDA_APPROVAL)):
            add("fda_approval", FDA_APPROVAL)
        elif FDA_MILESTONE.search(text):
            add("fda_milestone", FDA_MILESTONE)

    gov_rx = re.compile(r"(?:" + GOV.pattern + r")[^.]{0,200}(?:" + AWARD.pattern + r")|(?:" + AWARD.pattern
                        + r")[^.]{0,200}(?:" + GOV.pattern + ")", re.I)
    if items & {"1.01", "7.01", "8.01"} and gov_rx.search(text):
        add("gov_contract", gov_rx)

    if "1.01" in items:
        sec = section(body, "1.01")
        has_gov = any(t == "gov_contract" for t, _ in tags)
        if MERGER.search(sec):
            add("merger", MERGER, sec)
        elif "3.02" in items or DILUTION.search(sec):
            add("dilution", ENTERED if ENTERED.search(sec) else DILUTION, sec)
        elif "2.03" in items or FINANCING.search(sec):
            if not has_gov:
                add("financing", ENTERED if ENTERED.search(sec) else FINANCING, sec)
        elif not has_gov:
            add("agreement", ENTERED, sec)
    if "2.01" in items:
        add("acquisition", re.compile(r"complet\w*[^.]{0,120}(?:acquisition|merger|sale|disposition)", re.I),
            section(body, "2.01"))
    if "5.02" in items:
        sec = section(body, "5.02")
        if EXEC_EVENT.search(sec):
            add("exec_change", EXEC_EVENT, sec)
    if "2.02" in items:
        add("earnings", None)
    return tags


def alert_tags():
    return {t.strip() for t in C.EVENT_ALERT_TAGS.split(",") if t.strip()}


# ------------------------------------------------------------------ fetching

def _items_from_submissions(edgar, cik, acc):
    data = edgar.submissions(cik, max_age=600)
    rec = (data or {}).get("filings", {}).get("recent", {})
    for i, a in enumerate(rec.get("accessionNumber", [])):
        if a == acc:
            it = (rec.get("items") or [""] * (i + 1))[i] or ""
            doc = (rec.get("primaryDocument") or [""] * (i + 1))[i] or ""
            return [x.strip() for x in it.split(",") if x.strip()], doc
    return None, ""


def filing_text(edgar, cik, acc, items, primary_doc=""):
    """(8-K body, press-release exhibit ex-99) as cleaned text. The body is the largest non-exhibit HTML file,
    skipping EDGAR's index / header pages (those were being read instead of the filing)."""
    files = edgar.filing_index(cik, acc)
    if not primary_doc:
        body = [f for f in files if f.get("name", "").lower().endswith((".htm", ".html"))
                and not EXHIBIT.search(f["name"]) and "index" not in f["name"].lower()
                and not re.match(r"R\d+\.htm", f["name"])]
        body.sort(key=lambda f: -_size(f))
        primary_doc = body[0]["name"] if body else ""
    main = clean_text(edgar.document(cik, acc, primary_doc) or "", C.EVENT_MAX_DOC_CHARS) if primary_doc else ""
    exhibit = ""
    if set(items) & {"7.01", "8.01", "2.02", "1.01"}:
        ex = [f["name"] for f in files if EX99.search(f.get("name", ""))
              and f["name"].lower().endswith((".htm", ".html", ".txt"))]
        if ex:
            exhibit = clean_text(edgar.document(cik, acc, sorted(ex)[0]) or "", C.EVENT_MAX_DOC_CHARS)
    return main, exhibit


def _size(f):
    try:
        return int(f.get("size") or 0)
    except (TypeError, ValueError):
        return 0


def gather(edgar, today, index_days):
    """All 8-Ks from the live feed plus the last `index_days` business days of the daily index."""
    found, day, checked = {}, today, 0
    while checked < index_days:
        if day.weekday() < 5:
            found.update(edgar.daily_8k(day))
            checked += 1
        day -= dt.timedelta(days=1)
    found.update(edgar.current_8k())               # live feed last: its entries carry the item numbers
    return found


def process(db, edgar, market_get, filings, today):
    """Classify new 8-Ks and return the ones to alert on: [{event row + market data}]."""
    names.load(edgar)
    want = alert_tags()
    out, scanned = [], 0
    for acc, f in filings.items():
        if db.event_seen(acc):
            continue
        ticker = names.ticker_for_cik(f["cik"])
        items = f["items"]
        if not ticker:                                     # not a listed operating company (funds, private issuers)
            db.save_event(acc, f["cik"], None, f["company"], items or [], [], today.isoformat(), "unlisted")
            continue
        if items is not None and not set(items) & READ_ITEMS:
            db.save_event(acc, f["cik"], ticker, f["company"], items, [], today.isoformat(), "skipped_items")
            continue
        doc = ""
        if items is None:                                  # daily-index entries carry no item list
            items, doc = _items_from_submissions(edgar, f["cik"], acc)
            if items is None:
                continue                                   # submissions not updated yet -> retry next cycle
            if not set(items) & READ_ITEMS:
                db.save_event(acc, f["cik"], ticker, f["company"], items, [], today.isoformat(), "skipped_items")
                continue
        if set(items) <= {"2.02", "9.01"} and "earnings" not in want:
            # earnings-only 8-K: record it (shown on insider alerts) without downloading it or pricing the stock
            db.save_event(acc, f["cik"], ticker, f["company"], items, [("earnings", "")], today.isoformat(), "logged")
            continue
        m = market_get(ticker)
        reason = gate(m, items)
        if reason:
            db.save_event(acc, f["cik"], ticker, f["company"], items, [], today.isoformat(), "rejected_market", reason)
            continue
        body, exhibit = filing_text(edgar, f["cik"], acc, items, doc)
        if not body and not exhibit:
            continue                                       # retry next cycle
        scanned += 1
        tags = classify(items, body, exhibit)
        tset = {t for t, _ in tags}
        amt = 0.0
        if "gov_contract" in tset:
            amt = award_amount(body + " " + exhibit)
        elif tset & {"merger", "agreement"}:
            amt = award_amount(section(body, "1.01"))
        alert = [t for t, _ in tags if t in want]
        if alert == ["agreement"] or (alert and set(alert) <= {"agreement", "earnings"}):
            # a generic agreement alerts only if its value is material to the company
            mcap = m.get("mcap") if m else None
            if not (amt and mcap and amt / mcap >= C.EVENT_AGREEMENT_MIN_PCT_MCAP):
                alert = []
        status, note, verdict = ("alert" if alert else "logged"), "", None
        if alert:
            from . import event_ai                         # lazy: event_ai imports this module
            if event_ai.enabled():
                verdict = event_ai.review(ticker, f["company"], items, tags, body, exhibit,
                                          m.get("mcap") if m else None, today.isoformat())
            if verdict and C.EVENT_AI_MODE == "on":
                ok, tags = event_ai.apply(verdict, tags, want)
                if ok is False:
                    alert, status, note = [], "ai_filtered", verdict["summary"]
                    log.info("%s 8-K held by AI review: %s", ticker, verdict["summary"])
                else:
                    alert = [tags[0][0]]
                    amt = amt or verdict["amount_usd"] or 0.0
        ev = db.save_event(acc, f["cik"], ticker, f["company"], items, tags, today.isoformat(), status, note,
                           amount=amt, price=m["price"] if m else None, ai=json.dumps(verdict) if verdict else "")
        if alert:
            out.append({"id": ev, "acc": acc, "cik": f["cik"], "ticker": ticker, "company": f["company"],
                        "items": items, "tags": tags, "amount": amt, "m": m,
                        "when": f.get("accepted") or today.isoformat(),
                        "ai": verdict if C.EVENT_AI_MODE == "on" else None})
    log.info("8-K: %d filings checked, %d read, %d to alert", len(filings), scanned, len(out))
    return out


def gate(m, items):
    """Rejection reason or None. Bankruptcy / non-reliance skip the size gates (warnings matter at any size)."""
    if not m:
        return "no market data"
    if set(items) & {"1.03", "4.02"}:
        return None
    if m["price"] < C.EVENT_MIN_PRICE:
        return f"price ${m['price']:.2f} < min"
    if m["adv20"] < C.EVENT_MIN_ADV_USD:
        return f"ADV20 ${m['adv20']/1e6:.1f}M too low"
    if m["mcap"] is not None and (m["mcap"] < C.EVENT_MIN_MCAP or (C.EVENT_MAX_MCAP and m["mcap"] > C.EVENT_MAX_MCAP)):
        return f"market cap ${m['mcap']/1e6:,.0f}M out of range"
    return None


def insider_context(db, ticker, today):
    """Open-market insider buying in the same stock over WINDOW_DAYS, for the confluence line on event alerts."""
    since = (today - dt.timedelta(days=C.WINDOW_DAYS)).isoformat()
    rows = [r for r in db.purchases_since(since) if r["ticker"] == ticker]
    if not rows:
        return None
    return {"total": sum(r["value"] for r in rows), "insiders": len({r["owner"].strip().lower() for r in rows}),
            "first": min(r["tx_date"] for r in rows), "last": max(r["tx_date"] for r in rows)}


def link(cik, acc):
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{acc}-index.htm"


def esc(s):
    return html.escape(s or "")
