"""Post-earnings-drift signal: a large EPS beat, a strong positive price reaction on heavy volume, and a price
that has held the gain. Data: Yahoo earnings dates (a scraped table - can break; failures are reported as errors,
never as 'fired')."""
import datetime as dt
import logging
from . import config as C
from .signals import SignalResult

log = logging.getLogger("drift")


def assess(earn_date, surprise, dates, closes, vols, price_now):
    """Pure decision function. dates/closes/vols are parallel lists of daily bars (oldest first).
    surprise is a fraction (0.12 = +12%). Returns (fired, reason)."""
    if surprise is None or surprise < C.DRIFT_MIN_SURPRISE:
        return False, "EPS surprise below minimum"
    pre = max((i for i, d in enumerate(dates) if d < earn_date), default=None)
    if pre is None or pre + 1 >= len(dates) or pre < 5:
        return False, "not enough price history around the report"
    post = min(pre + 2, len(dates) - 1)          # day after the report date covers before-open and after-close reports
    gap = closes[post] / closes[pre] - 1
    if gap < C.DRIFT_MIN_GAP:
        return False, f"price reaction {gap:+.1%} below minimum"
    base_vols = vols[max(0, pre - 20):pre]
    avg = sum(base_vols) / len(base_vols) if base_vols else 0
    vr = max(vols[pre + 1:post + 1]) / avg if avg else 0
    if vr < C.DRIFT_MIN_VOL_RATIO:
        return False, f"reaction volume only {vr:.1f}x average"
    if price_now < closes[post] * (1 - C.DRIFT_HOLD_TOL):
        return False, "price has given back the post-earnings gain"
    return True, f"EPS beat {surprise:+.0%}, price {gap:+.1%} on {vr:.1f}x volume, gain held"


def _latest_report(rows, today):
    """rows: [(date, surprise_fraction_or_None)] -> most recent already-reported row within the window."""
    best = None
    for d, s in rows:
        if s is None or d > today or (today - d).days > C.DRIFT_WINDOW_DAYS:
            continue
        if best is None or d > best[0]:
            best = (d, s)
    return best


def check(edgar, c, m, today):
    import yfinance as yf
    import pandas as pd
    tk = yf.Ticker(c["ticker"].replace(".", "-"))
    ed = tk.get_earnings_dates(limit=8)
    if ed is None or ed.empty or "Surprise(%)" not in ed.columns:
        return SignalResult("earnings_drift", False, error="earnings dates unavailable")
    rows = []
    for ts, r in ed.iterrows():
        s = r.get("Surprise(%)")
        rows.append((ts.date(), None if pd.isna(s) else float(s) / 100.0))      # Yahoo reports percent
    best = _latest_report(rows, today)
    if not best:
        return SignalResult("earnings_drift", False, 0, "no reported earnings in window")
    h = tk.history(period="6mo", auto_adjust=False).dropna(subset=["Close", "Volume"])
    if len(h) < 30:
        return SignalResult("earnings_drift", False, error="price history unavailable")
    dates = [i.date() for i in h.index]
    ok, why = assess(best[0], best[1], dates, [float(x) for x in h["Close"]], [float(x) for x in h["Volume"]], m["price"])
    return SignalResult("earnings_drift", ok, C.DRIFT_POINTS if ok else 0, why)
