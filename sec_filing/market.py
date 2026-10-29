"""Market data via yfinance (free, unofficial - swap for Polygon/Tiingo/FMP if you need reliability)."""
import time
import logging
import warnings
import datetime as dt

import yfinance  # noqa: F401  - must be imported BEFORE the filter below: on import yfinance adds its own
                 # "show DeprecationWarnings from yfinance" rule, which would override ours (the Pandas4Warning spam)
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"yfinance")
warnings.filterwarnings("ignore", category=FutureWarning, module=r"yfinance")

log = logging.getLogger("market")
_cache = {}
TTL = 600          # 10 minutes: prices must be fresh enough to catch after-hours moves
ET = "America/New_York"


def label_session(bar_et, now_et):
    """Which session the latest price bar belongs to."""
    m = bar_et.hour * 60 + bar_et.minute
    if m >= 16 * 60:
        return "after-hours"
    if m < 9 * 60 + 30:
        return "pre-market"
    n = now_et.hour * 60 + now_et.minute
    open_now = now_et.weekday() < 5 and 9 * 60 + 30 <= n < 16 * 60
    return "live" if open_now else "close"


def get(ticker):
    t0, val = _cache.get(ticker, (0, None))
    if time.time() - t0 < TTL:
        return val
    val = _fetch(ticker)
    _cache[ticker] = (time.time(), val)
    return val


def _fetch(ticker):
    import yfinance as yf
    from zoneinfo import ZoneInfo
    sym = ticker.replace(".", "-")
    try:
        tk = yf.Ticker(sym)
        h = tk.history(period="1y", auto_adjust=False)
        if h is None or len(h) < 65:
            return None
        close, vol = h["Close"], h["Volume"]
        last_close = float(close.iloc[-1])
        price, session = last_close, "close"
        try:   # latest price including pre/after-hours
            x = tk.history(period="2d", interval="5m", prepost=True, auto_adjust=False)["Close"].dropna()
            if len(x):
                bar = x.index[-1].tz_convert(ET)
                session = label_session(bar, dt.datetime.now(ZoneInfo(ET)))
                if session != "close":
                    price = float(x.iloc[-1])
        except Exception as e:
            log.info("extended-hours price unavailable for %s: %s", ticker, e)
        ext = price / last_close - 1 if session in ("after-hours", "pre-market") else 0.0
        mcap = None
        try:
            fi = tk.fast_info
            mcap = float(fi["market_cap"]) if fi["market_cap"] else None
        except Exception:
            pass
        return {
            "price": price, "last_close": last_close, "session": session, "ext_move": ext,
            "adv20": float((close * vol).tail(20).mean()), "mcap": mcap,
            "ret20": price / float(close.iloc[-21]) - 1,
            "ret60": price / float(close.iloc[-61]) - 1,
            "pct_of_52w_high": price / float(close.max()),
        }
    except Exception as e:
        log.warning("market data failed for %s: %s", ticker, e)
        return None


def return_after(ticker, alert_date_iso, alert_price, n):
    """Return from alert price to the close n trading days after the alert date (None if not yet available)."""
    import yfinance as yf
    try:
        h = yf.Ticker(ticker.replace(".", "-")).history(start=alert_date_iso, auto_adjust=False)["Close"].dropna()
        if len(h) <= n or not alert_price:
            return None
        return float(h.iloc[n]) / alert_price - 1
    except Exception as e:
        log.warning("outcome lookup failed for %s: %s", ticker, e)
        return None


_bench_cache = {}


def _closes(sym, start_iso):
    import yfinance as yf
    return yf.Ticker(sym.replace(".", "-")).history(start=start_iso, auto_adjust=False)["Close"].dropna()


def forward_returns(ticker, start_iso, base_price, ns=(5, 20, 60), bench="SPY"):
    """{n: (stock_return, benchmark_return)} for each n trading days after start_iso that is available.
    Stock return is measured from base_price (the price at alert time); the benchmark from its close on start_iso."""
    out = {}
    if not base_price:
        return out
    try:
        h = _closes(ticker, start_iso)
        t0, b = _bench_cache.get((bench, start_iso), (0, None))
        if b is None or time.time() - t0 > 3600:
            b = _closes(bench, start_iso)
            _bench_cache[(bench, start_iso)] = (time.time(), b)
        if len(b) == 0:
            return out
        for n in ns:
            if len(h) > n and len(b) > n:
                out[n] = (float(h.iloc[n]) / base_price - 1, float(b.iloc[n]) / float(b.iloc[0]) - 1)
    except Exception as e:
        log.warning("forward returns failed for %s: %s", ticker, e)
    return out
