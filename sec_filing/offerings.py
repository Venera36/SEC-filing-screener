"""Detect equity-offering filings for an issuer via EDGAR's submissions feed."""
import logging

log = logging.getLogger("offerings")
STRONG_FORMS = ("424B", "FWP", "S-1")      # prospectus supplements, free-writing prospectus, S-1 registrations


def find(edgar, cik, since_iso):
    """Offering-related filings on/after since_iso.
    Returns a list (empty = checked, none found) or None if the check could not be done."""
    data = edgar.submissions(cik) if cik else None
    if not data:
        log.info("offering check unavailable for CIK %s", cik)
        return None
    rec = data.get("filings", {}).get("recent", {})
    forms, dates, items = rec.get("form", []), rec.get("filingDate", []), rec.get("items", [])
    out = []
    for i, form in enumerate(forms):
        if i >= len(dates) or dates[i] < since_iso:
            continue
        it = (items[i] if i < len(items) else "") or ""
        # 8-K item 3.02 = unregistered sale of equity (private placements)
        if form.startswith(STRONG_FORMS) or (form.startswith("8-K") and "3.02" in it):
            out.append(f"{form} {dates[i]}")
    return out
