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

if __name__ == "__main__":
    test_pipeline(); print("ok")

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
