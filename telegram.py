import html
import requests
from . import config as C


def _usd(x):
    return f"${x/1e6:.2f}M" if x >= 1e6 else f"${x/1e3:,.0f}K"


def format_alert(c, m, score, why, warns=(), signals=(), ai=None):
    lines = [f"🟢 <b>INSIDER BUYING — ${html.escape(c['ticker'])}</b> ({html.escape(c['issuer'])})"]
    for w in warns:
        lines.append(f"⚠️ <b>{html.escape(w)}</b>")
    lines += [f"Score <b>{score}/100</b> · Total <b>{_usd(c['total'])}</b> · {len(c['insiders'])} insider(s)",
              f"Window: {c['first_date']} → {c['last_date']}", ""]
    for p in c["insiders"][:6]:
        stake = "new position" if p["stake_pct"] is None else f"+{p['stake_pct']:.0%} stake"
        lines.append(f"• {html.escape(p['name'])} ({html.escape(p['title'] or 'n/a')}): {_usd(p['value'])}, {stake}")
    mcap = f"${m['mcap']/1e9:.2f}B" if m["mcap"] else "n/a"
    sess = m.get("session", "close")
    price_note = f"{sess}; last close ${m['last_close']:.2f}" if sess in ("after-hours", "pre-market") else sess
    lines += ["",
              f"Price ${m['price']:.2f} ({price_note}) · insider avg ${c['avg_price']:.2f}",
              f"20d {m['ret20']:+.0%} · 60d {m['ret60']:+.0%} · ADV20 {_usd(m['adv20'])} · Mkt cap {mcap}",
              "Why: " + html.escape("; ".join(why))]
    if signals:
        lines.append("➕ Also: " + html.escape("; ".join(f"{s.name}: {s.reason}" for s in signals)))
    if ai:
        note = ai["summary"] or "no summary"
        if ai["red_flags"]:
            note += " Flags: " + ", ".join(f["type"] or f["detail"][:40] for f in ai["red_flags"][:3])
        lines.append("🤖 AI note (verify): " + html.escape(note))
    lines += [f"<a href=\"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={c['cik']}&type=4\">EDGAR filings</a>",
              "<i>Screening signal only, not financial advice.</i>"]
    return "\n".join(lines)


def _api(method, **kw):
    r = requests.post(f"https://api.telegram.org/bot{C.TELEGRAM_BOT_TOKEN}/{method}", timeout=kw.pop("timeout", 20), **kw)
    r.raise_for_status()
    return r.json()


def send(text, buttons=None):
    """buttons: [(label, callback_data), ...] shown as one row of inline buttons."""
    payload = {"chat_id": C.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in buttons]]}
    return _api("sendMessage", json=payload)


def answer_callback(callback_id, text):
    _api("answerCallbackQuery", json={"callback_query_id": callback_id, "text": text})


def clear_buttons(chat_id, message_id):
    _api("editMessageReplyMarkup", json={"chat_id": chat_id, "message_id": message_id,
                                         "reply_markup": {"inline_keyboard": []}})


def get_updates(offset, timeout=30):
    r = _api("getUpdates", json={"offset": offset, "timeout": timeout,
                                 "allowed_updates": ["message", "callback_query"]}, timeout=timeout + 10)
    return r.get("result", [])
