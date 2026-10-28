import math
from collections import defaultdict
from . import config as C


def group(rows):
    """Cluster purchase rows per issuer and per insider."""
    by = defaultdict(list)
    for r in rows:
        if r["tier"] == 0 and not C.INCLUDE_TEN_PCT_OWNERS:
            continue
        by[r["ticker"] or r["cik"]].append(r)

    out = []
    for tk, rs in by.items():
        ins = {}
        for r in rs:
            d = ins.setdefault(r["owner"].strip().lower(), {
                "name": r["owner"], "title": r["title"], "tier": 0, "value": 0.0, "shares": 0.0,
                "last_date": "", "last_after": None})
            d["value"] += r["value"]
            d["shares"] += r["shares"]
            d["tier"] = max(d["tier"], r["tier"])
            d["title"] = d["title"] or r["title"]
            if r["tx_date"] >= d["last_date"]:
                d["last_date"], d["last_after"] = r["tx_date"], r["owned_after"]
        for d in ins.values():
            prior = (d["last_after"] or 0) - d["shares"]
            d["stake_pct"] = d["shares"] / prior if prior > 0 else None   # None = new position
        people = sorted(ins.values(), key=lambda x: -x["value"])
        total = sum(p["value"] for p in people)
        shares = sum(p["shares"] for p in people)
        out.append({
            "ticker": rs[0]["ticker"], "cik": rs[0]["cik"], "issuer": rs[0]["issuer"],
            "insiders": people, "total": total, "avg_price": total / shares if shares else 0,
            "first_date": min(r["tx_date"] for r in rs), "last_date": max(r["tx_date"] for r in rs),
        })
    return out


def insider_gate(c):
    if c["total"] < C.MIN_TOTAL_USD:
        return False
    if len(c["insiders"]) >= C.MIN_INSIDERS:
        return True
    return c["total"] >= C.EXCEPTIONAL_SINGLE_USD   # exceptional single-insider buy


def market_gate(c, m):
    """Return a rejection reason, or None if the stock passes."""
    if m["price"] < C.MIN_PRICE: return f"price ${m['price']:.2f} < min"
    if m["adv20"] < C.MIN_ADV_USD: return f"ADV20 ${m['adv20']/1e6:.1f}M too low"
    if m["mcap"] is not None and not (C.MIN_MCAP <= m["mcap"] <= C.MAX_MCAP):
        return f"market cap ${m['mcap']/1e6:,.0f}M out of range"
    if m["ret20"] > C.MAX_RUNUP_20D: return f"20d run-up {m['ret20']:+.0%}"
    if m["ret60"] > C.MAX_RUNUP_60D: return f"60d run-up {m['ret60']:+.0%}"
    above = m["price"] / c["avg_price"] - 1 if c["avg_price"] else 0
    if above > C.MAX_ABOVE_INSIDER_PRICE: return f"price {above:+.0%} above insider avg buy"
    return None


def score(c, m):
    """0-100. Hard gates already passed; this ranks the *preferences*."""
    pts, why = 0.0, []
    n = len(c["insiders"])

    v = min(25, 10 + 15 * math.log10(max(c["total"], 1) / C.MIN_TOTAL_USD))
    pts += max(v, 0)

    ins_pts = {1: 5, 2: 15, 3: 20}.get(n, 25)
    pts += ins_pts
    if n >= 2: why.append(f"{n} distinct insiders buying")
    else: why.append("exceptional single-insider purchase")

    t3 = [p for p in c["insiders"] if p["tier"] == 3]
    t2 = [p for p in c["insiders"] if p["tier"] == 2]
    pts += min(20, 10 * len(t3) + 4 * len(t2))
    if t3: why.append("C-suite (CEO/CFO/COO): " + ", ".join(p["title"] or p["name"] for p in t3))

    best = max(((p["stake_pct"] if p["stake_pct"] is not None else 1.0) for p in c["insiders"]), default=0)
    pts += 20 if best >= 0.5 else 15 if best >= 0.2 else 10 if best >= 0.1 else 5 if best >= 0.03 else 0
    if best >= 0.1: why.append(f"meaningful stake increase (up to {'new position' if best >= 1 else f'+{best:.0%}'})")

    pts += 5 if m["adv20"] >= 20e6 else 3
    pts += 5 if m["ret60"] <= 0 else 3 if m["ret60"] <= 0.15 else 0
    if m["ret60"] <= 0: why.append("not extended (60d return <= 0)")
    return round(min(pts, 100)), why


def is_material_update(prev, c):
    import time
    if time.time() - prev["sent_at"] > 30 * 86400:
        return True
    old = set(prev["insiders"].split("|"))
    new = {p["name"].strip().lower() for p in c["insiders"]}
    return bool(new - old) or c["total"] >= 1.5 * prev["total"]
