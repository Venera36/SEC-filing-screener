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
CREATE TABLE IF NOT EXISTS kv(k TEXT PRIMARY KEY, v TEXT);
-- One row per insider cluster that passed the insider gate: alerted, near-miss or rejected.
-- This is the decision log: it lets you compare what you were alerted on with what you were not.
CREATE TABLE IF NOT EXISTS candidates(
  id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT, cik TEXT, cluster_start TEXT, eval_date TEXT,
  status TEXT, note TEXT DEFAULT '', base_score INTEGER, final_score INTEGER, total REAL, price REAL,
  signals TEXT DEFAULT '[]', ai TEXT DEFAULT '', decision TEXT DEFAULT '',
  last_outcome_check TEXT DEFAULT '',
  ret5 REAL, ret20 REAL, ret60 REAL, spy5 REAL, spy20 REAL, spy60 REAL,
  UNIQUE(ticker, cluster_start));
"""
TRACKED_COLS = {"offering_notified", "last_offering_check", "last_outcome_check",
                "ret5", "ret20", "ret60", "spy5", "spy20", "spy60"}
CAND_COLS = {"last_outcome_check", "ret5", "ret20", "ret60", "spy5", "spy20", "spy60"}


class DB:
    def __init__(self, data_dir):
        os.makedirs(data_dir, exist_ok=True)
        self.c = sqlite3.connect(os.path.join(data_dir, "insider.db"), timeout=30)
        self.c.row_factory = sqlite3.Row
        try:
            self.c.execute("PRAGMA journal_mode=WAL")      # lets the Telegram thread and main loop share the file
        except sqlite3.DatabaseError:
            pass
        self.c.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """Add columns introduced after the first release to databases that already exist."""
        have = {r["name"] for r in self.c.execute("PRAGMA table_info(tracked)")}
        with self.c:
            for col in ("spy5", "spy20", "spy60"):
                if col not in have:
                    self.c.execute(f"ALTER TABLE tracked ADD COLUMN {col} REAL")

    # ---- key/value (pause flag, Telegram update offset) ----
    def get_kv(self, k, default=None):
        r = self.c.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
        return r["v"] if r else default

    def set_kv(self, k, v):
        with self.c:
            self.c.execute("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))

    # ---- filings / purchases ----
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

    # ---- alerts ----
    def last_alert(self, ticker):
        return self.c.execute(
            "SELECT * FROM alerts WHERE ticker=? ORDER BY sent_at DESC LIMIT 1", (ticker,)).fetchone()

    def record_alert(self, ticker, total, insiders):
        with self.c:
            self.c.execute("INSERT INTO alerts VALUES(?,?,?,?)",
                           (ticker, time.time(), total, "|".join(sorted(insiders))))

    def alerts_since(self, ts):
        return self.c.execute("SELECT COUNT(*) FROM alerts WHERE sent_at>=?", (ts,)).fetchone()[0]

    # ---- outcome / offering tracking (alerts only) ----
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

    # ---- candidate / decision log ----
    def log_candidate(self, ticker, cik, cluster_start, eval_date, status, base_score, final_score,
                      total, price, signals_json="[]", ai_json="", note=""):
        """One row per (ticker, cluster). An 'alerted' row is never downgraded. Outcomes are measured from
        eval_date/price, which are the first-seen values, reset to the alert date/price if it is alerted."""
        with self.c:
            row = self.c.execute("SELECT id,status FROM candidates WHERE ticker=? AND cluster_start=?",
                                 (ticker, cluster_start)).fetchone()
            if row is None:
                cur = self.c.execute(
                    "INSERT INTO candidates(ticker,cik,cluster_start,eval_date,status,note,base_score,final_score,"
                    "total,price,signals,ai) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (ticker, cik, cluster_start, eval_date, status, note, base_score, final_score,
                     total, price, signals_json, ai_json))
                return cur.lastrowid
            if row["status"] == "alerted":
                return row["id"]
            self.c.execute(
                "UPDATE candidates SET status=?,note=?,base_score=?,final_score=?,total=?,signals=?,ai=? WHERE id=?",
                (status, note, base_score, final_score, total, signals_json, ai_json, row["id"]))
            if status == "alerted":
                self.c.execute(
                    "UPDATE candidates SET eval_date=?,price=?,last_outcome_check='',ret5=NULL,ret20=NULL,ret60=NULL,"
                    "spy5=NULL,spy20=NULL,spy60=NULL WHERE id=?", (eval_date, price, row["id"]))
            return row["id"]

    def set_decision(self, cand_id, decision):
        with self.c:
            self.c.execute("UPDATE candidates SET decision=? WHERE id=?", (decision, cand_id))

    def open_candidates(self, since_iso):
        return self.c.execute("SELECT * FROM candidates WHERE eval_date>=?", (since_iso,)).fetchall()

    def all_candidates(self):
        return self.c.execute("SELECT * FROM candidates ORDER BY eval_date").fetchall()

    def update_candidate(self, row_id, **fields):
        bad = set(fields) - CAND_COLS
        if bad:
            raise ValueError(f"unknown columns {bad}")
        sets = ",".join(f"{k}=?" for k in fields)
        with self.c:
            self.c.execute(f"UPDATE candidates SET {sets} WHERE id=?", (*fields.values(), row_id))
