# SEC Filing

SEC filing screener → Telegram alerts. Four alert types:

| Alert | Source | What it catches |
|---|---|---|
| 🟢 **Insider buying** | SEC Form 4 | Clusters of genuine open-market insider purchases (the original bot) |
| 📄 🧬 🏛 🔴 **8-K events** | SEC 8-K | Material agreements, government contracts, FDA news, acquisitions, CEO/CFO changes, bankruptcy, non-reliance on financials |
| 🏛 **Government contracts** | DoD daily contracts, USAspending | Awards that are large relative to the company's market cap |
| 🧬 **FDA approvals** | openFDA (Drugs@FDA) | Original NDA/BLA approvals (confirmation feed) |

When two signals line up (insiders buying + a contract, FDA news or 8-K in the same stock), each alert says so.
**No trading.** Alerts are screening signals, not financial advice.

## Insider buying: how it works
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
   cp env.example .env && export $(grep -v '^#' .env | xargs)
   python tests/test_smoke.py && python tests/test_upgrade.py && python tests/test_filings.py
   python -m sec_filing.main --once --dry-run --backfill 5   # prints alerts, sends nothing
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
  Run `python -m sec_filing.main --report` (e.g. `railway run ...`) for a summary table with average return and win rate.
  Alerts sent before this feature existed are not tracked.

## Extra signals, confluence and the decision log (v2)
Insider buying stays the anchor. Other signals are modules (`activist.py`, `earnings_drift.py`) that return fired / not fired,
points and a reason; add your own in `signals.py`. **Default is `SIGNALS_MODE=shadow`: they are scored and logged but change nothing.**

| `SIGNALS_MODE` | Behaviour |
|---|---|
| `shadow` (default) | Signals are logged only. Alerts are exactly as before. |
| `boost` | Fired signals add points; insider-only alerts still go out. |
| `require` | Alert only if the insider signal **and** at least `MIN_CONFIRMING_SIGNALS` (1) other signal fired. |

- **Activist 13D:** a new *initial* Schedule 13D on the issuer in the last `ACTIVIST_LOOKBACK_DAYS` (amendments and 13G are ignored).
- **Post-earnings drift:** recent EPS beat >= `DRIFT_MIN_SURPRISE`, price gap >= `DRIFT_MIN_GAP` on heavy volume, gain still held.
  Uses Yahoo's scraped earnings table; if it fails the signal reports an error and does not fire.
- **Daily cap:** `MAX_ALERTS_PER_DAY` (default 3, 0 = unlimited). Best-scored candidates get the slots.
- **Candidate log:** every cluster that passes the insider gate is stored with its status (`alerted`, `below_score`,
  `confluence_missing`, `rejected_market`, `offering_hold`, `ai_veto`, `capped`), signals and AI output. Forward 5/20/60-day
  returns are tracked for all of them **against `BENCHMARK` (SPY)**, so `--report` shows whether alerts beat both the market
  and the candidates you did not alert on. Promote a signal from `shadow` only when this data supports it.

## Optional AI review (`AI_MODE`)
Off by default. For the few candidates about to alert, a model reads recent 8-K excerpts and returns validated JSON
(red flags, veto, confidence, summary). Code supplies every number; the model only reads text. `shadow` = log only;
`veto` = a veto with confidence >= `AI_VETO_MIN_CONF` holds the alert and the summary is shown. Set `LLM_API_KEY`, `LLM_MODEL`
and (for non-OpenAI providers) `LLM_BASE_URL` to any OpenAI-compatible chat-completions endpoint. Filing text is untrusted input;
the model's only power is to hold an alert.

## 8-K screening
Every cycle the bot reads EDGAR's live 8-K feed (plus the daily index after waking), keeps listed companies with a
known ticker, and reads only filings with items worth reading. The filing body and its press-release exhibit (ex-99)
are scanned with keyword rules; each finding becomes a tag:

| Item | Tag(s) | Alerts by default |
|---|---|---|
| 1.01 Material agreement | `merger` (merger / acquisition agreement), `gov_contract` (government customer + award words), `dilution` (item 3.02, underwriting, securities purchase, ATM), `financing` (item 2.03, credit facility, notes), `agreement` (anything else) | merger, gov_contract; agreement only if its value is >= `EVENT_AGREEMENT_MIN_PCT_MCAP` (1%) of market cap |
| 1.03 Bankruptcy | `bankruptcy` | yes (any company size) |
| 2.01 Acquisition / disposal completed | `acquisition` | yes |
| 2.02 Earnings | `earnings` | no: a bare "we reported earnings" 8-K has no beat/miss data. Stored and shown on insider alerts |
| 4.02 Non-reliance on past financials | `non_reliance` | yes (any company size) |
| 5.02 Officer change | `exec_change`, only when the CEO or CFO is the subject ("resigned as CFO", "appointed to serve as CEO", "transition of CFO") | yes |
| 7.01 / 8.01 Press release / other events | `fda_approval`, `fda_milestone` (Breakthrough, Fast Track, priority review, PDUFA, filing accepted), `fda_negative` (Complete Response Letter, clinical hold, refusal to file), `gov_contract` | yes |

Choose which tags alert with `EVENT_ALERT_TAGS`. Gates (lighter than the insider gates, since an 8-K often explains
a move): price >= `EVENT_MIN_PRICE` ($3), ADV20 >= `EVENT_MIN_ADV_USD` ($2M), market cap `EVENT_MIN_MCAP`-`EVENT_MAX_MCAP`
($100M-$20B). Agreements and officer changes are judged from their own Item section only, not the press release.
Every 8-K, contract and FDA alert shows how far the stock has moved since the news (from the last close before it),
flagged "market already reacted" at >= `EVENT_MOVED_WARN` (10%).
Alerts quote the sentence that triggered the tag; **always read the filing**, keyword rules can misfire.

### AI review of 8-Ks (`EVENT_AI_MODE`)
Keyword rules are fast but literal. With `EVENT_AI_MODE=on`, every 8-K the rules flag for an alert is also read by a
model (same `LLM_API_KEY` / `LLM_MODEL` / `LLM_BASE_URL` as the insider AI review; any OpenAI-compatible endpoint).
It returns what the event really is, whether it is material for this company, the likely direction and a 2-sentence
summary. A confident (`EVENT_AI_MIN_CONF`, 0.6) "not material" holds the alert (status `ai_filtered`); a confident
different event type relabels it; the summary replaces the keyword quote on the alert. `shadow` stores the verdict
without changing alerts. If the API fails, the keyword decision stands. `EVENT_AI_MAX_PER_DAY` (150) caps cost.
The model only reads text; it never sees prices and cannot place orders.

## Government contracts and FDA approvals
- **DoD** (`dod`): the Department of Defense posts every award >= $7.5M each business day around 5pm ET.
  Checked hourly (`DOD_POLL_MINUTES`). Multiple-award contracts (one ceiling shared by many companies) are skipped.
- **USAspending** (`usaspending`): civilian agencies (NASA, DHS, HHS/BARDA, DOE, VA...). DoD awards are skipped here
  because DoD reports to USAspending with a 90-day delay. Checked every 6h.
- **openFDA** (`openfda`): original NDA/BLA approvals (generics and supplements skipped). Drugs@FDA lags by days, so
  the 8-K scan usually catches an approval first; an approval already alerted from an 8-K isn't sent twice.

Contract and FDA records name a company, not a ticker. Names are matched to the SEC's official ticker list, with an
alias table for big contractors' subsidiaries (Sikorsky → LMT, Electric Boat → GD...). Approximate matches are labelled
"verify" on the alert; anything uncertain is not alerted. A contract alerts only if it is >= `CONTRACT_MIN_USD` ($10M)
**and** >= `CONTRACT_MIN_PCT_MCAP` (1%) of market cap: a $100M award matters to an $800M company, not to Lockheed.

**First run:** each new feed records what already exists without alerting, so you don't get a burst of old news.
Combined cap for 8-K + contract + FDA alerts: `MAX_EVENT_ALERTS_PER_DAY` (15). Optional separate chat: `EVENTS_CHAT_ID`.
Every 8-K/contract/FDA alert gets the same 5/20/60-trading-day result follow-ups vs SPY, and `--report` groups them by tag.

**Confluence with insider buying:** 8-K/contract/FDA alerts show any insider buying in the same stock over
`WINDOW_DAYS`. Insider alerts show negative 8-K news as warnings, and the `events` signal (in `SIGNALS_ENABLED`) adds
up to `EVENTS_POINTS_CAP` points for positive news, following `SIGNALS_MODE` like the other signals (default `shadow` = logged only).

## Telegram buttons and commands
Alerts carry **Approve / Skip** buttons that record your decision (shown in `--report`). They never place orders.
`/pause` and `/resume` stop and restart new alerts (ingestion and follow-ups continue); `/status` shows state and today's count.
Only `TELEGRAM_CHAT_ID` is obeyed. Disable with `TELEGRAM_CONTROL=false`. Not started with `--once` or `--dry-run`.

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
- Extra-signal and AI results are only as good as their data (Yahoo scrape, EDGAR form labels, the model). Verify them in
  `shadow` mode before letting them affect alerts. Nothing here is a guarantee of profit; compare against SPY in `--report`.
- 8-K tags come from keyword rules, not a reading of the filing. Expect occasional misfires; the alert quotes the
  triggering sentence so you can judge quickly.
- The DoD, USAspending and openFDA parsers depend on those sites' current formats. A source that changes or blocks the
  bot logs a warning and is skipped; the rest keep running. If the DoD source answers 403, set `WEB_USER_AGENT` to a
  normal browser user-agent string.
- No FDA decision (PDUFA) calendar: there is no free official source.
- No order execution of any kind (including paper). Alpaca paper tracking is intentionally not included yet.
