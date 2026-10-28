import html
import requests
from . import config as C


def _usd(x):
    return f"${x/1e6:.2f}M" if x >= 1e6 else f"${x/1e3:,.0f}K"


def format_alert(c, m, score, why, warns=()):
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
              "Why: " + html.escape("; ".join(why)),
              f"<a href=\"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={c['cik']}&type=4\">EDGAR filings</a>",
              "<i>Screening signal only, not financial advice.</i>"]
    return "\n".join(lines)


def send(text):
    r = requests.post(f"https://api.telegram.org/bot{C.TELEGRAM_BOT_TOKEN}/sendMessage",
                      json={"chat_id": C.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML",
                            "disable_web_page_preview": True}, timeout=20)
    r.raise_for_status()
