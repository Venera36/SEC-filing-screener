"""'events' signal for insider alerts: recent positive 8-K / contract / FDA news on the same company.
Negative 8-K news (bankruptcy, non-reliance, FDA setback, CEO/CFO change, dilution) never adds points;
main.py shows it as a warning on the alert instead (see negative_warnings)."""
import datetime as dt
from . import config as C
from .events import TAGS, NEGATIVE
from .signals import SignalResult


def _since(today):
    return (today - dt.timedelta(days=C.EVENTS_LOOKBACK_DAYS)).isoformat()


def check(db, c, today):
    if db is None:
        return SignalResult("events", False, error="no database")
    pts, why = 0.0, []
    for e in db.recent_events(ticker=c["ticker"], cik=c["cik"], since_iso=_since(today)):
        for tag, _ in e["tags"]:
            if tag not in NEGATIVE and TAGS.get(tag, ("", "", 0))[2] > 0:
                pts += TAGS[tag][2]
                why.append(f"{TAGS[tag][0]} 8-K {e['seen_date']}")
    for a in db.recent_awards(c["ticker"], _since(today)):
        label = "FDA approval" if a["source"] == "openfda" else f"${a['amount']/1e6:,.0f}M contract"
        pts += TAGS["fda_approval" if a["source"] == "openfda" else "gov_contract"][2]
        why.append(f"{label} ({a['agency']}) {a['award_date']}")
    if not why:
        return SignalResult("events", False, 0, f"no positive 8-K/contract/FDA news in {C.EVENTS_LOOKBACK_DAYS}d")
    return SignalResult("events", True, min(pts, C.EVENTS_POINTS_CAP), "; ".join(dict.fromkeys(why))[:300])


def negative_warnings(db, c, today):
    out = []
    for e in db.recent_events(ticker=c["ticker"], cik=c["cik"], since_iso=_since(today)):
        for tag, _ in e["tags"]:
            if tag in NEGATIVE:
                out.append(f"8-K {e['seen_date']}: {TAGS[tag][0]}")
    return list(dict.fromkeys(out))
