import os
import json
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
-- 8-K filings that were looked at (status: alerted, logged, skipped_items, rejected_market, unlisted, capped)
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, accession TEXT UNIQUE, cik TEXT, ticker TEXT, company TEXT,
  items TEXT, tags TEXT DEFAULT '[]', seen_date TEXT, status TEXT, note TEXT DEFAULT '', amount REAL, price REAL,
  alerted_at REAL, last_outcome_check TEXT DEFAULT '',
  ret5 REAL, ret20 REAL, ret60 REAL, spy5 REAL, spy20 REAL, spy60 REAL);
CREATE INDEX IF NOT EXISTS ix_e_ticker ON events(ticker, seen_date);
-- Government contract awards (DoD, USAspending) and FDA approvals (openFDA)
CREATE TABLE IF NOT EXISTS awards(
  id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, key TEXT UNIQUE, award_date TEXT, seen_date TEXT,
  company TEXT, ticker TEXT, cik TEXT, agency TEXT, amount REAL, detail TEXT, status TEXT, note TEXT DEFAULT '',
  price REAL, alerted_at REAL, last_outcome_check TEXT DEFAULT '',
  ret5 REAL, ret20 REAL, ret60 REAL, spy5 REAL, spy20 REAL, spy60 REAL);
CREATE INDEX IF NOT EXISTS ix_a_ticker ON awards(ticker, seen_date);
"""
FEED_TABLES = ("events", "awards")
TRACKED_COLS = {"offering_notified", "last_offering_check", "last_outcome_check",
                "ret5", "ret20", "ret60", "spy5", "spy20", "spy60"}
CAND_COLS = {"last_outcome_check", "ret5", "ret20", "ret60", "spy5", "spy20", "spy60"}


class DB:
    def __init__(self, data_dir):
        os.makedirs(data_dir, exist_ok=True)
        # file name kept from the insider-only version so the existing Railway volume data carries over
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

    # ---- 8-K events ----
    def event_seen(self, acc):
        return self.c.execute("SELECT 1 FROM events WHERE accession=?", (acc,)).fetchone() is not None

    def save_event(self, acc, cik, ticker, company, items, tags, seen_date, status, note="", amount=None, price=None):
        with self.c:
            cur = self.c.execute(
                "INSERT OR IGNORE INTO events(accession,cik,ticker,company,items,tags,seen_date,status,note,amount,price)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (acc, cik, ticker, company, ",".join(items or []), json.dumps(list(tags or [])), seen_date, status,
                 note, amount, price))
            return cur.lastrowid

    def recent_events(self, ticker=None, cik=None, since_iso=""):
        """Classified 8-Ks (tags non-empty) for one company since a date, newest first."""
        q = "SELECT * FROM events WHERE seen_date>=? AND tags!='[]' AND (ticker=? OR cik=?) ORDER BY seen_date DESC"
        rows = self.c.execute(q, (since_iso, ticker or "", str(int(cik)) if cik else "")).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["tags"] = [tuple(t) for t in json.loads(d["tags"] or "[]")]
            out.append(d)
        return out

    # ---- contract / FDA awards ----
    def award_seen(self, key):
        return self.c.execute("SELECT 1 FROM awards WHERE key=?", (key,)).fetchone() is not None

    def save_award(self, source, key, award_date, seen_date, company, ticker, cik, agency, amount, detail, status,
                   note="", price=None):
        with self.c:
            cur = self.c.execute(
                "INSERT OR IGNORE INTO awards(source,key,award_date,seen_date,company,ticker,cik,agency,amount,detail,"
                "status,note,price) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (source, key, award_date, seen_date, company, ticker, cik, agency, amount, detail, status, note, price))
            return cur.lastrowid

    def recent_awards(self, ticker, since_iso, source=None):
        q = "SELECT * FROM awards WHERE ticker=? AND seen_date>=? AND status IN ('alerted','logged')"
        args = [ticker, since_iso]
        if source:
            q += " AND source=?"
            args.append(source)
        return [dict(r) for r in self.c.execute(q + " ORDER BY seen_date DESC", args)]

    # ---- shared by both feed tables ----
    def mark_feed(self, table, row_id, status, note=None):
        assert table in FEED_TABLES
        with self.c:
            if status == "alerted":
                self.c.execute(f"UPDATE {table} SET status=?, alerted_at=? WHERE id=?", (status, time.time(), row_id))
            else:
                self.c.execute(f"UPDATE {table} SET status=? WHERE id=?", (status, row_id))
            if note is not None:
                self.c.execute(f"UPDATE {table} SET note=? WHERE id=?", (note, row_id))

    def feed_alerts_since(self, ts):
        return sum(self.c.execute(f"SELECT COUNT(*) FROM {t} WHERE alerted_at>=?", (ts,)).fetchone()[0]
                   for t in FEED_TABLES)

    def open_feed_alerts(self, table, since_iso):
        assert table in FEED_TABLES
        return self.c.execute(f"SELECT * FROM {table} WHERE status='alerted' AND seen_date>=? AND price>0",
                              (since_iso,)).fetchall()

    def all_feed_alerts(self, table):
        assert table in FEED_TABLES
        return self.c.execute(f"SELECT * FROM {table} WHERE status='alerted' ORDER BY seen_date").fetchall()

    def update_feed(self, table, row_id, **fields):
        assert table in FEED_TABLES
        bad = set(fields) - CAND_COLS
        if bad:
            raise ValueError(f"unknown columns {bad}")
        sets = ",".join(f"{k}=?" for k in fields)
        with self.c:
            self.c.execute(f"UPDATE {table} SET {sets} WHERE id=?", (*fields.values(), row_id))

    def feed_empty(self, table, source=None):
        assert table in FEED_TABLES
        q, args = f"SELECT 1 FROM {table}", ()
        if source:
            q, args = q + " WHERE source=?", (source,)
        return self.c.execute(q + " LIMIT 1", args).fetchone() is None
