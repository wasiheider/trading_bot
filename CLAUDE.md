# CLAUDE.md — Trading Bot Project Brief

This file is loaded automatically by Claude Code. Read it fully before making any changes.

---

## What This Project Is

A TradingView-to-OANDA **paper trading bot** validating a Wyckoff range strategy. The goal is to build a track record on OANDA demo before manually trading the same signals on prop firm accounts (FTMO / TPT Apex). Prop firms have no order API — automated execution is OANDA demo only. This is a POC, not a production trading system.

**Zero local machine dependency.** Everything runs in the cloud. Deleting the local folder has no effect on the live system.

---

## Full System Architecture

### Where Everything Lives

| Component | Platform | Always On? | Local Required? |
|-----------|----------|-----------|----------------|
| Bot runtime (Flask) | Railway | ✅ 24/7 | ❌ No |
| Source code | GitHub (`wasiheider/trading_bot`) | ✅ | ❌ No |
| Database | Railway PostgreSQL | ✅ 24/7 | ❌ No |
| Env vars / secrets | Railway Variables | ✅ | ❌ No |
| TradingView alerts | TradingView cloud | ✅ 24/7 | ❌ No |
| Order execution | OANDA Demo API | ✅ 24/7 | ❌ No |
| Notifications | Telegram | ✅ | ❌ No |
| Daily monitor agent | GitHub Actions cron (`.github/workflows/daily-report.yml`) | ✅ 7am CT | ❌ No |
| CLAUDE.md / README | GitHub (repo root) | ✅ | ❌ No |

### Signal Flow

```
TradingView (cloud, 15M bar close)
        │  HTTPS POST /webhook/paper
        ▼
Railway Flask Bot  ──────────────────────► Railway PostgreSQL
(tradingbot-production-1e5a.up.railway.app)   (trades / bot_state / signals)
        │
        ├──► OANDA Demo API  →  order placed / closed (forex only)
        └──► Telegram        →  every trade event
```

### Daily Monitoring Flow

```
GitHub Actions cron (7am CT daily — .github/workflows/daily-report.yml)
        │  GET /report
        ▼
Railway Flask Bot  →  Telegram (balance, PNL, W/L, risk flags)
```

### Deploy Flow

```
Local edit  →  git push  →  GitHub (paper-trading branch)  →  Railway auto-deploy (~2 min)
```

### Railway Project (`heartfelt-serenity / production`)

```
┌──────────────────────┐     ┌──────────────────────┐
│  trading_bot         │◄───►│  Postgres            │
│  Flask app · Online  │     │  PostgreSQL · Online  │
└──────────────────────┘     └──────────────────────┘
DATABASE_URL auto-injected from Postgres into trading_bot
```

---

## Deployment

- **Live bot:** `tradingbot-production-1e5a.up.railway.app`
- **Branch deployed:** `paper-trading`
- **GitHub:** `github.com/wasiheider/trading_bot`
- **Dashboard:** `/dashboard`
- **OANDA demo account:** `101-001-39435783-001` — forex pairs only

Push to `paper-trading` → Railway auto-deploys.

---

## File Map

| File | Role |
|------|------|
| `main.py` | Entry point — scheduler (midnight reset, weekly summary, heartbeat) + Flask |
| `server.py` | Flask app — all endpoints, webhook handler, `/report` monitor |
| `db.py` | PostgreSQL layer — all CRUD for trades, bot_state, signals tables |
| `risk.py` | Position sizing, PNL limit checks, in-memory `paper_state` backed by PostgreSQL |
| `oanda.py` | OANDA REST API — place/close orders, open trades, account summary |
| `config.py` | All environment variables and risk constants |
| `notifier.py` | Telegram send helper — `parse_mode: HTML` |
| `logger.py` | Legacy SQLite schema — not actively wired into current flow |
| `pine_script_paper_v5.pine` | **Active Pine Script** — load on all 12 × 15M TV charts |
| `pine_script_paper_v4.pine` | Retired — kept for reference only |
| `dashboard.html` | Single-file live dashboard served at `/dashboard` |
| `generate_architecture_pdf.py` | Generates `Trading_Bot_Architecture.pdf` — run locally |
| `CLAUDE.md` | This file — auto-loaded by Claude Code |
| `README.md` | Public project documentation |

---

## Strategy — v5 (current)

### Range — 4H Pivot-Anchored
- `ta.pivothigh/low` with `pivot_len=10` on 4H via `request.security("240", ...)`
- Levels lock once both sides established (`range_locked = true`) and stay fixed
- Range resets only when price closes beyond boundary by 30% of range size (`range_broken`)
- When range first locks: `high_zone_touches` and `low_zone_touches` initialised to 1 each

### Quality Gate (all 3 required)
1. **Boundary touches ≥ 3** — combined H+L, wick-based
2. **Mid crosses ≥ 3** — settled side by `close`; wicks trigger the count
3. **30M swing pattern** — pivot_high → pivot_low → pivot_high → pivot_low, each leg ≥ 4 bars

### Entry Models

| Model | Setup field | Signal |
|-------|------------|--------|
| A — BOS + Rejection | `"bos"` or `""` | 15M BOS near boundary → retest → rejection candle |
| B — Spring / Upthrust | `"spring"` / `"upthrust"` | Wick beyond boundary closes back inside; deferred entry |
| C — BOS + RSI Divergence | `"bos_div"` | Same as A + RSI divergence at retest (30–70); priority over A |
| D — Mid BOS + Retest | `"mid_bos"` | 15M structural BOS within ±35% of range mid → retest |

### TP / SL
- **TP1** @ 1:1 RR → close 50% + SL moves to breakeven
- **TP2** @ 1:3 RR → close remaining 50%
- **Trailing SL** after TP1: at 1.5R → move to 1R, then trail 0.5R behind peak

### Risk
- `$500` per trade (`RISK_PER_TRADE = 0.005`)
- Daily loss limit: `$4,000` | Weekly loss limit: `$10,000`
- One open position per instrument at a time

---

## Instruments

| Instrument | OANDA Execution | Telegram + Log only |
|-----------|:--------------:|:-------------------:|
| EURUSD, GBPUSD, USDJPY, EURNZD, NZDUSD | ✅ | — |
| XAUUSD, XAGUSD, US100, US30, US500, USOIL, BTCUSD | — | ✅ |

CFDs, metals, and indices are not on this OANDA demo account type. Do not add to `INSTRUMENT_MAP`.

---

## Data Storage — Railway PostgreSQL

All persistent data lives in Railway PostgreSQL. `DATABASE_URL` is auto-injected.

| Table | Contents |
|-------|----------|
| `trades` | Full trade ledger — every entry, exit, PNL, OANDA trade ID |
| `bot_state` | Daily/weekly PNL, balance, win/loss counters (single row, id=1) |
| `signals` | Every incoming TradingView webhook — full pipeline trace |

**All DB operations go through `db.py`.** Never write directly to tables from other modules.

**Startup sequence (critical):** `db.init_db()` is called at the top of both `main.py` and `server.py` before `risk` is imported. `risk.py` loads state from DB at module level — tables must exist first.

**Stale trade cleanup:** On startup, `db.close_stale_non_forex_opens()` marks any OPEN record with no `oanda_trade_id` for a non-forex instrument as `UNKNOWN`. These are log-only trades whose lifecycle webhooks were never received.

`data/trades.db` (SQLite), `trades.json`, and `state.json` are legacy — no longer used.

---

## API Endpoints

| Endpoint | Method | Notes |
|----------|--------|-------|
| `/` | GET | Health — `{"status":"ok","strategy":"v5"}` |
| `/dashboard` | GET | Serves `dashboard.html` |
| `/state` | GET | Balance, PNL, open trades, per-model stats (A/B/C/D) |
| `/trades` | GET | Full trade ledger from PostgreSQL |
| `/report` | GET | Daily monitor — sends Telegram summary; called by GitHub Actions cron at 7am CT |
| `/news` | GET | Yahoo Finance RSS (15-min cache) |
| `/calendar` | GET | ForexFactory calendar (1-hour cache) |
| `/webhook/paper` | POST | TradingView signal receiver — entries + lifecycle events |
| `/admin/reset` | POST | Full state reset — requires `PAPER_WEBHOOK_TOKEN` |

---

## Environment Variables (Railway — never commit)

| Variable | Value / Notes |
|----------|--------------|
| `DATABASE_URL` | Auto-injected from Postgres service |
| `OANDA_API_TOKEN` | OANDA demo API key |
| `OANDA_ACCOUNT_ID` | `101-001-39435783-001` |
| `OANDA_BASE_URL` | `https://api-fxpractice.oanda.com` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |
| `PAPER_WEBHOOK_TOKEN` | Shared secret — must match Pine Script token input |
| `PAPER_ACCOUNT_SIZE` | `100000` |

| `ANTHROPIC_API_KEY` | Reserved for future Claude integration |

---

## Automation Summary

| Function | Trigger | Automated? |
|----------|---------|-----------|
| Signal detection | TradingView 15M bar close | ✅ Fully automated |
| Webhook delivery | TradingView alert fires | ✅ Fully automated |
| Risk check + sizing | Every incoming signal | ✅ Fully automated |
| OANDA order placement | Signal approved | ✅ Fully automated |
| TP1/TP2/SL management | TradingView lifecycle alert | ✅ Fully automated |
| Telegram notifications | Every trade event | ✅ Fully automated |
| Daily PNL reset | Midnight CT (scheduler) | ✅ Fully automated |
| Weekly PNL reset | Monday midnight CT | ✅ Fully automated |
| Weekly summary | Friday 3:50pm CT (scheduler) | ✅ Fully automated |
| Daily monitor report | 7am CT (GitHub Actions cron) | ✅ Fully automated |
| Database persistence | Every trade / state change | ✅ Fully automated |
| Code deployment | git push to paper-trading | ✅ Fully automated |
| Prop firm trade entry | FTMO/Apex have no API | ⚠️ Manual — always |
| TradingView alert setup | When Pine Script is updated | ⚠️ Manual — one-time per update |

---

## Pine Script Setup (TradingView)

- **Active script:** `pine_script_paper_v5.pine`
- **Chart timeframe:** 15M on all instruments
- **Alert condition:** "Any alert() function call" — Once Per Bar Close
- **Webhook URL:** `https://tradingbot-production-1e5a.up.railway.app/webhook/paper`
- **Token input:** must match `PAPER_WEBHOOK_TOKEN` Railway env var
- **Active charts (12):** EURUSD, GBPUSD, USDJPY, EURNZD, NZDUSD, XAUUSD, XAGUSD, US100, US30, US500, USOIL, BTCUSD

When updating Pine Script: reload on all 12 charts and recreate alerts.

---

## Deploy Timing Warning

Every push to `paper-trading` triggers a Railway redeploy (~2 min downtime). TradingView does **not** retry missed webhooks. Any TP1/TP2/SL lifecycle webhook for a non-forex trade (US100, US500, US30, USOIL, XAUUSD, XAGUSD, BTCUSD) that fires during the restart window is permanently lost — the trade will eventually be marked UNKNOWN.

**Avoid pushing to `paper-trading` during active trading hours when non-forex positions may be open:**
- US indices (US100, US500, US30): 9:30am–4:00pm CT
- Gold/Silver/Oil: nearly 24/7 — push during low-volatility hours (e.g. 5–6pm CT)
- Bitcoin: 24/7 — push during overnight CT hours if possible

Forex trades are unaffected (OANDA manages their TP/SL independently of webhooks).

---

## Critical Constraints — Do Not Violate

1. **Never reset `mid_crosses` in expansion or `range_broken` blocks.** Causes perpetual 0 on volatile instruments. Counter only resets on chart load. (Lesson: v4 Jun 9 2026 — three debugging iterations.)

2. **Spring/Upthrust zone uses `range_high[1]` / `range_low[1]`** (prior bar). On live bars, `request.security` immediately incorporates the current bar's wick — using current bar causes signals to never fire live.

3. **OANDA forex pairs only.** `INSTRUMENT_MAP` in `oanda.py` contains 5 forex pairs. Non-forex → Telegram + log only, never reaches `place_order()`.

4. **`request.get_json(force=True, silent=True)`** in webhook handler. TradingView does not always send `Content-Type: application/json` — using `request.json` causes silent 415 failures.

5. **`db.init_db()` before `from risk import ...`** in both `main.py` and `server.py`. Risk loads state from DB at module level — order matters.

6. **`db.py` is the only DB interface.** All reads/writes go through it. Never use raw psycopg2 calls in other modules.

7. **Position sizing outputs integer units for forex.** `_UNITS_PER_LOT` in `oanda.py` is `1` for all pairs — do not change it.

8. **Telegram `parse_mode` is HTML.** All `send_telegram()` calls must use HTML formatting (`<b>`, `<code>`) not Markdown (`*`, `` ` ``).

---

## Telegram Mascot

All Telegram messages are prefixed with `🩷👑🤖👑🩷` — intentional (daughter's crowned robot design). Do not remove it.

---

## Known Gaps / What's Next

- **Entry D in `model_stats`** — `/state` maps `"mid_bos"` → model D now, but dashboard HTML analytics `setupToModel` may still need updating
- **`logger.py`** — legacy SQLite file, not wired in; candidate for removal or full PostgreSQL migration
