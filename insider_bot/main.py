import argparse
import logging
import sys
import time
import datetime as dt

from . import config as C, market, schedule, screener, telegram
from .db import DB
from .edgar import Edgar
from .parser import parse_form4

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")


def ingest(db, edgar, index_days):
    found = dict(edgar.current_feed())
    day, checked = dt.date.today(), 0
    while checked <= index_days:                      # daily index covers anything the live feed missed
        if day.weekday() < 5:
            found.update(edgar.daily_form4(day))
            checked += 1
        day -= dt.timedelta(days=1)
    new = {a: p for a, p in found.items() if not db.seen(a)}
    log.info("%d Form 4 filings found, %d new", len(found), len(new))
    kept = 0
    for acc, path in new.items():
        text = edgar.filing_text(path)
        if text is None:
            continue                                   # retry next cycle
        rows = parse_form4(text)
        kept += len(rows)
        db.save_filing(acc, rows)
    log.info("stored %d open-market purchase rows", kept)


def evaluate(db, dry):
    since = (dt.date.today() - dt.timedelta(days=C.WINDOW_DAYS)).isoformat()
    for c in screener.group(db.purchases_since(since)):
        if not c["ticker"] or c["ticker"] == "NONE" or not screener.insider_gate(c):
            continue
        m = market.get(c["ticker"])
        if not m:
            log.info("%s: no market data", c["ticker"]); continue
        reason = screener.market_gate(c, m)
        if reason:
            log.info("%s rejected: %s", c["ticker"], reason); continue
        sc, why = screener.score(c, m)
        if sc < C.MIN_SCORE:
            log.info("%s score %s below threshold", c["ticker"], sc); continue
        prev = db.last_alert(c["ticker"])
        if prev and not screener.is_material_update(prev, c):
            continue
        msg = telegram.format_alert(c, m, sc, why)
        if dry:
            print("\n" + msg + "\n")
        else:
            telegram.send(msg)
        db.record_alert(c["ticker"], c["total"], [p["name"].strip().lower() for p in c["insiders"]])
        log.info("ALERT %s score=%s total=%.0f", c["ticker"], sc, c["total"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    ap.add_argument("--dry-run", action="store_true", help="print alerts instead of sending to Telegram")
    ap.add_argument("--backfill", type=int, default=3, help="business days of daily index to scan (default 3)")
    a = ap.parse_args()

    if not C.SEC_USER_AGENT:
        sys.exit("Set SEC_USER_AGENT (e.g. 'Your Name you@example.com') - the SEC requires it.")
    if not a.dry_run and not (C.TELEGRAM_BOT_TOKEN and C.TELEGRAM_CHAT_ID):
        sys.exit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (or use --dry-run).")

    db, edgar = DB(C.DATA_DIR), Edgar(C.SEC_USER_AGENT)
    first = True
    while True:
        if C.ENFORCE_SCHEDULE and not a.once and not schedule.is_active():
            wait = schedule.seconds_until_active()
            log.info("outside active window; sleeping %d min (until %s %s)", wait // 60 + 1, C.ACTIVE_START, C.SCHEDULE_TZ)
            time.sleep(min(wait + 5, 3600))
            first = True          # re-scan the daily index after being idle (covers overnight/weekend filings)
            continue
        try:
            ingest(db, edgar, a.backfill if first else 2)
            evaluate(db, a.dry_run)
            first = False
        except Exception:
            log.exception("cycle failed")
        if a.once:
            break
        time.sleep(C.POLL_MINUTES * 60)


if __name__ == "__main__":
    main()
