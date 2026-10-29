"""Tests for 8-K screening, government contracts, FDA approvals and the name matcher. No network."""
import os, sys, json, tempfile, datetime as dt
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
                                        "company": "Acme Defense Systems, Inc.", "items": ["8.01", "9.01"]}}
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
    saved = (M._push, M.market.get, M.events.gather)
    M._push = lambda msg, dry, buttons=None, chat_id=None: sent.append(msg)
    M.market.get = lambda t: M_OK
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
        assert db.feed_alerts_since(0) == 1
    finally:
        M._push, M.market.get, M.events.gather = saved


def test_scan_feeds_seed_due_and_cap():
    db = _fresh_db()
    sent = []
    rec = lambda k, amt: {"source": "dod", "key": k, "date": "2026-10-28", "company": "Acme Defense Systems, Inc.",
                          "agency": "Army", "amount": amt, "multi": False, "detail": "d", "link": ""}
    batches = [[rec("k0", 5e7)], [rec("k1", 5e7)]]
    saved = (M._push, M.market.get, dict(feeds.SOURCES), C.FEEDS_ENABLED)
    M._push = lambda msg, dry, buttons=None, chat_id=None: sent.append(msg)
    M.market.get = lambda t: M_OK
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
        assert db.c.execute("SELECT status FROM awards WHERE key='k1'").fetchone()[0] == "alerted"
    finally:
        M._push, M.market.get = saved[0], saved[1]
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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
