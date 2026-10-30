import argparse
import html
import json
import logging
import sys
import time
import datetime as dt

from . import (analysis, config as C, control, event_signal, events, feeds, market, offerings, schedule, screener,
               signals, telegram)
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


def _push(msg, dry, buttons=None, chat_id=None):
    if dry:
        print("\n" + msg + ("\n[buttons: " + " | ".join(t for t, _ in buttons) + "]" if buttons else "") + "\n")
    else:
        telegram.send(msg, buttons, chat_id=chat_id) if chat_id else telegram.send(msg, buttons)


def _buttons(cand_id):
    return [("✅ Approve", f"d:a:{cand_id}"), ("⏭ Skip", f"d:s:{cand_id}")]


def _log_cand(db, c, m, status, base, final, results, ai=None, note=""):
    return db.log_candidate(
        c["ticker"], c["cik"], c["first_date"], schedule.now_local().date().isoformat(), status, base, final,
        c["total"], m["price"], signals.to_json(results), json.dumps(ai) if ai else "", note)


def evaluate(db, edgar, dry):
    if db.get_kv("paused") == "1":
        log.info("alerts paused (send /resume in Telegram to re-enable)")
        return
    today = schedule.now_local().date()
    since = (today - dt.timedelta(days=C.WINDOW_DAYS)).isoformat()

    # Pass 1: gates, score, extra signals. Everything that passes the insider gate is written to the candidate log.
    finalists = []
    for c in screener.group(db.purchases_since(since)):
        if not c["ticker"] or c["ticker"] == "NONE" or not screener.insider_gate(c):
            continue
        m = market.get(c["ticker"])
        if not m:
            log.info("%s: no market data", c["ticker"]); continue
        base, why = screener.score(c, m)
        reason = screener.market_gate(c, m)
        if reason:
            log.info("%s rejected: %s", c["ticker"], reason)
            _log_cand(db, c, m, "rejected_market", base, base, [], note=reason); continue

        results = signals.run_all(edgar, c, m, today, db=db) if base >= C.MIN_SCORE - C.NEAR_MISS_MARGIN else []
        comb = signals.combine(base, results, C.SIGNALS_MODE, C.MIN_CONFIRMING_SIGNALS)
        if comb["score"] < C.MIN_SCORE:
            log.info("%s score %s below threshold", c["ticker"], comb["score"])
            _log_cand(db, c, m, "below_score", base, comb["final"], results); continue
        if not comb["confirmed_ok"]:
            log.info("%s has %d confirming signal(s), needs %d", c["ticker"], len(comb["fired"]), C.MIN_CONFIRMING_SIGNALS)
            _log_cand(db, c, m, "confluence_missing", base, comb["final"], results); continue
        prev = db.last_alert(c["ticker"])
        if prev and not screener.is_material_update(prev, c):
            continue
        finalists.append({"c": c, "m": m, "base": base, "why": why, "results": results, "comb": comb})

    # Pass 2: best first, so the daily cap keeps the strongest candidates.
    day_start = schedule.now_local().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    sent_today = db.alerts_since(day_start)
    for f in sorted(finalists, key=lambda x: (-x["comb"]["score"], -x["c"]["total"])):
        c, m, results, comb = f["c"], f["m"], f["results"], f["comb"]
        if C.MAX_ALERTS_PER_DAY and sent_today >= C.MAX_ALERTS_PER_DAY:
            log.info("%s skipped: daily alert cap (%d) reached", c["ticker"], C.MAX_ALERTS_PER_DAY)
            _log_cand(db, c, m, "capped", f["base"], comb["final"], results); continue

        warns = event_signal.negative_warnings(db, c, today)
        if m["session"] in ("after-hours", "pre-market") and abs(m["ext_move"]) >= C.EXT_MOVE_WARN:
            warns.append(f"{m['session']} move {m['ext_move']:+.1%} vs last close ${m['last_close']:.2f}")
        offering = []
        if C.OFFERING_CHECK:
            start = (dt.date.fromisoformat(c["first_date"]) - dt.timedelta(days=3)).isoformat()
            offering = offerings.find(edgar, c["cik"], start)
            if offering:
                if C.OFFERING_ACTION == "hold":
                    log.info("%s HELD: offering filings %s", c["ticker"], offering)
                    _log_cand(db, c, m, "offering_hold", f["base"], comb["final"], results, note=", ".join(offering[:4])); continue
                warns.append("Possible equity offering: " + ", ".join(offering[:4]))

        ai = analysis.analyze(edgar, c, m, results, warns) if analysis.enabled() else None
        if C.AI_MODE == "veto" and analysis.should_veto(ai):
            log.info("%s HELD by AI review: %s", c["ticker"], ai["summary"])
            _log_cand(db, c, m, "ai_veto", f["base"], comb["final"], results, ai, note=ai["summary"]); continue

        cid = _log_cand(db, c, m, "alerted", f["base"], comb["final"], results, ai)
        shown_signals = comb["fired"] if C.SIGNALS_MODE != "shadow" else []     # shadow = logged, not shown
        shown_ai = ai if C.AI_MODE == "veto" else None
        _push(telegram.format_alert(c, m, comb["score"], f["why"], warns, shown_signals, shown_ai),
              dry, _buttons(cid))
        db.record_alert(c["ticker"], c["total"], [p["name"].strip().lower() for p in c["insiders"]])
        if C.TRACK_OUTCOMES or C.OFFERING_CHECK:
            db.add_tracked(c["ticker"], c["cik"], today.isoformat(), m["price"], comb["score"], c["total"], bool(offering))
        sent_today += 1
        log.info("ALERT %s score=%s total=%.0f warns=%s", c["ticker"], comb["score"], c["total"], warns)


# ---------------------------------------------------------------- 8-K, contracts, FDA

def _day_start():
    return schedule.now_local().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _room_for_event_alert(db):
    return not C.MAX_EVENT_ALERTS_PER_DAY or db.feed_alerts_since(_day_start()) < C.MAX_EVENT_ALERTS_PER_DAY


def scan_events(db, edgar, dry, index_days):
    """8-K screening. Runs every cycle; filings already looked at are skipped via the events table."""
    if not C.EVENTS_ENABLED:
        return
    today = schedule.now_local().date()
    found = events.gather(edgar, today, index_days + 1)          # +1: today's index doesn't exist yet
    if db.feed_empty("events"):
        # First run ever: record what already exists without alerting (otherwise days-old 8-Ks would alert)
        for acc, f in found.items():
            db.save_event(acc, f["cik"], None, f["company"], f["items"] or [], [], today.isoformat(), "seeded")
        log.info("8-K: first run, recorded %d existing filings without alerting", len(found))
        return
    want = events.alert_tags()
    for ev in events.process(db, edgar, market.get, found, today):
        if db.get_kv("paused") == "1":
            db.mark_feed("events", ev["id"], "paused"); continue
        if not _room_for_event_alert(db):
            log.info("%s 8-K skipped: daily event alert cap (%d) reached", ev["ticker"], C.MAX_EVENT_ALERTS_PER_DAY)
            db.mark_feed("events", ev["id"], "capped"); continue
        tags = sorted(ev["tags"], key=lambda t: t[0] not in want)      # alert-worthy tags first
        labels = [(t, events.TAGS[t][0], events.TAGS[t][1], snip) for t, snip in tags]
        ev["link"] = events.link(ev["cik"], ev["acc"])
        ev["reaction"] = market.move_since(ev["ticker"], ev["when"], ev["m"]["price"])
        _push(telegram.format_event(ev, labels, events.insider_context(db, ev["ticker"], today)), dry,
              chat_id=C.EVENTS_CHAT_ID or None)
        db.mark_feed("events", ev["id"], "alerted")
        log.info("8-K ALERT %s %s", ev["ticker"], [t for t, _ in ev["tags"]])


def scan_feeds(db, edgar, dry):
    """Government contracts and FDA approvals, each source on its own polling interval."""
    today, now = schedule.now_local().date(), time.time()
    for name in (n.strip() for n in C.FEEDS_ENABLED.split(",")):
        fn = feeds.SOURCES.get(name)
        if not fn or not feeds.due(db, name, now):
            continue
        db.set_kv(f"feed_last_{name}", now)
        try:
            recs = fn(today)
        except Exception:
            log.exception("feed %s failed", name); continue
        if recs and db.feed_empty("awards", name):
            for r in recs:                     # first run for this source: record the backlog without alerting
                db.save_award(name, r["key"], r["date"], today.isoformat(), r["company"], None, None, r["agency"],
                              r["amount"], r["detail"], "seeded")
            log.info("%s: first run, recorded %d existing records without alerting", name, len(recs))
            continue
        for a in feeds.process(db, edgar, market.get, recs, today):
            status = "alerted"
            if db.get_kv("paused") == "1":
                status = "paused"
            elif not _room_for_event_alert(db):
                status = "capped"
                log.info("%s %s skipped: daily event alert cap reached", a["ticker"], name)
            else:
                a["reaction"] = market.move_since(a["ticker"], a["date"], a["m"]["price"])
                _push(telegram.format_award(a, events.insider_context(db, a["ticker"], today)), dry,
                      chat_id=C.EVENTS_CHAT_ID or None)
                log.info("%s ALERT %s $%.0f", name.upper(), a["ticker"], a["total"])
            for i in a["ids"]:
                db.mark_feed("awards", i, status if status != "alerted" or i == a["ids"][0] else "logged")


def follow_up_feeds(db, dry):
    """5/20/60-trading-day results for 8-K, contract and FDA alerts (vs benchmark), sent like insider results."""
    today = schedule.now_local().date()
    for table in ("events", "awards"):
        for r in db.open_feed_alerts(table, (today - dt.timedelta(days=100)).isoformat()):
            if r["last_outcome_check"] == today.isoformat():
                continue
            age = (today - dt.date.fromisoformat(r["seen_date"])).days
            pending = [n for n in (5, 20, 60) if r[f"ret{n}"] is None and age >= int(n * 7 / 5)]
            fields = {"last_outcome_check": today.isoformat()}
            if pending:
                for n, (ret, spy) in market.forward_returns(r["ticker"], r["seen_date"], r["price"], pending,
                                                            C.BENCHMARK).items():
                    fields[f"ret{n}"], fields[f"spy{n}"] = ret, spy
                    kind = "8-K" if table == "events" else {"openfda": "FDA"}.get(r["source"], "contract")
                    _push(f"📊 <b>${html.escape(r['ticker'])}</b> {kind} alert, {n}-trading-day result: "
                          f"<b>{ret:+.1%}</b> vs {html.escape(C.BENCHMARK)} {spy:+.1%} "
                          f"(alert {r['seen_date']} at ${r['price']:.2f})", dry, chat_id=C.EVENTS_CHAT_ID or None)
            db.update_feed(table, r["id"], **fields)


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
            pending = [n for n in (5, 20, 60) if t[f"ret{n}"] is None and age >= int(n * 7 / 5)]
            if pending:
                for n, (r, spy) in market.forward_returns(t["ticker"], t["alert_date"], t["alert_price"],
                                                          pending, C.BENCHMARK).items():
                    db.update_tracked(t["id"], **{f"ret{n}": r, f"spy{n}": spy})
                    _push(f"📊 <b>${tk}</b> {n}-trading-day result: <b>{r:+.1%}</b> vs {html.escape(C.BENCHMARK)} "
                          f"{spy:+.1%} (alert {t['alert_date']} at ${t['alert_price']:.2f})", dry)
            db.update_tracked(t["id"], last_outcome_check=today.isoformat())


def follow_up_candidates(db):
    """Forward returns (vs benchmark) for every logged candidate - alerted or not - so the two can be compared."""
    today = schedule.now_local().date()
    for r in db.open_candidates((today - dt.timedelta(days=100)).isoformat()):
        if not r["price"] or r["last_outcome_check"] == today.isoformat():
            continue
        age = (today - dt.date.fromisoformat(r["eval_date"])).days
        pending = [n for n in (5, 20, 60) if r[f"ret{n}"] is None and age >= int(n * 7 / 5)]
        if not pending:
            continue
        fields = {"last_outcome_check": today.isoformat()}
        for n, (ret, spy) in market.forward_returns(r["ticker"], r["eval_date"], r["price"], pending, C.BENCHMARK).items():
            fields[f"ret{n}"], fields[f"spy{n}"] = ret, spy
        db.update_candidate(r["id"], **fields)
        time.sleep(0.3)


def _stats(rows, n):
    v = [r[f"ret{n}"] for r in rows if r[f"ret{n}"] is not None]
    ex = [r[f"ret{n}"] - r[f"spy{n}"] for r in rows if r[f"ret{n}"] is not None and r[f"spy{n}"] is not None]
    if not v:
        return None
    s = f"{n}d: n={len(v)} avg={sum(v)/len(v):+.1%} win-rate={sum(x > 0 for x in v)/len(v):.0%}"
    return s + (f" avg vs {C.BENCHMARK}={sum(ex)/len(ex):+.1%} (n={len(ex)})" if ex else "")


def report(db):
    rows = db.all_tracked()
    f = lambda v: "   -   " if v is None else f"{v:+7.1%}"
    print("== Sent alerts ==")
    print(f"{'ticker':8}{'alerted':12}{'price':>9}{'score':>6}{'5d':>9}{'20d':>9}{'60d':>9}")
    for r in rows:
        print(f"{r['ticker']:8}{r['alert_date']:12}{r['alert_price']:9.2f}{r['score']:6}  "
              f"{f(r['ret5'])} {f(r['ret20'])} {f(r['ret60'])}")
    for n in (5, 20, 60):
        line = _stats(rows, n)
        if line:
            print(line)
    if not rows:
        print("No tracked alerts yet.")

    cands = db.all_candidates()
    print("\n== Candidate log (what you were NOT alerted on vs what you were) ==")
    for status in sorted({r["status"] for r in cands}):
        grp = [r for r in cands if r["status"] == status]
        print(f"[{status}] {len(grp)} candidate(s)")
        for n in (5, 20, 60):
            line = _stats(grp, n)
            if line:
                print("   " + line)
    dec = {}
    for r in cands:
        if r["decision"]:
            dec[r["decision"]] = dec.get(r["decision"], 0) + 1
    if dec:
        print("Your decisions:", ", ".join(f"{k}={v}" for k, v in sorted(dec.items())))
    if not cands:
        print("No candidates logged yet.")

    for table, title in (("events", "8-K alerts"), ("awards", "Contract / FDA alerts")):
        rows = db.all_feed_alerts(table)
        print(f"\n== {title} ==")
        if not rows:
            print("None yet.")
            continue
        groups = {}
        for r in rows:
            key = (json.loads(r["tags"])[0][0] if r["tags"] != "[]" else "?") if table == "events" else r["source"]
            groups.setdefault(key, []).append(r)
        for key, grp in sorted(groups.items()):
            print(f"[{key}] {len(grp)} alert(s)")
            for n in (5, 20, 60):
                line = _stats(grp, n)
                if line:
                    print("   " + line)


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
    if not a.dry_run and not a.once and C.TELEGRAM_CONTROL:
        control.start()
    log.info("SEC Filing bot · signals mode=%s (%s) · AI mode=%s · insider alert cap/day=%s",
             C.SIGNALS_MODE, C.SIGNALS_ENABLED, C.AI_MODE, C.MAX_ALERTS_PER_DAY or "unlimited")
    log.info("8-K screening=%s (alert tags: %s) · feeds=%s · event alert cap/day=%s", C.EVENTS_ENABLED,
             C.EVENT_ALERT_TAGS, C.FEEDS_ENABLED or "none", C.MAX_EVENT_ALERTS_PER_DAY or "unlimited")
    first = True
    while True:
        if C.ENFORCE_SCHEDULE and not a.once and not schedule.is_active():
            wait = schedule.seconds_until_active()
            log.info("outside active window; sleeping %d min (until %s %s)", wait // 60 + 1, C.ACTIVE_START, C.SCHEDULE_TZ)
            time.sleep(min(wait + 5, 3600))
            first = True          # re-scan the daily index after being idle (covers overnight/weekend filings)
            continue
        backfill = a.backfill if first else 2
        try:
            ingest(db, edgar, backfill)
            evaluate(db, edgar, a.dry_run)
        except Exception:
            log.exception("insider cycle failed")
        try:
            scan_events(db, edgar, a.dry_run, backfill if first else 0)
        except Exception:
            log.exception("8-K cycle failed")
        try:
            scan_feeds(db, edgar, a.dry_run)
        except Exception:
            log.exception("contract/FDA cycle failed")
        first = False
        try:
            follow_up(db, edgar, a.dry_run)
            follow_up_candidates(db)
            follow_up_feeds(db, a.dry_run)
        except Exception:
            log.exception("follow-up failed")
        if a.once:
            break
        time.sleep(C.POLL_MINUTES * 60)


if __name__ == "__main__":
    main()
