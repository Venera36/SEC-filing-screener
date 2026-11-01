"""Tests for 8-K screening, government contracts, FDA approvals and the name matcher. No network."""
import os, sys, json, time, tempfile, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["SEC_USER_AGENT"] = "test test@example.com"
from sec_filing import config as C, events, feeds, names, telegram, event_signal, signals, main as M
from sec_filing.db import DB
from sec_filing.edgar import Edgar

TODAY = dt.date(2026, 10, 29)
M_OK = {"price": 20.0, "last_close": 20.0, "session": "close", "ext_move": 0.0, "adv20": 3e7, "mcap": 8e8,
        "ret20": 0.02, "ret60": -0.05, "pct_of_52w_high": 0.8}
TICKERS = [{"cik_str": 111, "ticker": "ACME", "title": "Acme Defense Systems, Inc."},
           {"cik_str": 222, "ticker": "VRTX", "title": "Vertex Pharmaceuticals Inc"},
           {"cik_str": 333, "ticker": "BIOX", "title": "Bioxcel Therapeutics, Inc."},
           {"cik_str": 936468, "ticker": "LMT", "title": "LOCKHEED MARTIN CORP"},
           {"cik_str": 444, "ticker": "NANO", "title": "Nano Widgets Corp"}]
names.set_rows(TICKERS)


def tags(items, text):
    return [t for t, _ in events.classify(items, text)]


# ---------------------------------------------------------------- classifier
def test_classify_fda():
    assert tags(["8.01"], "On October 28, 2026, the U.S. Food and Drug Administration approved ZORVAX "
                          "(zorvaxumab) for adults with moderate disease.") == ["fda_approval"]
    assert tags(["7.01", "9.01"], "The Company announced that ZORVAX was approved by the FDA for adults.") == ["fda_approval"]
    assert tags(["8.01"], "The Company received a Complete Response Letter from the FDA regarding its NDA.") == ["fda_negative"]
    assert tags(["8.01"], "The FDA placed the IND on clinical hold.") == ["fda_negative"]
    assert tags(["8.01"], "The FDA granted Breakthrough Therapy designation to BX-12.") == ["fda_milestone"]
    assert tags(["8.01"], "The FDA accepted the BLA for priority review with a PDUFA date of March 3, 2027.") == ["fda_milestone"]
    # forward-looking talk about approval is not an approval
    assert "fda_approval" not in tags(["8.01"], "We expect to seek FDA approval of BX-12 in 2027.")
    # board approvals are not FDA approvals
    assert tags(["8.01"], "The Board of Directors approved a share repurchase program of $50 million.") == []


def test_classify_contracts_and_agreements():
    t = ("On October 27, 2026, Acme Defense Systems, Inc. was awarded a $48.5 million firm-fixed-price contract "
         "by the U.S. Army for counter-drone systems.")
    assert tags(["8.01"], t) == ["gov_contract"]
    assert events.award_amount(t) == 48.5e6
    assert tags(["1.01"], "The Company entered into a supply agreement with Globex for $12 million per year.") == ["agreement"]
    assert tags(["1.01"], "The Company entered into an underwriting agreement for a public offering of shares.") == ["dilution"]
    assert tags(["1.01"], "The Company entered into a Credit Agreement providing a $200 million revolving facility.") == ["financing"]
    assert tags(["1.01", "2.03"], "Acme entered into a contract with the Department of Defense valued at $30 million.") == ["gov_contract"]


def test_classify_other_items():
    assert tags(["1.03"], "The Company filed a voluntary petition under Chapter 11.") == ["bankruptcy"]
    assert tags(["4.02"], "The financial statements should no longer be relied upon.") == ["non_reliance"]
    assert tags(["2.01"], "The Company completed the acquisition of Initech.") == ["acquisition"]
    assert tags(["5.02"], "John Smith resigned as Chief Financial Officer effective November 1.") == ["exec_change"]
    assert tags(["5.02"], "Jane Roe was elected to the Board of Directors.") == []      # director only: not alerted
    assert tags(["2.02", "9.01"], "The Company issued a press release with third quarter results.") == ["earnings"]


# ---------------------------------------------------------------- EDGAR parsing
ATOM = """<?xml version="1.0" encoding="ISO-8859-1" ?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>8-K - Acme Defense Systems, Inc. (0000000111) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/111/000011126000000012/0000111260-00-000012-index.htm"/>
<summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-10-29 &lt;b&gt;AccNo:&lt;/b&gt; 0000111260-00-000012 &lt;b&gt;Size:&lt;/b&gt; 300 KB
&lt;br&gt;Item 8.01: Other Events&lt;br&gt;Item 9.01: Financial Statements and Exhibits</summary>
<updated>2026-10-29T10:01:02-04:00</updated><category scheme="https://www.sec.gov/" label="form type" term="8-K"/></entry>
<entry><title>4 - Someone (0000000999) (Reporting)</title>
<link href="https://www.sec.gov/Archives/edgar/data/999/000099926000000001/x-index.htm"/>
<category term="4"/></entry></feed>"""

IDX = """Form Type   Company Name                                                  CIK         Date Filed  File Name
---------------------------------------------------------------------------------------------------------------------------------------------
4           Someone                                                       999         20261028    edgar/data/999/0000999260-00-000001.txt
8-K         VERTEX PHARMACEUTICALS INC / MA                               222         20261028    edgar/data/222/0000222260-00-000007.txt
8-K/A       OTHER CO                                                      555         20261028    edgar/data/555/0000555260-00-000001.txt
"""


def test_edgar_parsing():
    e = Edgar("t t@example.com")
    e._get = lambda url, soft=False: ATOM if "getcurrent" in url else IDX
    f = e.current_8k(pages=1)
    assert f == {"0000111260-00-000012": {"path": "edgar/data/111/0000111260-00-000012.txt", "cik": "111",
                                        "company": "Acme Defense Systems, Inc.", "items": ["8.01", "9.01"],
                                        "accepted": "2026-10-29T10:01:02-04:00"}}
    d = e.daily_8k(dt.date(2026, 10, 28))
    assert list(d) == ["0000222260-00-000007"] and d["0000222260-00-000007"]["items"] is None
    assert list(e.daily_form4(dt.date(2026, 10, 28))) == ["0000999260-00-000001"]


# ---------------------------------------------------------------- names
def test_names():
    assert names.norm("VERTEX PHARMS INC") == "vertex pharmaceuticals"
    assert names.match("VERTEX PHARMS INC")[:2] == ("VRTX", "222")
    assert names.match("Acme Defense Systems Inc.")[0] == "ACME"
    assert names.match("Sikorsky Aircraft Corp.")[0] == "LMT"                        # subsidiary alias
    assert names.match("Lockheed Martin Rotary and Mission Systems")[0] == "LMT"
    assert names.match("Anduril Industries Inc.") is None                            # known private
    assert names.match("Acme Plumbing Supply LLC") is None                           # same first word, not a match
    assert names.match("Totally Unknown Contractor LLC") is None
    assert names.ticker_for_cik("0000000111") == "ACME"


# ---------------------------------------------------------------- DoD parser
DOD_PAGE = """<h1>Contracts For Oct. 28, 2026</h1>
<p><strong>ARMY</strong></p>
<p>Acme Defense Systems, Inc., Huntsville, Alabama, was awarded a $48,500,000 firm-fixed-price contract for
counter-small unmanned aircraft systems. Work will be performed in Huntsville, Alabama, with an estimated completion
date of Oct. 30, 2028. Army Contracting Command, Redstone Arsenal, Alabama, is the contracting activity (W31P4Q-27-C-0001).</p>
<p>Lockheed Martin Corp., Grand Prairie, Texas, is awarded $9,800,000 for a modification (P00012) to contract
W31P4Q-24-C-0100 for missile spares.</p>
<p><strong>NAVY</strong></p>
<p>Alpha Corp., Reston, Virginia; Bravo LLC, Arlington, Virginia; and Acme Defense Systems, Inc., Huntsville, Alabama,
are each awarded a multiple-award contract with a combined ceiling of $950,000,000 for engineering services.</p>
<p>Nano Widgets Corp., San Diego, California, has been awarded a $12,000,000 contract for sonar parts.</p>
<p>*Small business</p>"""


def test_dod_parser():
    rows = feeds.parse_dod_article(DOD_PAGE)
    got = [(a, c, amt, multi) for a, c, amt, multi, _ in rows]
    assert got[0] == ("Army", "Acme Defense Systems, Inc.", 48.5e6, False)
    assert got[1] == ("Army", "Lockheed Martin Corp.", 9.8e6, False)
    assert got[2][1] == "Nano Widgets Corp." and got[2][0] == "Navy"     # multi-award paragraph has no single awardee
    assert len(got) == 3
    assert feeds._parse_date("Oct. 28, 2026") == "2026-10-28" and feeds._parse_date("Sept. 3, 2026") == "2026-09-03"


def test_dod_multi_award_detected():
    p = ("<p>Alpha Corp., Reston, Virginia, has been awarded a multiple-award, indefinite-delivery contract with a "
         "ceiling of $950,000,000.</p>")
    assert feeds.parse_dod_article(p)[0][3] is True


# ---------------------------------------------------------------- feeds pipeline
def _fresh_db():
    return DB(tempfile.mkdtemp())


def test_feeds_process_and_gates():
    db = _fresh_db()
    mkt = {"ACME": dict(M_OK, mcap=8e8), "LMT": dict(M_OK, mcap=1.1e11), "NANO": dict(M_OK, mcap=6e8, adv20=1e5)}
    recs = [{"source": "dod", "key": f"dod:1:{i}", "date": "2026-10-28", "company": c, "agency": "Army", "amount": a,
             "multi": False, "detail": "d", "link": "https://example.test"} for i, (c, a) in
            enumerate([("Acme Defense Systems, Inc.", 48.5e6), ("Acme Defense Systems, Inc.", 5e6),
                       ("Lockheed Martin Corp.", 2e8), ("Nano Widgets Corp.", 12e6), ("Unknown Co", 3e7)])]
    out = feeds.process(db, None, mkt.get, recs, TODAY)
    assert [a["ticker"] for a in out] == ["ACME"]
    assert out[0]["total"] == 53.5e6 and len(out[0]["recs"]) == 2               # two awards -> one alert
    st = {r["company"] + str(r["amount"]): (r["status"], r["note"]) for r in db.c.execute("SELECT * FROM awards")}
    assert st["Lockheed Martin Corp.200000000.0"][0] == "rejected" and "of market cap" in st["Lockheed Martin Corp.200000000.0"][1]
    assert "ADV20" in st["Nano Widgets Corp.12000000.0"][1]
    assert st["Unknown Co30000000.0"][0] == "unmatched"
    assert feeds.process(db, None, mkt.get, recs, TODAY) == []                     # de-duplicated
    msg = telegram.format_award(out[0])
    assert "GOVERNMENT CONTRACT" in msg and "$53.50M" in msg and "6.7% of market cap" in msg


def test_openfda_parse():
    payload = {"results": [
        {"application_number": "NDA219999", "sponsor_name": "VERTEX PHARMS INC",
         "products": [{"brand_name": "ZORVAX", "active_ingredients": [{"name": "ZORVAXIB"}]}],
         "submissions": [{"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20261027",
                          "submission_class_code_description": "Type 1 - New Molecular Entity",
                          "review_priority": "PRIORITY"},
                         {"submission_type": "SUPPL", "submission_status": "AP", "submission_status_date": "20261028"}]},
        {"application_number": "ANDA200000", "sponsor_name": "GENERIC CO",
         "submissions": [{"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "20261027"}]},
        {"application_number": "NDA100000", "sponsor_name": "OLD CO",          # matched on an older SUPPL only
         "submissions": [{"submission_type": "ORIG", "submission_status": "AP", "submission_status_date": "19990101"}]}]}
    saved = feeds._get
    feeds._get = lambda url, **k: json.dumps(payload)
    try:
        recs = feeds.openfda(TODAY)
    finally:
        feeds._get = saved
    assert len(recs) == 1 and recs[0]["company"] == "VERTEX PHARMS INC" and recs[0]["date"] == "2026-10-27"
    assert "ZORVAX" in recs[0]["detail"] and "priority review" in recs[0]["detail"]
    out = feeds.process(_fresh_db(), None, lambda t: dict(M_OK, mcap=4e9), recs, TODAY)
    assert out and out[0]["ticker"] == "VRTX"
    assert "FDA APPROVAL" in telegram.format_award(out[0])


def test_usaspending_filters():
    class R:
        status_code = 200
        def json(self):
            return {"results": [
                {"Award ID": "A1", "generated_internal_id": "CONT_AWD_1", "Recipient Name": "ACME DEFENSE SYSTEMS INC",
                 "Award Amount": 2.5e7, "Awarding Agency": "National Aeronautics and Space Administration",
                 "Start Date": "2026-10-20", "Description": "LUNAR WIDGETS"},
                {"Award ID": "A2", "Recipient Name": "X", "Award Amount": 9e8, "Awarding Agency": "Department of Defense",
                 "Start Date": "2026-10-20"},
                {"Award ID": "A3", "Recipient Name": "Y", "Award Amount": 9e8, "Awarding Agency": "Department of Energy",
                 "Start Date": "2019-01-01"}]}
    saved = feeds._s.post
    feeds._s.post = lambda *a, **k: R()
    try:
        recs = feeds.usaspending(TODAY)
    finally:
        feeds._s.post = saved
    assert [r["key"] for r in recs] == ["usa:CONT_AWD_1"] and recs[0]["amount"] == 2.5e7


# ---------------------------------------------------------------- 8-K pipeline end-to-end (fake EDGAR)
class FakeEdgar:
    def __init__(self, docs, subs=None):
        self.docs, self.subs, self.calls = docs, subs or {}, []

    def filing_index(self, cik, acc):
        return [{"name": n} for n in self.docs.get(acc, {})]

    def document(self, cik, acc, name):
        self.calls.append((acc, name))
        return self.docs[acc][name]

    def submissions(self, cik, max_age=0):
        return self.subs.get(cik)

    def company_tickers(self):
        return TICKERS

    def current_8k(self, pages=3):
        return {}

    def daily_8k(self, day):
        return {}


def test_events_process():
    db = _fresh_db()
    docs = {
        "a1": {"acme8k.htm": "<html><p>Item 8.01 Other Events. See press release.</p></html>",
               "ex99-1.htm": "<p>Acme Defense Systems, Inc. was awarded a $48.5 million contract by the U.S. Army.</p>"},
        "a2": {"vrtx-8k.htm": "<p>Item 8.01. The U.S. Food and Drug Administration approved ZORVAX for adults.</p>"},
        "a3": {"x.htm": "<p>Results for the third quarter.</p>"},
        "a4": {"n.htm": "<p>The Company filed for Chapter 11 bankruptcy.</p>"},
        "a5": {"b.htm": "<p>unused</p>"},
    }
    subs = {"333": {"filings": {"recent": {"accessionNumber": ["a5"], "items": ["9.01"], "primaryDocument": ["b.htm"]}}}}
    filings = {
        "a1": {"cik": "111", "company": "Acme", "items": ["8.01", "9.01"]},
        "a2": {"cik": "222", "company": "Vertex", "items": ["8.01"]},
        "a3": {"cik": "111", "company": "Acme", "items": ["2.02", "9.01"]},
        "a4": {"cik": "444", "company": "Nano", "items": ["1.03"]},       # tiny ADV but bankruptcy skips size gates
        "a5": {"cik": "333", "company": "Bioxcel", "items": None},         # daily index: items from submissions
        "a6": {"cik": "999999", "company": "Some Fund", "items": ["8.01"]},
    }
    e = FakeEdgar(docs, subs)
    mkt = {"ACME": M_OK, "VRTX": dict(M_OK, mcap=1.2e10), "NANO": dict(M_OK, adv20=1e5), "BIOX": M_OK}
    out = events.process(db, e, mkt.get, filings, TODAY)
    got = {o["ticker"] + ":" + o["acc"]: [t for t, _ in o["tags"]] for o in out}
    assert got == {"ACME:a1": ["gov_contract"], "VRTX:a2": ["fda_approval"], "NANO:a4": ["bankruptcy"]}, got
    st = {r["accession"]: r["status"] for r in db.c.execute("SELECT * FROM events")}
    assert st["a3"] == "logged" and st["a5"] == "skipped_items" and st["a6"] == "unlisted"
    assert ("a5", "b.htm") not in e.calls                                  # no document fetch for 9.01-only
    acme = [o for o in out if o["ticker"] == "ACME"][0]
    assert acme["amount"] == 48.5e6
    labels = [(t, events.TAGS[t][0], events.TAGS[t][1], s) for t, s in acme["tags"]]
    acme["link"] = events.link("111", "0000111260-00-0012")
    msg = telegram.format_event(acme, labels, {"total": 7e5, "insiders": 2, "first": "2026-10-20", "last": "2026-10-24"})
    assert "Government contract" in msg and "Insiders also bought $700K" in msg and "$48.50M" in msg
    assert events.process(db, e, mkt.get, filings, TODAY) == []           # already seen


def test_event_signal_and_warnings():
    db = _fresh_db()
    db.save_event("x1", "111", "ACME", "Acme", ["8.01"], [("gov_contract", "s")], "2026-10-25", "alerted")
    db.save_event("x2", "111", "ACME", "Acme", ["5.02"], [("exec_change", "s")], "2026-10-26", "logged")
    db.save_event("x3", "111", "ACME", "Acme", ["8.01"], [("fda_approval", "s")], "2026-08-01", "logged")  # too old
    c = {"ticker": "ACME", "cik": "111"}
    r = event_signal.check(db, c, TODAY)
    assert r.fired and r.points == 10 and "Government contract" in r.reason
    assert event_signal.negative_warnings(db, c, TODAY) == ["8-K 2026-10-26: CEO/CFO change"]
    assert not event_signal.check(db, {"ticker": "ZZZ", "cik": "5"}, TODAY).fired
    reg = signals._registry(db)
    assert reg["events"](None, c, M_OK, TODAY).fired


def test_scan_events_seeds_then_alerts():
    db = _fresh_db()
    sent = []
    saved = (M._push, M.market.get, M.events.gather, M.market.move_since)
    M._push = lambda msg, dry, buttons=None, chat_id=None: sent.append(msg)
    M.market.get = lambda t: M_OK
    M.market.move_since = lambda tk, when, price: {"move": 0.124, "base": 17.8, "base_date": "2026-10-28"}
    docs = {"b1": {"m.htm": "<p>Acme was awarded a $50 million contract by the U.S. Navy.</p>"}}
    first = {"old": {"cik": "111", "company": "Acme", "items": ["8.01"], "path": ""}}
    try:
        e = FakeEdgar(docs)
        M.events.gather = lambda edgar, today, n: first
        M.scan_events(db, e, True, 3)
        assert sent == [] and db.event_seen("old")                          # first run: recorded, no alert
        M.events.gather = lambda edgar, today, n: dict(first, b1={"cik": "111", "company": "Acme", "items": ["8.01"]})
        M.scan_events(db, e, True, 0)
        assert len(sent) == 1 and "8-K — $ACME" in sent[0]
        assert "Since the 8-K: <b>+12.4%</b>" in sent[0] and "market already reacted" in sent[0]
        assert db.feed_alerts_since(0) == 1
        # a gov-contract press release for ACME already went out: the matching 8-K is logged, not sent again
        db.pr_add({"key": "pr1", "symbol": "ACME", "published": "", "title": "", "site": "", "url": "",
                   "tags": '["gov_contract"]', "first_seen": time.time(), "lag_sec": 0, "status": "alerted",
                   "alerted_at": time.time()})
        docs["b2"] = docs["b1"]
        M.events.gather = lambda edgar, today, n: dict(first, b2={"cik": "111", "company": "Acme", "items": ["8.01"]})
        M.scan_events(db, e, True, 0)
        assert len(sent) == 1
        assert db.c.execute("SELECT status, note FROM events WHERE accession='b2'").fetchone()[:] == \
            ("logged", "already alerted from the press release")
    finally:
        M._push, M.market.get, M.events.gather, M.market.move_since = saved


def test_scan_feeds_seed_due_and_cap():
    db = _fresh_db()
    sent = []
    rec = lambda k, amt: {"source": "dod", "key": k, "date": "2026-10-28", "company": "Acme Defense Systems, Inc.",
                          "agency": "Army", "amount": amt, "multi": False, "detail": "d", "link": ""}
    batches = [[rec("k0", 5e7)], [rec("k1", 5e7)]]
    saved = (M._push, M.market.get, dict(feeds.SOURCES), C.FEEDS_ENABLED, M.market.move_since)
    M._push = lambda msg, dry, buttons=None, chat_id=None: sent.append(msg)
    M.market.get = lambda t: M_OK
    M.market.move_since = lambda tk, when, price: {"move": 0.02, "base": 19.6, "base_date": "2026-10-27"}
    feeds.SOURCES["dod"] = lambda today: batches.pop(0)
    C.FEEDS_ENABLED = "dod"
    try:
        M.scan_feeds(db, None, True)
        assert sent == [] and db.award_seen("k0")                             # seeded
        M.scan_feeds(db, None, True)
        assert sent == [] and batches                                         # not due yet (hourly)
        db.set_kv("feed_last_dod", 0)
        M.scan_feeds(db, None, True)
        assert len(sent) == 1 and "GOVERNMENT CONTRACT — $ACME" in sent[0]
        assert "Since the award: <b>+2.0%</b>" in sent[0] and "already reacted" not in sent[0]
        assert db.c.execute("SELECT status FROM awards WHERE key='k1'").fetchone()[0] == "alerted"
    finally:
        M._push, M.market.get, M.market.move_since = saved[0], saved[1], saved[4]
        feeds.SOURCES.clear(); feeds.SOURCES.update(saved[2]); C.FEEDS_ENABLED = saved[3]


def test_follow_up_feeds_and_report():
    db = _fresh_db()
    i = db.save_event("r1", "111", "ACME", "Acme", ["8.01"], [("gov_contract", "s")], "2026-09-01", "alert", price=10.0)
    db.mark_feed("events", i, "alerted")
    sent = []
    saved = (M._push, M.market.forward_returns)
    M._push = lambda msg, dry, buttons=None, chat_id=None: sent.append(msg)
    M.market.forward_returns = lambda tk, start, price, ns, b: {n: (0.1, 0.02) for n in ns}
    try:
        M.follow_up_feeds(db, True)
        assert len(sent) == 2 and "8-K alert, 5-trading-day result: <b>+10.0%</b>" in sent[0]
        M.follow_up_feeds(db, True)
        assert len(sent) == 2                                                  # once per day
        M.report(db)
    finally:
        M._push, M.market.forward_returns = saved


def test_live_misfires_2026_10_30():
    """Sentences from the first live morning that produced wrong alerts, plus the real changes that should alert."""
    # BCBP: accounting officer reporting to the CFO is not a CFO change
    bcbp = ("Item 5.02 Departure of Directors or Certain Officers. Finally, Karen Duran has been appointed the "
            "Company's principal accounting officer reporting to the Company's Chief Financial Officer, in addition to "
            "her role as Chief Accounting Officer. Item 9.01 Financial Statements and Exhibits.")
    assert "exec_change" not in tags(["2.02", "5.02", "8.01", "9.01"], bcbp)
    # CRI: the CEO departure is old news inside the earnings press release, the 5.02 section is about something else
    body = ("Item 2.02 Results of Operations. See Exhibit 99.1. Item 5.02 Departure of Directors. On October 28, the "
            "Board approved the 2026 Equity Incentive Plan amendment, which increases the shares reserved for awards.")
    pr = ("Results exclude costs related to the departure of Michael Casey, the Company's former Chief Executive "
          "Officer, in the first quarter of fiscal 2025 and costs related to the departure of Douglas C.")
    assert [t for t, _ in events.classify(["2.02", "5.02", "8.01"], body, pr)] == ["earnings"]
    # real changes
    clx = ("Item 5.02. On October 28, 2026, the Board of Directors of The Clorox Company appointed Fabian Garcia to "
           "serve as the Chief Executive Officer (\"CEO\") of the Company, effective January 5, 2027.")
    assert "exec_change" in tags(["5.02", "7.01"], clx)
    assert "exec_change" in tags(["5.02"], "Item 5.02 Transition of Chief Financial Officer. On October 29, 2026, ...")
    assert "exec_change" in tags(["5.02"], "Item 5.02 Jane Doe notified the Board of her decision to retire as CFO.")
    assert "exec_change" in tags(["5.02"], "Item 5.02 John Roe, the Company's CEO, will step down on December 31.")
    # agreements
    assert tags(["1.01", "7.01"], "Item 1.01 Entry into a Material Definitive Agreement. On October 29, the Company "
                "entered into an Agreement and Plan of Merger with Rural Bancorp for $69.3 million.") == ["merger"]
    assert tags(["1.01", "2.03", "3.02"], "Item 1.01 Entry into a Material Definitive Agreement Investment "
                "Agreement On October 23, 2026, the Company entered into an Investment Agreement.") == ["dilution"]
    assert tags(["1.01", "2.03"], "Item 1.01 Entry into a Material Definitive Agreement. The Company entered into "
                "an amendment to its agreement with the lenders.") == ["financing"]
    assert events.award_amount("The agreement provides for $0.001 par value shares and a $40 million fee.") == 4e7
    snip = events.classify(["1.01"], "Item 1.01 Entry into a Material Definitive Agreement. ☐ On May 1 the Company "
                                     "entered into a supply agreement with Globex.")[0][1]
    assert "entered into a supply agreement" in snip and "Definitive" not in snip and "☐" not in snip


def test_agreement_size_gate_and_body_pick():
    db = _fresh_db()
    docs = {"g1": {"0001-index-headers.html": "<p>ACCESSION NUMBER: x ITEM INFORMATION: Entry into agreement</p>",
                   "acme-20261029.htm": "<p>Item 1.01. The Company entered into a supply agreement worth $3 million.</p>"},
            "g2": {"big.htm": "<p>Item 1.01. The Company entered into a supply agreement worth $50 million.</p>"}}
    e = FakeEdgar(docs)
    filings = {"g1": {"cik": "111", "company": "Acme", "items": ["1.01"]},
               "g2": {"cik": "111", "company": "Acme", "items": ["1.01"]}}
    out = events.process(db, e, lambda t: dict(M_OK, mcap=8e8), filings, TODAY)
    assert [o["acc"] for o in out] == ["g2"]                           # $3M of $800M is too small to alert
    assert ("g1", "acme-20261029.htm") in e.calls and not any("index" in n for _, n in e.calls)
    st = {r["accession"]: r["status"] for r in db.c.execute("SELECT * FROM events")}
    assert st["g1"] == "logged"


def test_move_since():
    import pandas as pd
    from sec_filing import market
    idx = pd.DatetimeIndex(["2026-10-27", "2026-10-28", "2026-10-29"]).tz_localize("America/New_York")
    closes = pd.Series([10.0, 11.0, 12.0], index=idx)
    saved = market._closes
    market._closes = lambda sym, start: closes
    try:
        r = market.move_since("ACME", "2026-10-29T07:15:02-04:00", 13.2)       # pre-market news: base = prior close
        assert r["base"] == 11.0 and r["base_date"] == "2026-10-28" and abs(r["move"] - 0.2) < 1e-9
        r = market.move_since("ACME", "2026-10-29T17:30:00-04:00", 13.2)       # after the close: base = that close
        assert r["base"] == 12.0 and abs(r["move"] - 0.1) < 1e-9
        assert market.move_since("ACME", "2026-10-29", 13.2)["base"] == 11.0   # date only
        assert market.move_since("ACME", "2026-10-27", 13.2) is None           # no close before the news
        assert market.move_since("ACME", "", 13.2) is None
    finally:
        market._closes = saved


def test_event_ai():
    import sqlite3
    from sec_filing import event_ai
    # replies are validated into a fixed shape
    assert event_ai.normalize("not json") is None
    v = event_ai.normalize('```json\n{"event_type":"MERGER","material":true,"direction":"up","confidence":"0.9",'
                           '"amount_usd":"69300000","summary":"Agreed to buy Rural Bancorp for $69.3M."}\n```')
    assert v["event_type"] == "merger" and v["direction"] == "neutral" and v["confidence"] == 0.9
    assert v["amount_usd"] == 69.3e6 and v["material"] is True
    assert event_ai.normalize({"event_type": "rocket", "confidence": 7})["event_type"] == "none"
    want = events.alert_tags()
    tg = [("agreement", "q")]
    mk = lambda **k: dict({"event_type": "merger", "material": True, "direction": "positive", "confidence": 0.9,
                           "amount_usd": None, "counterparty": "", "summary": "s"}, **k)
    assert event_ai.apply(mk(), tg, want) == (True, [("merger", ""), ("agreement", "q")])      # relabelled
    assert event_ai.apply(mk(material=False), tg, want)[0] is False                            # held
    assert event_ai.apply(mk(material=False, confidence=0.3), tg, want)[0] is True             # unsure -> keywords
    assert event_ai.apply(None, tg, want) == (None, tg)

    docs = {"z1": {"m.htm": "<p>Item 5.02. Jane Doe resigned as Chief Financial Officer of the Company.</p>"},
            "z2": {"n.htm": "<p>Item 5.02. Jane Doe resigned as Chief Financial Officer of the Company.</p>"},
            "z3": {"o.htm": "<p>Item 5.02. Jane Doe resigned as Chief Financial Officer of the Company.</p>"}}
    f = lambda acc: {acc: {"cik": "111", "company": "Acme", "items": ["5.02"]}}
    saved = (event_ai._chat, C.EVENT_AI_MODE, C.LLM_API_KEY, C.LLM_MODEL)
    calls = []
    C.LLM_API_KEY, C.LLM_MODEL = "k", "m"
    try:
        db = _fresh_db()
        # on: a confident "not material" holds the alert, and the verdict is stored
        C.EVENT_AI_MODE = "on"
        event_ai._chat = lambda sys_, user: calls.append(json.loads(user)) or json.dumps(mk(
            event_type="exec_change", material=False, summary="Interim CFO swap, routine."))
        assert events.process(db, FakeEdgar(docs), lambda t: M_OK, f("z1"), TODAY) == []
        row = db.c.execute("SELECT status,note,ai FROM events WHERE accession='z1'").fetchone()
        assert row[0] == "ai_filtered" and "routine" in row[1] and json.loads(row[2])["material"] is False
        assert calls[0]["ticker"] == "ACME" and "resigned as Chief Financial Officer" in calls[0]["filing_text"]
        # on: material -> alert carries the AI summary instead of the keyword quote
        event_ai._chat = lambda sys_, user: json.dumps(mk(event_type="exec_change", direction="negative",
                                                          summary="CFO Jane Doe resigned effective immediately."))
        out = events.process(db, FakeEdgar(docs), lambda t: M_OK, f("z2"), TODAY)
        assert len(out) == 1 and out[0]["ai"]["direction"] == "negative"
        labels = [(t, events.TAGS[t][0], events.TAGS[t][1], sn) for t, sn in out[0]["tags"]]
        msg = telegram.format_event(dict(out[0], link="https://x.test"), labels)
        assert "🤖 🔴 CFO Jane Doe resigned" in msg and "“" not in msg
        # API failure -> keyword decision stands
        def boom(*a):
            raise RuntimeError("503")
        event_ai._chat = boom
        assert len(events.process(db, FakeEdgar(docs), lambda t: M_OK, f("z3"), TODAY)) == 1
        # shadow: verdict stored, alert unchanged and no AI line
        C.EVENT_AI_MODE = "shadow"
        event_ai._chat = lambda sys_, user: json.dumps(mk(material=False))
        docs["z4"] = docs["z1"]
        out = events.process(db, FakeEdgar(docs), lambda t: M_OK, f("z4"), TODAY)
        assert len(out) == 1 and out[0]["ai"] is None
        assert json.loads(db.c.execute("SELECT ai FROM events WHERE accession='z4'").fetchone()[0])["material"] is False
    finally:
        event_ai._chat, C.EVENT_AI_MODE, C.LLM_API_KEY, C.LLM_MODEL = saved

    # an existing database (events table without the ai column) is migrated
    d = tempfile.mkdtemp()
    con = sqlite3.connect(os.path.join(d, "insider.db"))
    con.execute("CREATE TABLE events(id INTEGER PRIMARY KEY AUTOINCREMENT, accession TEXT UNIQUE, cik TEXT, ticker TEXT,"
                " company TEXT, items TEXT, tags TEXT DEFAULT '[]', seen_date TEXT, status TEXT, note TEXT DEFAULT '',"
                " amount REAL, price REAL, alerted_at REAL, last_outcome_check TEXT DEFAULT '', ret5 REAL, ret20 REAL,"
                " ret60 REAL, spy5 REAL, spy20 REAL, spy60 REAL)")
    con.commit(); con.close()
    DB(d).save_event("m1", "1", "A", "A", [], [], "2026-10-30", "logged", ai='{"x":1}')


def test_price_sources():
    import sys as _sys, types, logging
    import pandas as pd
    from zoneinfo import ZoneInfo
    from sec_filing import prices, market
    ET = ZoneInfo("America/New_York")
    # timestamps: Alpaca sends nanoseconds + Z
    assert prices.parse_ts("2026-10-30T19:59:59.875519744Z").microsecond == 875519
    assert prices.parse_ts("bad") is None

    # staleness: open market needs a recent trade; closed market accepts anything since the last close
    open_now = dt.datetime(2026, 10, 30, 11, 0, tzinfo=ET)
    assert market.is_fresh(open_now - dt.timedelta(minutes=10), open_now, dt.date(2026, 10, 29))
    assert not market.is_fresh(open_now - dt.timedelta(hours=2), open_now, dt.date(2026, 10, 29))
    night = dt.datetime(2026, 10, 30, 23, 0, tzinfo=ET)
    assert market.is_fresh(dt.datetime(2026, 10, 30, 19, 55, tzinfo=ET), night, dt.date(2026, 10, 30))
    assert not market.is_fresh(dt.datetime(2026, 10, 29, 19, 55, tzinfo=ET), night, dt.date(2026, 10, 30))
    premkt = dt.datetime(2026, 10, 30, 8, 0, tzinfo=ET)
    assert market.is_fresh(dt.datetime(2026, 10, 30, 7, 30, tzinfo=ET), premkt, dt.date(2026, 10, 29))

    today = dt.datetime.now(ET).date()
    days = pd.bdate_range(end=today - dt.timedelta(days=3), periods=90)
    hist = [{"date": d.strftime("%Y-%m-%d"), "close": 10 + i * 0.05, "volume": 600000} for i, d in enumerate(days)]

    class R:
        def __init__(self, code, body):
            self.status_code, self._b = code, body
        def json(self):
            return self._b
    calls, routes = [], {}
    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, dict(params or {}), dict(headers or {})))
        for k, v in routes.items():
            if k in url:
                return v
        return R(404, {})

    class YT:                                   # Yahoo stand-in: history fallback + no intraday + market cap
        def __init__(self, s):
            pass
        def history(self, period=None, interval=None, start=None, prepost=False, auto_adjust=False):
            if interval:
                raise RuntimeError("no intraday")
            idx = pd.bdate_range(end=today - dt.timedelta(days=1), periods=80, tz="America/New_York")
            return pd.DataFrame({"Close": [20.0] * 80, "Volume": [1e6] * 80}, index=idx)
        fast_info = {"market_cap": 1.5e9}

    saved = (prices._s.get, C.ALPACA_API_KEY_ID, C.ALPACA_API_SECRET_KEY, C.FMP_API_KEY, _sys.modules.get("yfinance"),
             dict(market._cache))
    prices._s.get = fake_get
    _sys.modules["yfinance"] = types.SimpleNamespace(Ticker=YT)
    market._cache.clear()
    try:
        # 1) FMP history (stable list format) + FMP market cap + Alpaca day bar newer than the history
        C.ALPACA_API_KEY_ID, C.ALPACA_API_SECRET_KEY, C.FMP_API_KEY = "kid", "sec", "fk"
        routes.update({
            "historical-price-eod/full": R(200, hist),
            "/quote": R(200, [{"symbol": "ACME", "price": 14.9, "marketCap": 8.1e8, "previousClose": 14.4}]),
            "/snapshot": R(200, {"latestTrade": {"p": 15.2, "t": "2026-10-01T15:00:00.123456789Z"},
                                  "dailyBar": {"t": dt.datetime.combine(today, dt.time(4, 0)).isoformat() + "Z",
                                               "c": 15.0, "v": 1200}}),
        })
        m = market._fetch("ACME")
        assert m["mcap"] == 8.1e8 and m["last_close"] == 15.0          # today's close added from Alpaca's day bar
        assert m["price"] == 15.0 and m["session"] == "close"          # old trade ignored as stale
        assert abs(m["adv20"] - sum((10 + i * 0.05) * 6e5 for i in range(70, 90)) / 20) < 1   # FMP volumes only
        assert "FMP history" in m["src"]
        snap_call = [c for c in calls if "/snapshot" in c[0]][0]
        assert snap_call[1] == {"feed": "iex"} and snap_call[2]["APCA-API-KEY-ID"] == "kid"
        assert [c for c in calls if "/quote" in c[0]][0][1] == {"symbol": "ACME", "apikey": "fk"}

        # 2) v3-style {"historical": [...]} newest-first is also understood
        routes["historical-price-eod/full"] = R(200, {"historical": list(reversed(hist))})
        h = prices.fmp_history("ACME", "2026-01-01")
        assert h.index.is_monotonic_increasing and len(h) == 90

        # 3) rejected keys -> Yahoo for everything, one warning per source
        routes.update({"historical-price-eod/full": R(403, {"Error Message": "plan"}), "/quote": R(401, {}),
                       "/snapshot": R(401, {})})
        prices._warned.clear()
        logged = []
        h_ = logging.Handler(); h_.emit = lambda rec: logged.append(rec.getMessage())
        logging.getLogger("prices").addHandler(h_)
        m = market._fetch("ACME")
        m2 = market._fetch("ACME")
        logging.getLogger("prices").removeHandler(h_)
        assert m["last_close"] == 20.0 and m["mcap"] == 1.5e9 and "Yahoo history" in m["src"]
        assert len([x for x in logged if "Alpaca rejected" in x]) == 1 and len([x for x in logged if "FMP" in x]) == 1

        # 4) no keys at all -> no requests to Alpaca/FMP
        C.ALPACA_API_KEY_ID = C.FMP_API_KEY = ""
        calls.clear()
        assert market._fetch("ACME")["last_close"] == 20.0 and calls == []
        assert prices.describe() == "current price=Yahoo · history+market cap=Yahoo · fallback=Yahoo"
    finally:
        prices._s.get, C.ALPACA_API_KEY_ID, C.ALPACA_API_SECRET_KEY, C.FMP_API_KEY = saved[:4]
        if saved[4] is not None:
            _sys.modules["yfinance"] = saved[4]
        market._cache.clear(); market._cache.update(saved[5])


def test_dod_listing_war_gov_relative_links():
    listing = ('<a href="/Newsroom/Contracts/Contract/Article/4300001/">Contracts For Oct. 29, 2026</a>'
               '<a href="/Newsroom/Contracts/Contract/Article/4300001/">dup</a>'
               '<a href="https://www.war.gov/Newsroom/Contracts/Contract/Article/4299000/">older</a>')
    page = ('<h1>Department of War Contracts For Oct. 29, 2026</h1><p>ARMY</p><p>Acme Defense Systems, Inc., '
            'Huntsville, Alabama, was awarded a $48,500,000 contract for widgets.</p>')
    seen, saved = [], (feeds._get, C.DOD_CONTRACTS_URL)
    C.DOD_CONTRACTS_URL = "https://www.war.gov/Newsroom/Contracts/"
    feeds._get = lambda url, **k: seen.append(url) or (listing if url == C.DOD_CONTRACTS_URL else page)
    try:
        recs = feeds.dod(TODAY)
        assert seen[1:] == ["https://www.war.gov/Newsroom/Contracts/Contract/Article/4300001/",
                            "https://www.war.gov/Newsroom/Contracts/Contract/Article/4299000/"]
        assert recs[0]["date"] == "2026-10-29" and recs[0]["amount"] == 48.5e6
        feeds._get = lambda url, **k: "<html>no links</html>"
        assert feeds.dod(TODAY) == []
    finally:
        feeds._get, C.DOD_CONTRACTS_URL = saved


def test_bad_number_setting_falls_back():
    os.environ.update(X_BAD="X_BAD", X_COMMA="10,000,000", X_SCI="1e7")
    try:
        assert C._i("X_BAD", 360) == 360 and C._f("X_COMMA", 0) == 1e7 and C._i("X_SCI", 0) == 10000000
        assert C._f("X_MISSING", 0.5) == 0.5
    finally:
        for k in ("X_BAD", "X_COMMA", "X_SCI"):
            os.environ.pop(k)


# ---------------------------------------------------------------- pre-market press releases
import contextlib


@contextlib.contextmanager
def cfg(**kw):
    old = {k: getattr(C, k) for k in kw}
    for k, v in kw.items():
        setattr(C, k, v)
    try:
        yield
    finally:
        for k, v in old.items():
            setattr(C, k, v)


def test_press_release_tags():
    from sec_filing import pressreleases as PR
    t = lambda title, text="": [x for x, _ in PR.tag(title, text)]
    assert t("Acme Pharma Announces FDA Approval of ZORVAX for Moderate Disease") == ["fda_approval"]
    assert t("Acme Receives Complete Response Letter from FDA") == ["fda_negative"]
    assert t("Acme Plans to Seek FDA Approval Next Year") == []
    assert t("Nano Widgets to Acquire Gizmo Corp for $1.2 Billion") == ["merger"]
    assert t("Nano Widgets Completes Acquisition of Gizmo Corp") == ["acquisition"]
    assert t("Acme Appoints Jane Doe as Chief Executive Officer") == ["exec_change"]
    assert t("Acme Defense Awarded $400 Million Department of Defense Contract") == ["gov_contract"]
    assert t("Acme Reports Third Quarter 2026 Financial Results") == ["earnings"]
    assert t("Acme Files Voluntary Chapter 11 Petitions") == ["bankruptcy"]
    assert t("Acme to Present at Investor Conference") == []


def test_press_release_scan_modes():
    from sec_filing import pressreleases as PR
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
    now = dt.datetime(2026, 11, 2, 4, 10, tzinfo=ET).timestamp()
    def item(n, minutes_ago, title, sym="acme"):
        pub = dt.datetime.fromtimestamp(now - minutes_ago * 60, ET).strftime("%Y-%m-%d %H:%M:%S")
        return {"symbol": sym, "publishedDate": pub, "title": title, "text": "", "site": "Business Wire",
                "url": f"https://example.com/{n}"}
    assert abs(PR.published_ts("2026-11-02 04:00:00") - dt.datetime(2026, 11, 2, 4, 0, tzinfo=ET).timestamp()) < 1
    assert PR.published_ts("") is None and PR.published_ts("junk") is None
    tiny = dict(M_OK, mcap=4e7)
    mk = lambda t: {"TINY": tiny, "BUST": dict(tiny, price=0.5), "NONE": None}.get(t, M_OK)
    sent = []
    db = DB(tempfile.mkdtemp())
    deal = " to Acquire Gizmo Corp"
    scan = lambda its: PR.scan(db, its, now, sent.append, mk)
    status = lambda rows: [r["status"] for r in rows]
    assert C.PR_MODE == "alert"                                       # alerts are on by default
    with cfg(PR_MAX_AGE_MIN=120, PR_MAX_ALERTS_PER_DAY=3, PR_DEDUP_HOURS=18):
        # first poll: baseline only, nothing sent, nothing counted
        assert scan([item(1, 5, "Acme" + deal)]) == [] and sent == []
        assert db.c.execute("SELECT status FROM press_releases").fetchone()[0] == "baseline"
        # repeat ignored; tagged release alerts; untagged only logs; backlog is stale
        new = scan([item(1, 5, "Acme" + deal), item(2, 2, "Acme" + deal), item(3, 1, "Acme to Present at Conference"),
                    item(4, 300, "Acme" + deal)])
        assert status(new) == ["alerted", "new"] and len(sent) == 1
        assert "PRESS RELEASE — ACME" in sent[0] and "Merger" in sent[0] and abs(new[0]["lag_sec"] - 120) < 1
        assert db.c.execute("SELECT status FROM press_releases WHERE url LIKE '%/4'").fetchone()[0] == "stale"
        # same ticker + same news type again = duplicate; a different news type still alerts
        new = scan([item(5, 1, "Acme Announces Merger Agreement Update"),
                    item(6, 1, "Acme Appoints Jane Doe as Chief Executive Officer")])
        assert status(new) == ["duplicate", "alerted"] and len(sent) == 2
        # market gates: small cap and no data rejected; bankruptcy skips the size gates
        new = scan([item(7, 1, "Tiny" + deal, "tiny"), item(8, 1, "None" + deal, "none"),
                    item(9, 1, "Bust Files Voluntary Chapter 11 Petitions", "bust")])
        assert status(new) == ["rejected_market", "rejected_market", "alerted"] and len(sent) == 3
        assert "market cap" in db.c.execute("SELECT note FROM press_releases WHERE url LIKE '%/7'").fetchone()[0]
        # daily cap (3) reached
        assert status(scan([item(10, 1, "Other" + deal, "othr")])) == ["capped"]
    with cfg(PR_MAX_ALERTS_PER_DAY=0):
        db.set_kv("paused", "1")
        assert status(scan([item(11, 1, "Paws" + deal, "paws")])) == ["paused"]
        db.set_kv("paused", "0")
    with cfg(PR_MODE="log"):
        assert status(scan([item(12, 1, "Logd" + deal, "logd")])) == ["new"] and len(sent) == 3
        line = PR.summary(db, now - 3600)
        assert "Press releases (log): 10 new today" in line and "lag median" in line
    # the other direction: an 8-K or FDA record with news already sent from the release is not alerted again
    assert db.pr_alert_tags("ACME", now - 3600) == {"merger", "exec_change"}
    assert feeds._already_alerted_fda(db, "ACME", TODAY) is False
    db.pr_add({"key": "fda", "symbol": "VRTX", "published": "", "title": "", "site": "", "url": "",
               "tags": '["fda_approval"]', "first_seen": now, "lag_sec": 0, "status": "alerted", "alerted_at": time.time()})
    assert feeds._already_alerted_fda(db, "VRTX", schedule_today())


def schedule_today():
    from sec_filing import schedule
    return schedule.now_local().date()


def test_press_release_fetch_errors():
    from sec_filing import pressreleases as PR
    class R:
        def __init__(self, code, body, text=""):
            self.status_code, self._b, self.text = code, body, text
        def json(self):
            return self._b
    saved = PR._s.get
    try:
        for resp in (R(403, {}, "Restricted Endpoint"), R(200, {"Error Message": "Exclusive Endpoint"}), R(500, {})):
            PR._s.get = lambda *a, _r=resp, **k: _r
            PR._warned.clear()
            assert PR.fetch() is None
        PR._s.get = lambda *a, **k: R(200, [{"symbol": "A", "title": "x"}, "junk"])
        assert PR.fetch() == [{"symbol": "A", "title": "x"}]
    finally:
        PR._s.get = saved


def test_press_release_window_and_dst():
    from sec_filing import schedule as S
    from zoneinfo import ZoneInfo
    CT = ZoneInfo("America/Chicago")
    at = lambda *a: dt.datetime(*a, tzinfo=CT)
    with cfg(ACTIVE_START="05:00", ACTIVE_END="21:00", ACTIVE_DAYS="0,1,2,3,4", SCHEDULE_TZ="America/Chicago",
             HONOR_HOLIDAYS=True):
        assert S.is_active(at(2026, 11, 2, 3, 0), start="03:00") and not S.is_active(at(2026, 11, 2, 3, 0))
        assert not S.is_active(at(2026, 11, 2, 2, 59), start="03:00")
        assert not S.is_active(at(2026, 11, 26, 4, 0), start="03:00")        # Thanksgiving
        # Friday 10:04 pm CDT -> Monday 5:00 am CST spans the clock change: real time, not wall time
        n = dt.datetime(2026, 10, 31, 3, 4, tzinfo=dt.timezone.utc)
        w = S.seconds_until_active(n)
        assert (n + dt.timedelta(seconds=w)).astimezone(CT) == at(2026, 11, 2, 5, 0)
        w = S.seconds_until_active(n, start="03:00")
        assert (n + dt.timedelta(seconds=w)).astimezone(CT) == at(2026, 11, 2, 3, 0)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
