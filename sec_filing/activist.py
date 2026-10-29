"""Activist-stake signal: a new Schedule 13D filed on the issuer.

Only *initial* 13D filings count (the filer states an intent to influence the company). 13G (passive) and
13D/A amendments do not fire. Since Dec-2024 EDGAR labels the form 'SCHEDULE 13D'; older filings are 'SC 13D'.
"""
import datetime as dt
from . import config as C
from .signals import SignalResult

INITIAL_FORMS = ("SC 13D", "SCHEDULE 13D")


def find(edgar, cik, since_iso):
    """Initial 13D filings on/after since_iso. [] = checked, none found; None = could not check."""
    data = edgar.submissions(cik) if cik else None
    if not data:
        return None
    rec = data.get("filings", {}).get("recent", {})
    forms, dates = rec.get("form", []), rec.get("filingDate", [])
    return [f"{f} {dates[i]}" for i, f in enumerate(forms)
            if f in INITIAL_FORMS and i < len(dates) and dates[i] >= since_iso]


def check(edgar, c, m, today):
    since = (today - dt.timedelta(days=C.ACTIVIST_LOOKBACK_DAYS)).isoformat()
    found = find(edgar, c["cik"], since)
    if found is None:
        return SignalResult("activist", False, error="EDGAR submissions unavailable")
    if found:
        return SignalResult("activist", True, C.ACTIVIST_POINTS, "new Schedule 13D: " + ", ".join(found[:3]))
    return SignalResult("activist", False, 0, "no new 13D in last %dd" % C.ACTIVIST_LOOKBACK_DAYS)
