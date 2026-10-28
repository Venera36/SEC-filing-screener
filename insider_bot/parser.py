"""Parse a Form 4 complete-submission text file into open-market purchase rows."""
import re
import xml.etree.ElementTree as ET

XML_RE = re.compile(r"<ownershipDocument>.*?</ownershipDocument>", re.S)
TIER3 = re.compile(r"\b(ceo|cfo|coo|chief\s+executive|chief\s+financial|chief\s+operating)\b", re.I)
TIER2 = re.compile(r"\b(chief|president|chairman|chair|general\s+counsel)\b", re.I)


def _flag(v):
    return (v or "").strip().lower() in ("1", "true")


def _num(node, path):
    t = node.findtext(path)
    try:
        return float(t.strip()) if t and t.strip() else None
    except ValueError:
        return None


def role_tier(title, is_officer, is_director, is_ten):
    """3 = CEO/CFO/COO, 2 = other chief/president/chair, 1 = other officer/director, 0 = 10% holder only."""
    title = title or ""
    if TIER3.search(title):
        return 3
    if TIER2.search(title):
        return 2
    if is_officer or is_director or title:
        return 1
    return 0


def parse_form4(text):
    m = XML_RE.search(text or "")
    if not m:
        return []
    try:
        root = ET.fromstring(m.group(0))
    except ET.ParseError:
        return []

    if _flag(root.findtext("aff10b5One")):   # pre-planned 10b5-1 trades are weaker signals
        return []

    ticker = (root.findtext("issuer/issuerTradingSymbol") or "").strip().upper()
    issuer = (root.findtext("issuer/issuerName") or "").strip()
    cik = (root.findtext("issuer/issuerCik") or "").strip()

    owners = root.findall("reportingOwner")
    if not owners:
        return []
    name = (owners[0].findtext("reportingOwnerId/rptOwnerName") or "Unknown").strip()
    is_off = is_dir = is_ten = False
    title = ""
    for o in owners:
        rel = o.find("reportingOwnerRelationship")
        if rel is None:
            continue
        is_off |= _flag(rel.findtext("isOfficer"))
        is_dir |= _flag(rel.findtext("isDirector"))
        is_ten |= _flag(rel.findtext("isTenPercentOwner"))
        title = title or (rel.findtext("officerTitle") or "").strip()
    tier = role_tier(title, is_off, is_dir, is_ten)
    if not title and is_dir:
        title = "Director"

    rows = []
    for t in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        if (t.findtext("transactionCoding/transactionCode") or "").strip() != "P":
            continue
        if (t.findtext("transactionAmounts/transactionAcquiredDisposedCode/value") or "").strip() != "A":
            continue
        shares = _num(t, "transactionAmounts/transactionShares/value")
        price = _num(t, "transactionAmounts/transactionPricePerShare/value")
        if not shares or not price or price <= 0:
            continue
        rows.append({
            "cik": cik, "ticker": ticker, "issuer": issuer, "owner": name, "title": title, "tier": tier,
            "tx_date": (t.findtext("transactionDate/value") or "")[:10],
            "shares": shares, "price": price, "value": shares * price,
            "owned_after": _num(t, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"),
            "indirect": int((t.findtext("ownershipNature/directOrIndirectOwnership/value") or "D").strip() == "I"),
        })
    return rows
