import os


def _f(n, d): return float(os.getenv(n, d))
def _i(n, d): return int(os.getenv(n, d))
def _b(n, d): return os.getenv(n, str(d)).strip().lower() in ("1", "true", "yes")


def _mode(n, d, allowed):
    v = os.getenv(n, d).strip().lower()
    return v if v in allowed else d


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

# Offering watch, fresh prices, outcome tracking
OFFERING_CHECK = _b("OFFERING_CHECK", True)
OFFERING_ACTION = os.getenv("OFFERING_ACTION", "flag").strip().lower()   # flag = send with warning, hold = don't send
OFFERING_WATCH_DAYS = _i("OFFERING_WATCH_DAYS", 30)                      # follow-up window after an alert
EXT_MOVE_WARN = _f("EXT_MOVE_WARN", 0.05)                                # warn if pre/after-hours move >= 5%
TRACK_OUTCOMES = _b("TRACK_OUTCOMES", True)

# ---- Extra signals + confluence -------------------------------------------------
# shadow  = extra signals are scored and logged only; alert behaviour is unchanged (default)
# boost   = fired signals add points to the score; insider-only alerts still go out
# require = alert only if the insider signal AND >= MIN_CONFIRMING_SIGNALS other signals fired
SIGNALS_MODE = _mode("SIGNALS_MODE", "shadow", ("shadow", "boost", "require"))
SIGNALS_ENABLED = os.getenv("SIGNALS_ENABLED", "activist,earnings_drift")
MIN_CONFIRMING_SIGNALS = _i("MIN_CONFIRMING_SIGNALS", 1)
NEAR_MISS_MARGIN = _f("NEAR_MISS_MARGIN", 20)      # run signals/log candidates down to MIN_SCORE - margin
MAX_ALERTS_PER_DAY = _i("MAX_ALERTS_PER_DAY", 3)   # 0 = unlimited
BENCHMARK = os.getenv("BENCHMARK", "SPY")

ACTIVIST_LOOKBACK_DAYS = _i("ACTIVIST_LOOKBACK_DAYS", 30)
ACTIVIST_POINTS = _f("ACTIVIST_POINTS", 10)

DRIFT_POINTS = _f("DRIFT_POINTS", 8)
DRIFT_WINDOW_DAYS = _i("DRIFT_WINDOW_DAYS", 45)        # earnings must be this recent
DRIFT_MIN_SURPRISE = _f("DRIFT_MIN_SURPRISE", 0.10)    # EPS beat vs estimate (0.10 = 10%)
DRIFT_MIN_GAP = _f("DRIFT_MIN_GAP", 0.03)              # price reaction around the report
DRIFT_HOLD_TOL = _f("DRIFT_HOLD_TOL", 0.03)            # price must still be within 3% of the post-report close
DRIFT_MIN_VOL_RATIO = _f("DRIFT_MIN_VOL_RATIO", 1.5)   # reaction-day volume vs prior 20d average

# ---- Optional LLM review of finalists ------------------------------------------
# off = disabled (default) | shadow = run and log only | veto = a confident red-flag veto holds the alert
AI_MODE = _mode("AI_MODE", "off", ("off", "shadow", "veto"))
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").strip()   # any OpenAI-compatible endpoint
LLM_MODEL = os.getenv("LLM_MODEL", "").strip()
LLM_TEMPERATURE = os.getenv("LLM_TEMPERATURE", "").strip()      # blank = omit (some models reject it)
AI_VETO_MIN_CONF = _f("AI_VETO_MIN_CONF", 0.6)
AI_LOOKBACK_DAYS = _i("AI_LOOKBACK_DAYS", 30)
AI_MAX_FILINGS = _i("AI_MAX_FILINGS", 2)
AI_MAX_DOC_CHARS = _i("AI_MAX_DOC_CHARS", 4000)

# ---- Telegram buttons and /pause /resume /status ------------------------------
TELEGRAM_CONTROL = _b("TELEGRAM_CONTROL", True)
