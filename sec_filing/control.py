"""Telegram control channel: Approve/Skip buttons on alerts and /pause /resume /status commands.
Runs in a background thread with its own DB connection. Only the configured chat is obeyed.
The buttons record your decision in the database - they never place orders."""
import time
import logging
import threading

from . import config as C, schedule, telegram
from .db import DB

log = logging.getLogger("control")


def handle_update(db, upd):
    cb = upd.get("callback_query")
    if cb:
        msg = cb.get("message") or {}
        chat = str((msg.get("chat") or {}).get("id", ""))
        if chat != C.TELEGRAM_CHAT_ID:
            return
        parts = (cb.get("data") or "").split(":")
        if len(parts) == 3 and parts[0] == "d" and parts[1] in ("a", "s") and parts[2].isdigit():
            decision = "approved" if parts[1] == "a" else "skipped"
            db.set_decision(int(parts[2]), decision)
            telegram.answer_callback(cb["id"], f"Logged: {decision}")
            if msg.get("message_id"):
                telegram.clear_buttons(chat, msg["message_id"])
        else:
            telegram.answer_callback(cb["id"], "Unknown action")
        return

    m = upd.get("message") or {}
    if str((m.get("chat") or {}).get("id", "")) != C.TELEGRAM_CHAT_ID:
        return
    words = (m.get("text") or "").strip().lower().split()
    cmd = words[0].split("@")[0] if words else ""
    if cmd == "/pause":
        db.set_kv("paused", "1")
        telegram.send("⏸ New alerts paused. Ingestion and follow-ups continue. Send /resume to re-enable.")
    elif cmd == "/resume":
        db.set_kv("paused", "0")
        telegram.send("▶️ Alerts resumed.")
    elif cmd == "/status":
        start = schedule.now_local().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        telegram.send(f"<b>SEC Filing bot</b>\nAlerts: {'PAUSED' if db.get_kv('paused') == '1' else 'active'}\n"
                      f"Insider alerts today: {db.alerts_since(start)}"
                      f"{f' / cap {C.MAX_ALERTS_PER_DAY}' if C.MAX_ALERTS_PER_DAY else ''}\n"
                      f"\n8-K / contract / FDA alerts today: {db.feed_alerts_since(start)}"
                      f"{f' / cap {C.MAX_EVENT_ALERTS_PER_DAY}' if C.MAX_EVENT_ALERTS_PER_DAY else ''}\n"
                      f"Signals mode: {C.SIGNALS_MODE} · AI mode: {C.AI_MODE}")


def _run():
    db = DB(C.DATA_DIR)
    while True:
        try:
            offset = int(db.get_kv("tg_offset", "0") or 0)
            for upd in telegram.get_updates(offset, 30):
                try:
                    handle_update(db, upd)
                except Exception:
                    log.exception("update handling failed")
                db.set_kv("tg_offset", upd["update_id"] + 1)
        except Exception as e:
            log.warning("Telegram polling error: %s", e)
            time.sleep(15)


def start():
    threading.Thread(target=_run, name="telegram-control", daemon=True).start()
    log.info("Telegram control started (buttons, /pause, /resume, /status)")
