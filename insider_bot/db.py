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
"""


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
