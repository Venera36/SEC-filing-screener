import os


def _f(n, d): return float(os.getenv(n, d))
def _i(n, d): return int(os.getenv(n, d))
def _b(n, d): return os.getenv(n, str(d)).strip().lower() in ("1", "true", "yes")


SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DATA_DIR = os.getenv("DATA_DIR", "./data")

POLL_MINUTES = _i("POLL_MINUTES", 15)
WINDOW_DAYS = _i("WINDOW_DAYS", 14)            # cluster window (by transaction date)

# Hard gates
MIN_TOTAL_USD = _f("MIN_TOTAL_USD", 500000)
MIN_INSIDERS = _i("MIN_INSIDERS", 2)
EXCEPTIONAL_SINGLE_USD = _f("EXCEPTIONAL_SINGLE_USD", 1000000)  # lets 1 insider qualify
MIN_PRICE = _f("MIN_PRICE", 5)
MIN_ADV_USD = _f("MIN_ADV_USD", 5000000)
MIN_MCAP = _f("MIN_MCAP", 200000000)
MAX_MCAP = _f("MAX_MCAP", 10000000000)
MAX_RUNUP_20D = _f("MAX_RUNUP_20D", 0.25)
MAX_RUNUP_60D = _f("MAX_RUNUP_60D", 0.40)
MAX_ABOVE_INSIDER_PRICE = _f("MAX_ABOVE_INSIDER_PRICE", 0.15)

MIN_SCORE = _f("MIN_SCORE", 60)
INCLUDE_TEN_PCT_OWNERS = _b("INCLUDE_TEN_PCT_OWNERS", False)

# Run schedule (DST-aware; "CST" users should use America/Chicago)
SCHEDULE_TZ = os.getenv("SCHEDULE_TZ", "America/Chicago")
ACTIVE_START = os.getenv("ACTIVE_START", "05:00")
ACTIVE_END = os.getenv("ACTIVE_END", "21:00")
ACTIVE_DAYS = os.getenv("ACTIVE_DAYS", "0,1,2,3,4")   # 0=Mon ... 6=Sun
ENFORCE_SCHEDULE = _b("ENFORCE_SCHEDULE", True)
HONOR_HOLIDAYS = _b("HONOR_HOLIDAYS", True)   # EDGAR is closed on US federal holidays
