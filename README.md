# claude-insider-screener

SEC Form 4 insider-buying screener → Telegram alerts.

Screens SEC EDGAR Form 4 filings for clusters of genuine open-market insider purchases and sends a Telegram alert.
**No trading.** Alerts are screening signals, not financial advice.

## How it works
1. **Ingest** – polls EDGAR's live Form 4 feed + the daily index; parses each filing, keeping only
   transaction code `P` (open-market purchase, acquired). Excludes 10b5-1 plans, option exercises, grants, gifts.
2. **Cluster** – groups purchases per issuer over `WINDOW_DAYS` (by transaction date); 10%-holder-only buyers are excluded by default.
3. **Hard gates** – total ≥ $500K; ≥ 2 distinct insiders (or one purchase ≥ $1M); price ≥ $5; 20d avg $ volume ≥ $5M;
   market cap $200M–$10B; not run up (20d ≤ +25%, 60d ≤ +40%, price ≤ 15% above insiders' avg buy price).
4. **Score (0–100)** – size, number of insiders, CEO/CFO/COO involvement, stake increase %, liquidity, not-extended price. Alert if ≥ `MIN_SCORE` (60).
5. **De-dup** – re-alerts a ticker only if a new insider joins or the total grows ≥ 50%.

Market cap choice: $200M–$10B. Below that, liquidity/manipulation risk; above it, a $500K buy is noise for the company.

## Setup
1. Telegram: message **@BotFather** → `/newbot` → copy token. Message your bot once, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` to find your `chat.id`.
2. Local test:
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   cp .env.example .env && export $(grep -v '^#' .env | xargs)
   python tests/test_smoke.py
   python -m insider_bot.main --once --dry-run --backfill 5   # prints alerts, sends nothing
   ```
3. Push to GitHub, then on Railway: **New Project → Deploy from GitHub repo**.
4. Add variables: `SEC_USER_AGENT` (must contain your name + email, SEC requirement), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `DATA_DIR=/data`.
5. Add a **Volume** mounted at `/data` (otherwise SQLite is wiped on redeploy and you'll get duplicate alerts).

## Safety checks and tracking
- **Fresh prices:** alerts use the latest price including pre-market/after-hours, labelled in the message, and warn if the
  extended-hours move is >= `EXT_MOVE_WARN` (5%).
- **Offering check:** before alerting, EDGAR is checked for offering-related filings (424B prospectuses, FWP, S-1, 8-K item 3.02)
  since 3 days before the first purchase. `OFFERING_ACTION=flag` (default) sends the alert with a warning; `hold` suppresses it.
- **Offering watch:** for `OFFERING_WATCH_DAYS` (30) after each alert, a follow-up message is sent if an offering is filed.
- **Outcome tracking:** each alert's price is stored; 5/20/60 trading days later the result is sent to Telegram.
  Run `python -m insider_bot.main --report` (e.g. `railway run ...`) for a summary table with average return and win rate.
  Alerts sent before this feature existed are not tracked.

## Schedule
By default the bot only polls **Mon-Fri, 05:00-21:00 America/Chicago, skipping US federal holidays** (EDGAR accepts filings 6am-10pm ET = 5am-9pm CT) (handles CST/CDT automatically) and sleeps otherwise.
On waking it re-scans the SEC daily index, so filings made overnight or over the weekend are still picked up (the SEC
accepts Form 4s until 10pm ET). Change via `SCHEDULE_TZ`, `ACTIVE_START`, `ACTIVE_END`, `ACTIVE_DAYS`, or set `ENFORCE_SCHEDULE=false`.
Note: the Railway service still runs 24/7 (idle while sleeping); the sleep loop uses negligible CPU.

## Known limitations
- Market data uses `yfinance` (free, unofficial, can break/rate-limit). Swap `market.py` for Polygon/Tiingo/FMP for production.
- Form 4s are filed up to 2 business days after the trade; the signal is delayed by design.
- Stake % is approximate (indirect holdings and multi-class shares can skew it).
- Tune thresholds via env vars (see `.env.example`).
