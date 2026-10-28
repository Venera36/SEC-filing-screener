import os, sys, json, sqlite3, tempfile, contextlib, datetime as dt
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["SEC_USER_AGENT"] = "test test@example.com"
from insider_bot import config as C, signals, activist, earnings_drift, analysis, control, telegram
from insider_bot import market, offerings, schedule, main as M
from insider_bot.db import DB
from insider_bot.signals import SignalResult


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


# ---------- database ----------
def test_db_migration_and_candidates():
    d = tempfile.mkdtemp()
    con = sqlite3.connect(os.path.join(d, "insider.db"))          # a database created by the first release
    con.executescript("""CREATE TABLE tracked(id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, cik TEXT,
      alert_date TEXT, alert_price REAL, score INTEGER, total REAL, offering_notified INTEGER DEFAULT 0,
      last_offering_check REAL DEFAULT 0, last_outcome_check TEXT DEFAULT '', ret5 REAL, ret20 REAL, ret60 REAL);
      INSERT INTO tracked(ticker,cik,alert_date,alert_price,score,total) VALUES('OLD','1','2026-10-01',10,70,1e6);""")
    con.commit(); con.close()
    db = DB(d)
    row = db.all_tracked()[0]
    assert row["ticker"] == "OLD" and row["spy5"] is None            # old row survives, new columns exist
    db.update_tracked(row["id"], spy5=0.01)

    assert db.get_kv("paused") is None
    db.set_kv("paused", "1"); db.set_kv("paused", "0")
    assert db.get_kv("paused") == "0"

    i1 = db.log_candidate("ACME", "1", "2026-10-20", "2026-10-26", "below_score", 55, 63, 7e5, 10.0)
    i2 = db.log_candidate("ACME", "1", "2026-10-20", "2026-10-27", "confluence_missing", 55, 63, 7e5, 10.5)
    assert i1 == i2 and len(db.all_candidates()) == 1
    r = db.all_candidates()[0]
    assert r["status"] == "confluence_missing" and r["eval_date"] == "2026-10-26" and r["price"] == 10.0
    db.update_candidate(i1, ret5=0.02, spy5=0.01)
    db.log_candidate("ACME", "1", "2026-10-20", "2026-10-28", "alerted", 70, 78, 7e5, 11.0)
    r = db.all_candidates()[0]
    assert r["status"] == "alerted" and r["eval_date"] == "2026-10-28" and r["price"] == 11.0 and r["ret5"] is None
    db.log_candidate("ACME", "1", "2026-10-20", "2026-10-29", "below_score", 50, 50, 7e5, 9.0)
    assert db.all_candidates()[0]["status"] == "alerted"             # never downgraded
    db.set_decision(i1, "approved")
    assert db.all_candidates()[0]["decision"] == "approved"
    try:
        db.update_candidate(i1, status="x"); assert False
    except ValueError:
        pass


# ---------- signals ----------
def test_combine_modes():
    fired = [SignalResult("activist", True, 10, "13D")]
    none = [SignalResult("activist", False)]
    assert signals.combine(62, fired, "shadow", 1) == {"score": 62, "final": 72, "fired": fired, "confirmed_ok": True}
    assert signals.combine(62, fired, "boost", 1)["score"] == 72
    assert signals.combine(62, none, "boost", 1)["confirmed_ok"] is True
    assert signals.combine(62, none, "require", 1)["confirmed_ok"] is False
    assert signals.combine(62, fired, "require", 1)["confirmed_ok"] is True
    assert signals.combine(62, fired, "require", 2)["confirmed_ok"] is False
    assert signals.combine(95, fired, "boost", 1)["score"] == 100


def test_activist():
    class E:
        def __init__(self, forms, dates): self.d = {"filings": {"recent": {"form": forms, "filingDate": dates}}}
        def submissions(self, cik): return self.d
    e = E(["SCHEDULE 13D", "SC 13D/A", "SC 13G", "4", "SC 13D"], ["2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-08-01"])
    assert activist.find(e, "1", "2026-10-01") == ["SCHEDULE 13D 2026-10-20"]       # amendments, 13G, old filings ignored
    today = dt.date(2026, 10, 28)
    assert activist.check(e, {"cik": "1"}, {}, today).fired
    assert not activist.check(E(["4"], ["2026-10-20"]), {"cik": "1"}, {}, today).fired
    class Down:
        def submissions(self, cik): return None
    r = activist.check(Down(), {"cik": "1"}, {}, today)
    assert not r.fired and r.error                                              # data problem is an error, not a signal


def _bars(n=30):
    start = dt.date(2026, 9, 1)
    dates = [start + dt.timedelta(i) for i in range(n)]
    closes = [10.0] * 20 + [10.8, 11.0] + [11.0] * (n - 22)
    vols = [1000.0] * 20 + [3000.0, 1500.0] + [1000.0] * (n - 22)
    return dates, closes, vols


def test_drift_assess():
    dates, closes, vols = _bars()
    ed = dates[20]
    assert earnings_drift.assess(ed, 0.15, dates, closes, vols, 11.0)[0]
    assert not earnings_drift.assess(ed, 0.05, dates, closes, vols, 11.0)[0]            # small beat
    assert not earnings_drift.assess(ed, 0.15, dates, closes, vols, 9.5)[0]             # gave back the gain
    assert not earnings_drift.assess(ed, 0.15, dates, closes, [1000.0] * 30, 11.0)[0]   # no volume
    flat = [10.0] * 30
    assert not earnings_drift.assess(ed, 0.15, dates, flat, vols, 10.0)[0]              # no price reaction
    assert not earnings_drift.assess(dates[2], 0.15, dates, closes, vols, 11.0)[0]      # too little history
    today = dates[-1]
    rows = [(dates[20], 0.15), (dates[5], 0.5), (today + dt.timedelta(5), None), (dates[0] - dt.timedelta(90), 0.4)]
    assert earnings_drift._latest_report(rows, today) == (dates[20], 0.15)


# ---------- AI review ----------
def test_analysis():
    assert analysis.clean_text("<html><script>x=1</script><body>Hello <b>World</b>&amp;co</body></html>", 100) == "Hello World &co"
    assert len(analysis.clean_text("a" * 500, 50)) == 50
    n = analysis.normalize('```json\n{"red_flags":[{"type":"offering","detail":"ATM program"}],"veto":true,"confidence":1.7,"summary":"x"}\n```')
    assert n["veto"] is True and n["confidence"] == 1.0 and n["red_flags"][0]["type"] == "offering"
    assert analysis.normalize("not json") is None and analysis.normalize("[1]") is None
    assert analysis.normalize({"veto": "yes"})["veto"] is False                       # only a real boolean true vetoes
    with cfg(AI_VETO_MIN_CONF=0.6):
        assert analysis.should_veto({"veto": True, "confidence": 0.7})
        assert not analysis.should_veto({"veto": True, "confidence": 0.3})
        assert not analysis.should_veto(None)

    class E:
        asked = []
        def submissions(self, cik):
            return {"filings": {"recent": {"form": ["8-K", "4", "10-Q"], "filingDate": ["2026-10-20", "2026-10-21", "2026-10-01"],
                    "items": ["3.02", "", ""], "accessionNumber": ["0001-26-000001", "x", "y"], "primaryDocument": ["a.htm", "b.xml", "c.htm"]}}}
        def filing_text(self, path):
            E.asked.append(path); return "<p>Private placement announced</p>"
    listing, ex = analysis.recent_filings(E(), "1234", "2026-10-10", 2, 100)
    assert [x["form"] for x in listing] == ["8-K", "4"] and ex[0]["text"] == "Private placement announced"
    assert E.asked == ["edgar/data/1234/000126000001/a.htm"]

    c = {"ticker": "ACME", "issuer": "Acme", "cik": "1234", "first_date": "2026-10-20", "last_date": "2026-10-21", "avg_price": 12.4,
         "insiders": [{"name": "Jane", "title": "CEO", "value": 5e5}]}
    m = dict(price=12.9, ret20=0.02, ret60=-0.05, adv20=9e6, mcap=8e8)
    orig = analysis._chat
    try:
        seen = {}
        analysis._chat = lambda s, u: seen.update(user=u) or '{"red_flags":[],"veto":false,"confidence":0.4,"summary":"ok","catalyst":""}'
        out = analysis.analyze(E(), c, m, [SignalResult("activist", True, 10, "13D")], ["w"])
        assert out["summary"] == "ok" and "recent_8k_excerpts" in seen["user"] and "Private placement" in seen["user"]
        analysis._chat = lambda s, u: (_ for _ in ()).throw(RuntimeError("api down"))
        assert analysis.analyze(E(), c, m, [], []) is None                            # failure never raises
    finally:
        analysis._chat = orig


# ---------- evaluate(): every mode ----------
M_OK = dict(price=12.9, last_close=12.9, session="close", ext_move=0.0, adv20=9e6, mcap=8e8, ret20=0.02, ret60=-0.05, pct_of_52w_high=0.8)
FIRED = [SignalResult("activist", True, 10, "new Schedule 13D: SCHEDULE 13D 2026-10-20")]
NOT_FIRED = [SignalResult("activist", False, 0, "none")]


def _seed(db, *tickers):
    day = (schedule.now_local().date() - dt.timedelta(days=3)).isoformat()
    for i, tk in enumerate(tickers):
        mk = lambda owner, title, sh, px, after: dict(cik=str(100 + i), ticker=tk, issuer=tk + " Inc", owner=owner, title=title, tier=3,
                                                       tx_date=day, shares=sh, price=px, value=sh * px, owned_after=after, indirect=0)
        db.save_filing(f"acc-{tk}-1", [mk("Jane CEO", "CEO", 40000, 12.5, 140000)])
        db.save_filing(f"acc-{tk}-2", [mk("Bob CFO", "CFO", 20000, 12.4, 60000)])


@contextlib.contextmanager
def harness(sig_results, market_data=None):
    sent = []
    saved = (market.get, signals.run_all, offerings.find, telegram.send)
    market.get = lambda t: market_data or M_OK
    signals.run_all = lambda *a: sig_results
    offerings.find = lambda *a: []
    telegram.send = lambda text, buttons=None: sent.append((text, buttons))
    try:
        yield sent
    finally:
        market.get, signals.run_all, offerings.find, telegram.send = saved


def _statuses(db):
    return {r["ticker"]: r["status"] for r in db.all_candidates()}


def test_evaluate_shadow_is_unchanged_behaviour():
    db = DB(tempfile.mkdtemp()); _seed(db, "ACME")
    with harness(NOT_FIRED) as sent, cfg(SIGNALS_MODE="shadow"):
        M.evaluate(db, None, False)
    assert len(sent) == 1 and "INSIDER BUYING" in sent[0][0] and "Also:" not in sent[0][0]     # nothing extra shown
    assert [t for t, _ in sent[0][1]] == ["✅ Approve", "⏭ Skip"]
    c = db.all_candidates()[0]
    assert c["status"] == "alerted" and json.loads(c["signals"])[0]["name"] == "activist"      # but it IS logged
    assert len(db.all_tracked()) == 1
    assert sent[0][1][0][1] == f"d:a:{c['id']}"
    with harness(NOT_FIRED) as sent2:
        M.evaluate(db, None, False)
    assert sent2 == []                                                                          # de-dup still works


def test_evaluate_require_mode():
    db = DB(tempfile.mkdtemp()); _seed(db, "ACME")
    with harness(NOT_FIRED) as sent, cfg(SIGNALS_MODE="require", MIN_CONFIRMING_SIGNALS=1):
        M.evaluate(db, None, False)
    assert sent == [] and _statuses(db) == {"ACME": "confluence_missing"}
    with harness(FIRED) as sent, cfg(SIGNALS_MODE="require", MIN_CONFIRMING_SIGNALS=1):
        M.evaluate(db, None, False)
    assert len(sent) == 1 and "Also: activist" in sent[0][0] and _statuses(db) == {"ACME": "alerted"}


def test_evaluate_cap_pause_gate_veto():
    db = DB(tempfile.mkdtemp()); _seed(db, "AAA", "BBB")
    with harness(NOT_FIRED) as sent, cfg(MAX_ALERTS_PER_DAY=1):
        M.evaluate(db, None, False)
    assert len(sent) == 1 and sorted(_statuses(db).values()) == ["alerted", "capped"]

    db = DB(tempfile.mkdtemp()); _seed(db, "AAA")
    db.set_kv("paused", "1")
    with harness(NOT_FIRED) as sent:
        M.evaluate(db, None, False)
    assert sent == [] and db.all_candidates() == []

    db = DB(tempfile.mkdtemp()); _seed(db, "AAA")
    with harness(NOT_FIRED, dict(M_OK, price=5.5, adv20=1e6)) as sent:
        M.evaluate(db, None, False)
    assert sent == [] and _statuses(db) == {"AAA": "rejected_market"}                          # near-miss logged with the reason
    assert "ADV20" in db.all_candidates()[0]["note"]

    db = DB(tempfile.mkdtemp()); _seed(db, "AAA")
    veto = {"red_flags": [{"type": "offering", "detail": "ATM"}], "veto": True, "confidence": 0.9, "summary": "ATM filed", "catalyst": ""}
    orig = analysis.analyze
    analysis.analyze = lambda *a: veto
    try:
        with harness(NOT_FIRED) as sent, cfg(AI_MODE="veto", LLM_API_KEY="k", LLM_MODEL="m"):
            M.evaluate(db, None, False)
        assert sent == [] and _statuses(db) == {"AAA": "ai_veto"}
        db = DB(tempfile.mkdtemp()); _seed(db, "AAA")
        with harness(NOT_FIRED) as sent, cfg(AI_MODE="shadow", LLM_API_KEY="k", LLM_MODEL="m"):
            M.evaluate(db, None, False)
        assert len(sent) == 1 and "AI note" not in sent[0][0]                                   # shadow: logged, not shown
        assert json.loads(db.all_candidates()[0]["ai"])["veto"] is True
    finally:
        analysis.analyze = orig


def test_report_and_followups_run():
    db = DB(tempfile.mkdtemp())
    db.log_candidate("AAA", "1", "2026-09-01", "2026-09-02", "below_score", 50, 50, 1e6, 10.0)
    i = db.all_candidates()[0]["id"]
    db.update_candidate(i, ret20=0.05, spy20=0.01)
    saved = market.forward_returns
    market.forward_returns = lambda *a, **k: {}
    try:
        M.follow_up_candidates(db)
    finally:
        market.forward_returns = saved
    M.report(db)


# ---------- Telegram control ----------
def test_control():
    db = DB(tempfile.mkdtemp())
    db.log_candidate("AAA", "1", "2026-10-20", "2026-10-26", "alerted", 70, 70, 1e6, 10.0)
    cid = db.all_candidates()[0]["id"]
    log = []
    saved = (telegram.send, telegram.answer_callback, telegram.clear_buttons)
    telegram.send = lambda t, b=None: log.append(("send", t))
    telegram.answer_callback = lambda i, t: log.append(("ans", t))
    telegram.clear_buttons = lambda c, m: log.append(("clear", m))
    try:
        with cfg(TELEGRAM_CHAT_ID="42"):
            cb = lambda data, chat="42": {"update_id": 1, "callback_query": {"id": "x", "data": data, "message": {"message_id": 7, "chat": {"id": int(chat)}}}}
            control.handle_update(db, cb(f"d:s:{cid}", chat="999"))                    # stranger is ignored
            assert db.all_candidates()[0]["decision"] == "" and log == []
            control.handle_update(db, cb(f"d:a:{cid}"))
            assert db.all_candidates()[0]["decision"] == "approved" and ("clear", 7) in log
            control.handle_update(db, cb("d:a:abc"))                                   # malformed data is rejected
            msg = lambda t, chat=42: {"update_id": 2, "message": {"text": t, "chat": {"id": chat}}}
            control.handle_update(db, msg("/pause@MyBot"))
            assert db.get_kv("paused") == "1"
            control.handle_update(db, msg("/resume"))
            assert db.get_kv("paused") == "0"
            control.handle_update(db, msg("/status"))
            assert any(k == "send" and "Sent today" in t for k, t in log)
            before = len(log)
            control.handle_update(db, msg("/pause", chat=555))                         # stranger can't pause
            assert db.get_kv("paused") == "0" and len(log) == before
    finally:
        telegram.send, telegram.answer_callback, telegram.clear_buttons = saved


def test_format_alert_extras():
    c = {"ticker": "ACME", "issuer": "Acme", "cik": "1", "total": 7.48e5, "first_date": "a", "last_date": "b", "avg_price": 12.4,
         "insiders": [{"name": "J", "title": "CEO", "value": 5e5, "stake_pct": None}]}
    msg = telegram.format_alert(c, dict(M_OK, mcap=8e8), 80, ["x"], [], FIRED,
                                {"summary": "Looks clean <b>", "red_flags": [{"type": "dilution", "detail": "d"}]})
    assert "Also: activist" in msg and "AI note" in msg and "&lt;b&gt;" in msg and "dilution" in msg


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
