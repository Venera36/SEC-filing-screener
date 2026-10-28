import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ["SEC_USER_AGENT"] = "test test@example.com"
from insider_bot.parser import parse_form4
from insider_bot import screener

def filing(name, title, shares, price, after, officer=1):
    return f"""<SEC-DOCUMENT><XML><ownershipDocument>
<issuer><issuerCik>1</issuerCik><issuerName>Acme</issuerName><issuerTradingSymbol>ACME</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerName>{name}</rptOwnerName></reportingOwnerId>
<reportingOwnerRelationship><isOfficer>{officer}</isOfficer><officerTitle>{title}</officerTitle></reportingOwnerRelationship></reportingOwner>
<nonDerivativeTable>
<nonDerivativeTransaction><transactionDate><value>2026-10-20</value></transactionDate>
<transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>{shares}</value></transactionShares>
<transactionPricePerShare><value>{price}</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
<postTransactionAmounts><sharesOwnedFollowingTransaction><value>{after}</value></sharesOwnedFollowingTransaction></postTransactionAmounts>
<ownershipNature><directOrIndirectOwnership><value>D</value></directOrIndirectOwnership></ownershipNature>
</nonDerivativeTransaction>
<nonDerivativeTransaction><transactionDate><value>2026-10-20</value></transactionDate>
<transactionCoding><transactionCode>S</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>1</value></transactionShares>
<transactionPricePerShare><value>1</value></transactionPricePerShare>
<transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode></transactionAmounts></nonDerivativeTransaction>
</nonDerivativeTable></ownershipDocument></XML></SEC-DOCUMENT>"""

def test_pipeline():
    rows = parse_form4(filing("Jane CEO", "Chief Executive Officer", 40000, 12.5, 140000))
    rows += parse_form4(filing("Bob CFO", "CFO", 20000, 12.4, 60000))
    assert len(rows) == 2 and rows[0]["tier"] == 3          # only the 'P' rows
    c = screener.group(rows)[0]
    assert abs(c["total"] - 748000) < 1 and screener.insider_gate(c)
    m = dict(price=12.9, adv20=9e6, mcap=8e8, ret20=0.02, ret60=-0.05, pct_of_52w_high=0.8)
    assert screener.market_gate(c, m) is None
    sc, why = screener.score(c, m)
    print(sc, why); assert sc >= 60
    assert screener.market_gate(c, dict(m, ret60=0.9))      # run-up rejected

def test_offering_excluded():
    f = filing("Jane CEO", "CEO", 100000, 10.0, 200000)
    f = f.replace("</value></transactionPricePerShare>", '</value><footnoteId id="F1"/></transactionPricePerShare>', 1)
    f = f.replace("</ownershipDocument>", '<footnotes><footnote id="F1">Purchased in the issuer\'s underwritten public offering.</footnote></footnotes></ownershipDocument>')
    assert parse_form4(f) == []

def test_new_features():
    import tempfile, datetime as dt
    from insider_bot import offerings, market, telegram
    from insider_bot.db import DB

    class Fake:
        def submissions(self, cik):
            return {"filings": {"recent": {
                "form": ["424B5", "8-K", "8-K", "4", "FWP"],
                "filingDate": ["2026-10-27", "2026-10-27", "2026-10-20", "2026-10-23", "2026-10-10"],
                "items": ["", "3.02,9.01", "8.01", "", ""]}}}
    assert offerings.find(Fake(), "1", "2026-10-22") == ["424B5 2026-10-27", "8-K 2026-10-27"]
    assert offerings.find(Fake(), "1", "2026-10-28") == []
    class Down:
        def submissions(self, cik): return None
    assert offerings.find(Down(), "1", "2026-10-22") is None

    et = lambda d, h, m: dt.datetime(2026, 10, d, h, m)
    assert market.label_session(et(27, 16, 30), et(27, 21, 0)) == "after-hours"
    assert market.label_session(et(27, 8, 0), et(27, 8, 5)) == "pre-market"
    assert market.label_session(et(27, 15, 55), et(27, 21, 0)) == "close"
    assert market.label_session(et(27, 11, 0), et(27, 11, 5)) == "live"

    db = DB(tempfile.mkdtemp())
    db.add_tracked("ACME", "1", "2026-10-27", 22.83, 72, 1.5e6, False)
    row = db.open_tracked("2026-01-01")[0]
    db.update_tracked(row["id"], ret5=-0.05, offering_notified=1)
    r = db.all_tracked()[0]
    assert r["ret5"] == -0.05 and r["offering_notified"] == 1 and r["ret20"] is None

    rows = parse_form4(filing("Jane CEO", "CEO", 40000, 12.5, 140000))
    c = screener.group(rows)[0]
    m = dict(price=22.83, last_close=27.54, session="after-hours", ext_move=-0.171, adv20=9e6,
             mcap=8e8, ret20=-0.17, ret60=-0.3, pct_of_52w_high=0.5)
    msg = telegram.format_alert(c, m, 70, ["x"], ["after-hours move -17.1%"])
    assert "⚠️" in msg and "after-hours" in msg and "27.54" in msg

if __name__ == "__main__":
    test_new_features(); print("new features ok")
    test_offering_excluded(); test_pipeline(); print("ok")

def test_schedule():
    import datetime as dt
    from zoneinfo import ZoneInfo
    from insider_bot import schedule
    tz = ZoneInfo("America/Chicago")
    assert schedule.is_active(dt.datetime(2026, 10, 27, 5, 0, tzinfo=tz))        # Tue 5:00
    assert not schedule.is_active(dt.datetime(2026, 10, 27, 21, 0, tzinfo=tz))   # Tue 21:00
    assert not schedule.is_active(dt.datetime(2026, 10, 24, 12, 0, tzinfo=tz))   # Sat
    # Fri 22:00 -> Mon 05:00
    assert schedule.seconds_until_active(dt.datetime(2026, 10, 30, 22, 0, tzinfo=tz)) == 55 * 3600
    assert not schedule.is_active(dt.datetime(2026, 11, 26, 12, 0, tzinfo=tz))   # Thanksgiving
    assert schedule.seconds_until_active(dt.datetime(2026, 11, 25, 22, 0, tzinfo=tz)) == 31 * 3600
    assert schedule.seconds_until_active(dt.datetime(2026, 10, 27, 2, 0, tzinfo=tz)) == 3 * 3600

if __name__ == "__main__":
    test_schedule(); print("schedule ok")
