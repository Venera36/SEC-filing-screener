"""CIK -> ticker and company-name -> ticker matching, built on the SEC's official company_tickers.json.

Contract and FDA feeds only give a company *name* ("Lockheed Martin Rotary and Mission Systems",
"VERTEX PHARMS INC"), so names are normalized (suffixes dropped, FDA abbreviations expanded) and matched
conservatively. A wrong match would send a wrong alert, so anything uncertain returns None.
"""
import json
import os
import re
import time
import difflib
import logging

from . import config as C

log = logging.getLogger("names")

SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "llc", "lp", "llp", "ltd", "limited",
            "plc", "sa", "nv", "ag", "se", "the", "holdings", "holding", "group", "intl", "international",
            "us", "usa", "na", "de", "del", "dba", "cl", "class", "a"}
# Drugs@FDA abbreviates sponsor names
EXPAND = {"pharms": "pharmaceuticals", "pharma": "pharmaceuticals", "pharm": "pharmaceuticals",
          "labs": "laboratories", "lab": "laboratories", "theraps": "therapeutics", "thera": "therapeutics",
          "therap": "therapeutics", "biosci": "biosciences", "biopharms": "biopharmaceuticals",
          "biopharma": "biopharmaceuticals", "tech": "technologies", "techs": "technologies", "sys": "systems",
          "mfg": "manufacturing", "svcs": "services", "svc": "services", "assoc": "associates", "natl": "national"}
# Big contractors win most awards through subsidiaries whose names don't match the parent's listing
ALIASES = {
    "sikorsky": "LMT", "lockheed martin": "LMT", "raytheon": "RTX", "pratt whitney": "RTX", "collins aerospace": "RTX",
    "general dynamics": "GD", "electric boat": "GD", "bath iron works": "GD", "gulfstream": "GD",
    "northrop grumman": "NOC", "boeing": "BA", "huntington ingalls": "HII", "ingalls shipbuilding": "HII",
    "newport news shipbuilding": "HII", "l3harris": "LHX", "aerojet rocketdyne": "LHX", "bae systems": None,
    "leidos": "LDOS", "booz allen hamilton": "BAH", "saic": "SAIC", "science applications international": "SAIC",
    "caci": "CACI", "parsons": "PSN", "kbr": "KBR", "jacobs": "J", "amentum": "AMTM", "v2x": "VVX",
    "textron": "TXT", "bell textron": "TXT", "oshkosh defense": "OSK", "am general": None,
    "honeywell": "HON", "ge aerospace": "GE", "general electric": "GE", "bwx": "BWXT", "kratos": "KTOS",
    "aerovironment": "AVAV", "mercury systems": "MRCY", "curtiss wright": "CW", "palantir": "PLTR",
    "anduril": None, "spacex": None, "blue origin": None, "mckesson": "MCK", "cardinal health": "CAH",
    "humana military": "HUM", "health net federal": "CNC", "triwest": None, "fluor": "FLR", "ch2m": "J",
}

_tick = {"loaded": 0.0, "by_cik": {}, "by_name": {}, "names": []}


def norm(name):
    t = re.sub(r"&", " and ", (name or "").lower())
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    words = [EXPAND.get(w, w) for w in t.split()]
    while words and words[-1] in SUFFIXES:
        words.pop()
    words = [w for w in words if w not in ("the", "and")]
    return " ".join(words)


def _cache_path():
    return os.path.join(C.DATA_DIR, "company_tickers.json")


def load(edgar, max_age=86400):
    """Load (and refresh daily) the SEC ticker list. Falls back to the copy on disk if the SEC is unreachable."""
    if _tick["by_cik"] and time.time() - _tick["loaded"] < max_age:
        return True
    rows = edgar.company_tickers() if edgar else None
    path = _cache_path()
    if rows:
        try:
            os.makedirs(C.DATA_DIR, exist_ok=True)
            with open(path, "w") as f:
                json.dump(rows, f)
        except OSError:
            pass
    elif os.path.exists(path):
        with open(path) as f:
            rows = json.load(f)
    if not rows:
        log.warning("SEC ticker list unavailable - name matching disabled this cycle")
        return bool(_tick["by_cik"])
    set_rows(rows)
    return True


def set_rows(rows):
    by_cik, by_name = {}, {}
    for r in rows:                                   # first listing per CIK is the primary share class
        cik, tk = str(int(r["cik_str"])), str(r["ticker"]).upper()
        by_cik.setdefault(cik, {"ticker": tk, "title": r["title"]})
        n = norm(r["title"])
        if n:
            by_name.setdefault(n, (tk, cik))
    _tick.update(loaded=time.time(), by_cik=by_cik, by_name=by_name, names=list(by_name))


def ticker_for_cik(cik):
    r = _tick["by_cik"].get(str(int(cik))) if cik and str(cik).isdigit() else None
    return r["ticker"] if r else None


def cik_for_ticker(ticker):
    for cik, r in _tick["by_cik"].items():
        if r["ticker"] == ticker:
            return cik
    return None


def match(name):
    """(ticker, cik, how) for a company name, or None. Order: alias table, exact normalized name,
    then a close fuzzy match that must share the first word."""
    n = norm(name)
    if not n:
        return None
    for alias, tk in ALIASES.items():
        if n == alias or n.startswith(alias + " "):
            return (tk, cik_for_ticker(tk), "alias") if tk else None     # None = known private company
    if n in _tick["by_name"]:
        tk, cik = _tick["by_name"][n]
        return tk, cik, "exact"
    first = n.split()[0]
    if len(n) < 6:
        return None
    cands = [x for x in _tick["names"] if x.split()[0] == first]
    best = difflib.get_close_matches(n, cands, n=1, cutoff=C.NAME_MATCH_CUTOFF)
    if best:
        tk, cik = _tick["by_name"][best[0]]
        return tk, cik, "fuzzy"
    return None
