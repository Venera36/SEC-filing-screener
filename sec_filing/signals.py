"""Pluggable extra signals + the confluence combiner.

Each signal is a function (edgar, cluster, market, today) -> SignalResult. A signal that cannot get its data
returns fired=False with `error` set; it never raises into the main loop and is never counted as "fired".
"""
import json
import time
import logging
from dataclasses import dataclass, asdict

from . import config as C

log = logging.getLogger("signals")


@dataclass
class SignalResult:
    name: str
    fired: bool
    points: float = 0.0
    reason: str = ""
    error: str = ""


def _registry(db=None):
    from . import activist, earnings_drift, event_signal   # imported lazily so tests/tools can import this cheaply
    return {"activist": activist.check, "earnings_drift": earnings_drift.check,
            "events": lambda edgar, c, m, today: event_signal.check(db, c, today)}


_cache = {}


def run_all(edgar, c, m, today, db=None):
    """Run every enabled signal for a cluster. Results are cached per ticker/day so the 15-minute poll
    loop doesn't re-hit EDGAR and Yahoo for the same candidates."""
    key = (c["ticker"], today.isoformat())
    hit = _cache.get(key)
    if hit and time.time() < hit[0]:
        return hit[1]
    reg = _registry(db)
    out = []
    for name in (n.strip() for n in C.SIGNALS_ENABLED.split(",")):
        fn = reg.get(name)
        if not fn:
            continue
        try:
            res = fn(edgar, c, m, today)
        except Exception as e:                    # a signal must never break the evaluation loop
            log.warning("signal %s failed for %s: %s", name, c["ticker"], e)
            res = SignalResult(name, False, error=str(e)[:120])
        out.append(res)
    ttl = 900 if any(r.error for r in out) else 6 * 3600      # retry sooner after a data error
    _cache[key] = (time.time() + ttl, out)
    return out


def combine(base, results, mode, min_confirming):
    """Return {'score','final','fired','confirmed_ok'}.

    shadow  : decision score = base (behaviour unchanged); 'final' is what boost mode would have used
    boost   : decision score = base + points of fired signals (capped at 100)
    require : like boost, and at least `min_confirming` signals must have fired
    """
    fired = [r for r in results if r.fired]
    final = round(min(100, base + sum(r.points for r in fired)))
    if mode == "shadow":
        return {"score": base, "final": final, "fired": fired, "confirmed_ok": True}
    return {"score": final, "final": final, "fired": fired,
            "confirmed_ok": mode != "require" or len(fired) >= min_confirming}


def to_json(results):
    return json.dumps([asdict(r) for r in results])
