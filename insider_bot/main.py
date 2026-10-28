import argparse
import html
import logging
import sys
import time
import datetime as dt

from . import config as C, market, offerings, schedule, screener, telegram
from .db import DB
from .edgar import Edgar
from .parser import parse_form4

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")


def ingest(db, edgar, index_days):
    found = dict(edgar.current_feed())
    day, checked = schedule.now_local().date(), 0
    while checked <= index_days:                      # daily index covers anything the live feed missed
        if day.weekday() < 5:
            found.update(edgar.daily_form4(day))
            checked += 1
        day -= dt.timedelta(days=1)
    new = {a: p for a, p in found.items() if not db.seen(a)}
    log.info("%d Form 4 filings found (live feed + daily index), %d new", len(found), len(new))
    kept = 0
    for acc, path in new.items():
        text = edgar.filing_text(path)
        if text is None:
            continue                                   # retry next cycle
        rows = parse_form4(text)
        kept += len(rows)
        db.save_filing(acc, rows)
    log.info("stored %d open-market purchase rows", kept)


def _push(msg, dry):
    if dry:
        print("\n" + msg + "\n")
    else:
        telegram.send(msg)


def evaluate(db, edgar, dry):
    today = schedule.now_local().date()
    since = (today - dt.timedelta(days=C.WINDOW_DAYS)).isoformat()
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

        warns = []
        if m["session"] in ("after-hours", "pre-market") and abs(m["ext_move"]) >= C.EXT_MOVE_WARN:
            warns.append(f"{m['session']} move {m['ext_move']:+.1%} vs last close ${m['last_close']:.2f}")
        offering = []
        if C.OFFERING_CHECK:
            start = (dt.date.fromisoformat(c["first_date"]) - dt.timedelta(days=3)).isoformat()
            offering = offerings.find(edgar, c["cik"], start)
            if offering:
                if C.OFFERING_ACTION == "hold":
                    log.info("%s HELD: offering filings %s", c["ticker"], offering); continue
                warns.append("Possible equity offering: " + ", ".join(offering[:4]))

        _push(telegram.format_alert(c, m, sc, why, warns), dry)
        db.record_alert(c["ticker"], c["total"], [p["name"].strip().lower() for p in c["insiders"]])
        if C.TRACK_OUTCOMES or C.OFFERING_CHECK:
            db.add_tracked(c["ticker"], c["cik"], today.isoformat(), m["price"], sc, c["total"], bool(offering))
        log.info("ALERT %s score=%s total=%.0f warns=%s", c["ticker"], sc, c["total"], warns)


def follow_up(db, edgar, dry):
    """After alerts: watch for offerings and record 5/20/60-trading-day outcomes."""
    today = schedule.now_local().date()
    for t in db.open_tracked((today - dt.timedelta(days=100)).isoformat()):
        age = (today - dt.date.fromisoformat(t["alert_date"])).days
        tk = html.escape(t["ticker"])

        if (C.OFFERING_CHECK and not t["offering_notified"] and age <= C.OFFERING_WATCH_DAYS
                and time.time() - t["last_offering_check"] > 1800):
            found = offerings.find(edgar, t["cik"], t["alert_date"])
            db.update_tracked(t["id"], last_offering_check=time.time())
            if found:
                _push(f"⚠️ <b>OFFERING FILED — ${tk}</b>\nAlerted {t['alert_date']} at ${t['alert_price']:.2f}. "
                      f"New filings: {html.escape(', '.join(found[:4]))}\n"
                      "Insider buyers may now be underwater - re-check before acting.", dry)
                db.update_tracked(t["id"], offering_notified=1)

        if C.TRACK_OUTCOMES and t["last_outcome_check"] != today.isoformat():
            for n in (5, 20, 60):
                if t[f"ret{n}"] is None and age >= int(n * 7 / 5):
                    r = market.return_after(t["ticker"], t["alert_date"], t["alert_price"], n)
                    if r is not None:
                        db.update_tracked(t["id"], **{f"ret{n}": r})
                        _push(f"📊 <b>${tk}</b> {n}-trading-day result: <b>{r:+.1%}</b> "
                              f"(alert {t['alert_date']} at ${t['alert_price']:.2f})", dry)
            db.update_tracked(t["id"], last_outcome_check=today.isoformat())


def report(db):
    rows = db.all_tracked()
    f = lambda v: "   -   " if v is None else f"{v:+7.1%}"
    print(f"{'ticker':8}{'alerted':12}{'price':>9}{'score':>6}{'5d':>9}{'20d':>9}{'60d':>9}")
    for r in rows:
        print(f"{r['ticker']:8}{r['alert_date']:12}{r['alert_price']:9.2f}{r['score']:6}  "
              f"{f(r['ret5'])} {f(r['ret20'])} {f(r['ret60'])}")
    for n in (5, 20, 60):
        v = [r[f"ret{n}"] for r in rows if r[f"ret{n}"] is not None]
        if v:
            print(f"{n}d: n={len(v)} avg={sum(v)/len(v):+.1%} win-rate={sum(x > 0 for x in v)/len(v):.0%}")
    if not rows:
        print("No tracked alerts yet.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one cycle and exit")
    ap.add_argument("--dry-run", action="store_true", help="print alerts instead of sending to Telegram")
    ap.add_argument("--backfill", type=int, default=3, help="business days of daily index to scan (default 3)")
    ap.add_argument("--report", action="store_true", help="print alert outcome statistics and exit")
    a = ap.parse_args()

    if a.report:
        return report(DB(C.DATA_DIR))
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
            evaluate(db, edgar, a.dry_run)
            first = False
        except Exception:
            log.exception("cycle failed")
        try:
            follow_up(db, edgar, a.dry_run)
        except Exception:
            log.exception("follow-up failed")
        if a.once:
            break
        time.sleep(C.POLL_MINUTES * 60)


if __name__ == "__main__":
    main()
