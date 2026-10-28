"""Market data via yfinance (free, unofficial - swap for Polygon/Tiingo/FMP if you need reliability)."""
import time
import logging

log = logging.getLogger("market")
_cache = {}
TTL = 6 * 3600


def get(ticker):
    t0, val = _cache.get(ticker, (0, None))
    if time.time() - t0 < TTL:
        return val
    val = _fetch(ticker)
    _cache[ticker] = (time.time(), val)
    return val


def _fetch(ticker):
    import yfinance as yf
    sym = ticker.replace(".", "-")
    try:
        tk = yf.Ticker(sym)
        h = tk.history(period="1y", auto_adjust=False)
        if h is None or len(h) < 65:
            return None
        close, vol = h["Close"], h["Volume"]
        price = float(close.iloc[-1])
        mcap = None
        try:
            fi = tk.fast_info
            mcap = float(fi["market_cap"]) if fi["market_cap"] else None
        except Exception:
            pass
        return {
            "price": price,
            "adv20": float((close * vol).tail(20).mean()),
            "mcap": mcap,
            "ret20": price / float(close.iloc[-21]) - 1,
            "ret60": price / float(close.iloc[-61]) - 1,
            "pct_of_52w_high": price / float(close.max()),
        }
    except Exception as e:
        log.warning("market data failed for %s: %s", ticker, e)
        return None
