import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS filings(accession TEXT PRIMARY KEY, seen_at REAL);
CREATE TABLE IF NOT EXISTS purchases(
  accession TEXT, idx INTEGER, cik TEXT, ticker TEXT, issuer TEXT, owner TEXT, title TEXT,
  tier INTEGER, tx_date TEXT, shares REAL, price REAL, value REAL, owned_after REAL, indirect INTEGER,
  PRIMARY KEY(accession, idx));
CREATE INDEX IF NOT EXISTS ix_p_date ON purchases(tx_date);
CREATE TABLE IF NOT EXISTS alerts(ticker TEXT, sent_at REAL, total REAL, insiders TEXT);
CREATE TABLE IF NOT EXISTS tracked(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, cik TEXT, alert_date TEXT, alert_price REAL,
  score INTEGER, total REAL, offering_notified INTEGER DEFAULT 0, last_offering_check REAL DEFAULT 0,
  last_outcome_check TEXT DEFAULT '', ret5 REAL, ret20 REAL, ret60 REAL);
"""
TRACKED_COLS = {"offering_notified", "last_offering_check", "last_outcome_check", "ret5", "ret20", "ret60"}


class DB:
    def __init__(self, data_dir):
        os.makedirs(data_dir, exist_ok=True)
        self.c = sqlite3.connect(os.path.join(data_dir, "insider.db"))
        self.c.row_factory = sqlite3.Row
        self.c.executescript(SCHEMA)

    def seen(self, acc):
        return self.c.execute("SELECT 1 FROM filings WHERE accession=?", (acc,)).fetchone() is not None

    def save_filing(self, acc, rows):
        with self.c:
            self.c.execute("INSERT OR IGNORE INTO filings VALUES(?,?)", (acc, time.time()))
            for i, r in enumerate(rows):
                self.c.execute(
                    "INSERT OR IGNORE INTO purchases VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (acc, i, r["cik"], r["ticker"], r["issuer"], r["owner"], r["title"], r["tier"],
                     r["tx_date"], r["shares"], r["price"], r["value"], r["owned_after"], r["indirect"]))

    def purchases_since(self, iso_date):
        return [dict(r) for r in self.c.execute(
            "SELECT * FROM purchases WHERE tx_date>=? ORDER BY tx_date", (iso_date,))]

    def last_alert(self, ticker):
        return self.c.execute(
            "SELECT * FROM alerts WHERE ticker=? ORDER BY sent_at DESC LIMIT 1", (ticker,)).fetchone()

    def record_alert(self, ticker, total, insiders):
        with self.c:
            self.c.execute("INSERT INTO alerts VALUES(?,?,?,?)",
                           (ticker, time.time(), total, "|".join(sorted(insiders))))

    # ---- outcome / offering tracking ----
    def add_tracked(self, ticker, cik, alert_date, price, score, total, offering_flagged):
        with self.c:
            self.c.execute(
                "INSERT INTO tracked(ticker,cik,alert_date,alert_price,score,total,offering_notified) VALUES(?,?,?,?,?,?,?)",
                (ticker, cik, alert_date, price, score, total, int(bool(offering_flagged))))

    def open_tracked(self, since_iso):
        return self.c.execute("SELECT * FROM tracked WHERE alert_date>=?", (since_iso,)).fetchall()

    def all_tracked(self):
        return self.c.execute("SELECT * FROM tracked ORDER BY alert_date").fetchall()

    def update_tracked(self, row_id, **fields):
        bad = set(fields) - TRACKED_COLS
        if bad:
            raise ValueError(f"unknown columns {bad}")
        sets = ",".join(f"{k}=?" for k in fields)
        with self.c:
            self.c.execute(f"UPDATE tracked SET {sets} WHERE id=?", (*fields.values(), row_id))
