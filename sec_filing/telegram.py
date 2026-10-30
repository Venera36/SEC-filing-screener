
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


ITEM_NAMES = {"1.01": "Material agreement", "1.03": "Bankruptcy", "2.01": "Acquisition/disposal", "2.02": "Earnings",
              "4.02": "Non-reliance", "5.02": "Officer/director change", "7.01": "Reg FD / press release",
              "8.01": "Other events", "9.01": "Exhibits"}


def _market_line(m):
    mcap = f"${m['mcap']/1e9:.2f}B" if m.get("mcap") else "n/a"
    return (f"Price ${m['price']:.2f} · 20d {m['ret20']:+.0%} · 60d {m['ret60']:+.0%} · "
            f"ADV20 {_usd(m['adv20'])} · Mkt cap {mcap}")


def _insider_line(ins):
    if not ins:
        return []
    return [f"👤 <b>Insiders also bought {_usd(ins['total'])}</b> ({ins['insiders']} insider(s), "
            f"{ins['first']} → {ins['last']})"]


def format_event(ev, labels, insider=None):
    """8-K alert. labels: [(tag, label, sentiment, snippet)] in priority order."""
    tk, m = html.escape(ev["ticker"]), ev["m"]
    top = labels[0][0]
    icon = ("🔴" if labels[0][2] == "-" else "🧬" if top.startswith("fda") else "🏛" if top == "gov_contract"
            else "🤝" if top == "merger" else "👔" if top == "exec_change" else "📄")
    head = " · ".join(dict.fromkeys(lbl for _, lbl, _, _ in labels))
    lines = [f"{icon} <b>8-K — ${tk}</b> ({html.escape(ev['company'])})", f"<b>{html.escape(head)}</b>"]
    if ev.get("amount", 0) >= 1e6:
        pct = f" ({ev['amount'] / m['mcap']:.1%} of mkt cap)" if m.get("mcap") else ""
        lines.append(f"Amount mentioned: <b>{_usd(ev['amount'])}</b>{pct}")
    lines.append("Items: " + ", ".join(f"{i} {ITEM_NAMES.get(i, '')}".strip() for i in ev["items"]))
    for _, lbl, _, snip in labels[:2]:
        if snip:
            lines.append(f"<i>“{html.escape(snip[:280])}”</i>")
    lines += _insider_line(insider)
    lines += ["", _market_line(m),
              f"<a href=\"{html.escape(ev['link'])}\">Read the filing</a>",
              "<i>Screening signal only, not financial advice. Read the filing before acting.</i>"]
    return "\n".join(lines)


def format_award(a, insider=None):
    """Government contract (dod / usaspending) or FDA approval (openfda) alert."""
    tk, m = html.escape(a["ticker"]), a["m"]
    if a["source"] == "openfda":
        lines = [f"🧬 <b>FDA APPROVAL — ${tk}</b> ({html.escape(a['recs'][0]['company'])})"]
        for r in a["recs"][:3]:
            lines.append("• " + html.escape(r["detail"]))
        lines.append(f"Approval date: {a['date']} (Drugs@FDA lags by days - the market may already know)")
    else:
        src = "DoD daily contracts" if a["source"] == "dod" else "USAspending"
        pct = f" = <b>{a['total'] / m['mcap']:.1%} of market cap</b>" if m.get("mcap") else ""
        lines = [f"🏛 <b>GOVERNMENT CONTRACT — ${tk}</b>",
                 f"<b>{_usd(a['total'])}</b>{pct} · {len(a['recs'])} award(s) · {a['date']} · {src}"]
        for r in a["recs"][:3]:
            lines.append(f"• {html.escape(r['agency'])}: {html.escape(r['detail'][:300])}")
        if a["how"] == "fuzzy":
            lines.append(f"⚠️ Name matched approximately: “{html.escape(a['recs'][0]['company'])}” → ${tk}. Verify.")
    lines += _insider_line(insider)
    links = [f"<a href=\"{html.escape(r['link'])}\">source</a>" for r in a["recs"][:1] if r.get("link")]
    lines += ["", _market_line(m)] + links + ["<i>Screening signal only, not financial advice.</i>"]
    return "\n".join(lines)


def _api(method, **kw):
    r = requests.post(f"https://api.telegram.org/bot{C.TELEGRAM_BOT_TOKEN}/{method}", timeout=kw.pop("timeout", 20), **kw)
    r.raise_for_status()
    return r.json()


def send(text, buttons=None, chat_id=None):
    """buttons: [(label, callback_data), ...] shown as one row of inline buttons."""
    payload = {"chat_id": chat_id or C.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
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
