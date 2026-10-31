"""Price providers: Alpaca (real-time last trade, IEX feed on the free plan) and Financial Modeling Prep (daily
history, market cap). Yahoo (yfinance) remains the fallback in market.py, so a missing key, a rejected key or an
outage only downgrades the data source - it never stops alerts.

Every function returns None on any problem and logs at most one warning per source per 30 minutes.
"""
import re
import time
import logging
import datetime as dt
from zoneinfo import ZoneInfo
import requests

from . import config as C

log = logging.getLogger("prices")
_s = requests.Session()
_warned = {}


def _warn(src, msg, *args):
    if time.time() - _warned.get(src, 0) > 1800:
        _warned[src] = time.time()
        log.warning(msg, *args)


def alpaca_enabled():
    return bool(C.ALPACA_API_KEY_ID and C.ALPACA_API_SECRET_KEY)


def fmp_enabled():
    return bool(C.FMP_API_KEY)


def describe():
    cur = f"Alpaca ({C.ALPACA_FEED})" if alpaca_enabled() else "Yahoo"
    hist = "FMP" if fmp_enabled() else "Yahoo"
    return f"current price={cur} · history+market cap={hist} · fallback=Yahoo"


def parse_ts(s):
    """RFC 3339 timestamp (Alpaca sends nanoseconds and 'Z') -> aware datetime, or None."""
    if not s:
        return None
    s = re.sub(r"(\.\d{6})\d+", r"\1", str(s)).replace("Z", "+00:00")
    try:
        t = dt.datetime.fromisoformat(s)
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


# ------------------------------------------------------------------ Alpaca

def alpaca_snapshot(ticker):
    """{'price', 'time', 'day_close', 'day_date'} from Alpaca's snapshot, or None.
    price/time = latest trade (includes extended hours); day_close/day_date = today's regular-session bar."""
    if not alpaca_enabled():
        return None
    sym = ticker.replace("-", ".")
    try:
        r = _s.get(f"{C.ALPACA_DATA_URL}/v2/stocks/{sym}/snapshot", params={"feed": C.ALPACA_FEED}, timeout=15,
                   headers={"APCA-API-KEY-ID": C.ALPACA_API_KEY_ID, "APCA-API-SECRET-KEY": C.ALPACA_API_SECRET_KEY})
        if r.status_code in (401, 403):
            _warn("alpaca", "Alpaca rejected the API key (HTTP %s) - check ALPACA_API_KEY_ID / ALPACA_API_SECRET_KEY; "
                  "using Yahoo for current prices", r.status_code)
            return None
        if r.status_code != 200:
            if r.status_code != 404:                       # 404 = symbol not on Alpaca (common for OTC names)
                _warn("alpaca", "Alpaca HTTP %s for %s - using Yahoo", r.status_code, sym)
            return None
        d = r.json() or {}
    except (requests.RequestException, ValueError) as e:
        _warn("alpaca", "Alpaca request failed (%s) - using Yahoo", e)
        return None
    trade, bar = d.get("latestTrade") or {}, d.get("dailyBar") or {}
    t = parse_ts(trade.get("t"))
    if not trade.get("p") or not t:
        return None
    bt = parse_ts(bar.get("t"))
    return {"price": float(trade["p"]), "time": t,
            "day_close": float(bar["c"]) if bar.get("c") else None,
            "day_date": bt.astimezone(ZoneInfo("America/New_York")).date() if bt else None}


# ------------------------------------------------------------------ FMP

def _fmp(path, **params):
    if not fmp_enabled():
        return None
    try:
        r = _s.get(f"{C.FMP_BASE_URL}/{path}", params=dict(params, apikey=C.FMP_API_KEY), timeout=20)
        if r.status_code in (401, 403):
            _warn("fmp", "FMP rejected the request (HTTP %s) - check FMP_API_KEY and that your plan includes '%s'; "
                  "using Yahoo", r.status_code, path)
            return None
        if r.status_code != 200:
            _warn("fmp", "FMP HTTP %s for %s - using Yahoo", r.status_code, path)
            return None
        data = r.json()
    except (requests.RequestException, ValueError) as e:
        _warn("fmp", "FMP request failed (%s) - using Yahoo", e)
        return None
    if isinstance(data, dict) and ("Error Message" in data or "error" in data):
        _warn("fmp", "FMP error for %s: %s - using Yahoo", path, str(data)[:150])
        return None
    return data


def fmp_quote(ticker):
    """{'price', 'mcap', 'prev_close'} or None."""
    data = _fmp("quote", symbol=ticker.replace(".", "-"))
    row = data[0] if isinstance(data, list) and data else None
    if not isinstance(row, dict):
        return None
    f = lambda k: float(row[k]) if row.get(k) not in (None, "") else None
    return {"price": f("price"), "mcap": f("marketCap"), "prev_close": f("previousClose")}


def fmp_history(ticker, start_iso):
    """Daily bars from start_iso, oldest first, as a DataFrame with Close and Volume (dates as index), or None."""
    import pandas as pd
    data = _fmp("historical-price-eod/full", symbol=ticker.replace(".", "-"), **{"from": start_iso})
    rows = data.get("historical") if isinstance(data, dict) else data          # stable API: list; v3: {historical}
    if not isinstance(rows, list) or not rows:
        return None
    try:
        df = pd.DataFrame([{"date": r["date"], "Close": r.get("close"), "Volume": r.get("volume")} for r in rows])
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()
        df = df[~df.index.duplicated(keep="last")]
        df[["Close", "Volume"]] = df[["Close", "Volume"]].apply(pd.to_numeric, errors="coerce")
        return df.dropna(subset=["Close"])
    except (KeyError, TypeError, ValueError) as e:
        _warn("fmp", "unexpected FMP history format (%s) - using Yahoo", e)
        return None
