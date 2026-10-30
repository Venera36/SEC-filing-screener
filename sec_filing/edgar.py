"""Minimal, polite SEC EDGAR client (<10 req/s, descriptive User-Agent)."""
import json
import re
import time
import logging
import datetime as dt
import xml.etree.ElementTree as ET
import requests

log = logging.getLogger("edgar")
ATOM = "{http://www.w3.org/2005/Atom}"
IDX_RE = re.compile(r"^4\s{2,}.+?\s{2,}\d+\s+\d{8}\s+(edgar/data/\S+\.txt)\s*$")
IDX_8K_RE = re.compile(r"^8-K\s{2,}(.+?)\s{2,}(\d+)\s+\d{8}\s+(edgar/data/\S+\.txt)\s*$")
ITEM_RE = re.compile(r"Item\s+(\d\.\d{2})")


class Edgar:
    def __init__(self, user_agent):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})
        self._idx_cache = {}
        self._sub_cache = {}

    def _get(self, url, soft=False):
        """soft=True: for files that may simply not exist yet (EDGAR answers 403/404 for those) - no retries."""
        for attempt in range(4):
            time.sleep(0.15)
            try:
                r = self.s.get(url, timeout=30)
            except requests.RequestException as e:
                log.warning("request error %s: %s", url, e)
                time.sleep(2 * (attempt + 1))
                continue
            if r.status_code == 200:
                return r.text
            if r.status_code == 404 or (soft and r.status_code == 403):
                # expected for today's daily index (not published until the day closes) -> quiet
                log.log(logging.DEBUG if soft else logging.INFO, "not available (HTTP %s): %s", r.status_code, url)
                return None
            log.warning("HTTP %s for %s", r.status_code, url)
            wait = 5 * (attempt + 1)
            if r.status_code == 429:                       # honor Retry-After when the SEC sends it
                try:
                    wait = max(wait, min(int(r.headers.get("Retry-After", 0)), 120))
                except ValueError:
                    pass
            time.sleep(wait)
        return None

    @staticmethod
    def accession(path):
        return path.rsplit("/", 1)[-1].replace(".txt", "")

    def _daily_index(self, day: dt.date):
        """The day's form index. Past days are cached for 6h so the Form 4 and 8-K scans share one download;
        today's index doesn't exist until the day closes (EDGAR answers 403), so it is never cached."""
        key = day.isoformat()
        hit = self._idx_cache.get(key)
        if hit and time.time() - hit[0] < 6 * 3600:
            return hit[1]
        q = (day.month - 1) // 3 + 1
        txt = self._get(f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx",
                        soft=True)
        if txt:
            if len(self._idx_cache) > 10:
                self._idx_cache.clear()
            self._idx_cache[key] = (time.time(), txt)
        return txt

    def daily_form4(self, day: dt.date):
        """Form 4 filings listed in the EDGAR daily index (published after the day closes)."""
        out = {}
        for line in (self._daily_index(day) or "").splitlines():
            m = IDX_RE.match(line)
            if m:
                out[self.accession(m.group(1))] = m.group(1)
        return out

    def daily_8k(self, day: dt.date):
        """{accession: {path, cik, company, items(None = unknown)}} for 8-Ks in the daily index."""
        out = {}
        for line in (self._daily_index(day) or "").splitlines():
            m = IDX_8K_RE.match(line)
            if m:
                out[self.accession(m.group(3))] = {"path": m.group(3), "cik": m.group(2),
                                                   "company": m.group(1).strip(), "items": None}
        return out

    def current_feed(self, pages=3):
        """Near-real-time Form 4 filings from EDGAR's 'latest filings' Atom feed."""
        out = {}
        for p in range(pages):
            url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=4&company="
                   f"&dateb=&owner=include&start={p * 100}&count=100&output=atom")
            txt = self._get(url)
            if not txt:
                log.warning("live Form 4 feed unavailable - if this persists check SEC_USER_AGENT (name + real email)")
                break
            try:
                root = ET.fromstring(txt)
            except ET.ParseError:
                break
            for e in root.iter(ATOM + "entry"):
                cat = e.find(ATOM + "category")
                if cat is not None and cat.get("term") != "4":
                    continue
                link = e.find(ATOM + "link")
                m = re.search(r"/data/(\d+)/(\d{10})(\d{2})(\d{6})/", link.get("href", "")) if link is not None else None
                if m:
                    acc = f"{m.group(2)}-{m.group(3)}-{m.group(4)}"
                    out[acc] = f"edgar/data/{m.group(1)}/{acc}.txt"
        return out

    def submissions(self, cik, max_age=0):
        """Recent filing list for an issuer (data.sec.gov). None if unavailable.
        max_age > 0 reuses a copy fetched within that many seconds (8-K scans ask for the same issuers often)."""
        hit = self._sub_cache.get(str(cik))
        if max_age and hit and time.time() - hit[0] < max_age:
            return hit[1]
        txt = self._get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json")
        try:
            data = json.loads(txt) if txt else None
        except ValueError:
            data = None
        if data:
            if len(self._sub_cache) > 2000:
                self._sub_cache.clear()
            self._sub_cache[str(cik)] = (time.time(), data)
        return data

    def current_8k(self, pages=3):
        """Near-real-time 8-Ks from the 'latest filings' feed: {accession: {path, cik, company, items}}.
        items is the list of item numbers shown in the feed summary, or None when the summary has none."""
        out = {}
        for p in range(pages):
            url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company="
                   f"&dateb=&owner=include&start={p * 100}&count=100&output=atom")
            txt = self._get(url)
            if not txt:
                log.warning("live 8-K feed unavailable")
                break
            try:
                root = ET.fromstring(txt)
            except ET.ParseError:
                break
            n = 0
            for e in root.iter(ATOM + "entry"):
                n += 1
                cat = e.find(ATOM + "category")
                if cat is not None and cat.get("term") != "8-K":            # amendments repeat earlier news
                    continue
                link = e.find(ATOM + "link")
                m = re.search(r"/data/(\d+)/(\d{10})(\d{2})(\d{6})/", link.get("href", "")) if link is not None else None
                if not m:
                    continue
                acc = f"{m.group(2)}-{m.group(3)}-{m.group(4)}"
                title = e.findtext(ATOM + "title") or ""
                company = re.sub(r"^\S+\s+-\s+|\s+\(\d+\).*$", "", title).strip()
                items = ITEM_RE.findall(e.findtext(ATOM + "summary") or "")
                out[acc] = {"path": f"edgar/data/{m.group(1)}/{acc}.txt", "cik": m.group(1),
                            "company": company, "items": sorted(set(items)) or None,
                            "accepted": (e.findtext(ATOM + "updated") or "").strip()}
            if n < 100:
                break
        return out

    def company_tickers(self):
        """SEC's official CIK -> ticker list: [{'cik_str', 'ticker', 'title'}, ...] or None."""
        txt = self._get("https://www.sec.gov/files/company_tickers.json")
        try:
            return list(json.loads(txt).values()) if txt else None
        except (ValueError, AttributeError):
            return None

    def filing_index(self, cik, acc):
        """File list of one filing (names + types), or [] if unavailable."""
        txt = self._get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/index.json")
        try:
            return json.loads(txt)["directory"]["item"] if txt else []
        except (ValueError, KeyError, TypeError):
            return []

    def document(self, cik, acc, name):
        return self._get(f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{acc.replace('-', '')}/{name}")

    def filing_text(self, path):
        return self._get(f"https://www.sec.gov/Archives/{path}")
