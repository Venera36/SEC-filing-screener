"""Minimal, polite SEC EDGAR client (<10 req/s, descriptive User-Agent)."""
import re
import time
import logging
import datetime as dt
import xml.etree.ElementTree as ET
import requests

log = logging.getLogger("edgar")
ATOM = "{http://www.w3.org/2005/Atom}"
IDX_RE = re.compile(r"^4\s{2,}.+?\s{2,}\d+\s+\d{8}\s+(edgar/data/\S+\.txt)\s*$")


class Edgar:
    def __init__(self, user_agent):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"})

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
                log.info("not available (HTTP %s): %s", r.status_code, url)
                return None
            log.warning("HTTP %s for %s", r.status_code, url)
            time.sleep(5 * (attempt + 1))
        return None

    @staticmethod
    def accession(path):
        return path.rsplit("/", 1)[-1].replace(".txt", "")

    def daily_form4(self, day: dt.date):
        """Form 4 filings listed in the EDGAR daily index (published after the day closes)."""
        q = (day.month - 1) // 3 + 1
        url = f"https://www.sec.gov/Archives/edgar/daily-index/{day.year}/QTR{q}/form.{day:%Y%m%d}.idx"
        txt = self._get(url, soft=True)
        out = {}
        for line in (txt or "").splitlines():
            m = IDX_RE.match(line)
            if m:
                out[self.accession(m.group(1))] = m.group(1)
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

    def filing_text(self, path):
        return self._get(f"https://www.sec.gov/Archives/{path}")
