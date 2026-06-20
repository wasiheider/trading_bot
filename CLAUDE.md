# CLAUDE.md — Trading Bot Project Brief

This file is loaded automatically by Claude Code. Read it fully before making any changes.

## What This Project Is

A TradingView-to-OANDA **paper trading bot** validating a Wyckoff range strategy. The goal is to build a track record on OANDA demo before manually trading the same signals on prop firm accounts (FTMO / TPT Apex). Prop firms have no order API — automated execution is OANDA demo only. This is a POC, not a production trading system.

---

## Deployment

- **Live bot:** Railway — `tradingbot-production-1e5a.up.railway.app`
- **Branch deployed:** `paper-trading`
- **GitHub:** `github.com/wasiheider/trading_bot`
- **Dashboard:** `/dashboard`
- **TradingView VPS:** Vultr Dallas `207.148.7.167` — runs 24/7 ($5/mo)
- **OANDA demo account:** `101-001-39435783-001` — forex pairs only (no CFDs/metals/indices)

Push to `paper-trading` → Railway auto-deploys.

---

## File Map

| File | Role |
|------|------|
| `server.py` | Flask app — webhook receiver, all endpoints, trade log helpers |
| `config.py` | All env vars and risk constants |
| `risk.py` | Position sizing, daily/weekly PNL limits, `paper_state` in-memory store, `state.json` persistence |
| `oanda.py` | OANDA REST API — place/close orders, get open trades, account summary |
| `notifier.py` | Telegram send helper |
| `logger.py` | SQLite schema (`data/trades.db`) — defined but not actively wired into current flow |
| `main.py` | Entry point (gunicorn runs `server.py` directly via Procfile) |
| `pine_script_paper_v5.pine` | **Active Pine Script** — load this on all 15M TV charts |
| `pine_script_paper_v4.pine` | Retired — kept for reference only |
| `dashboard.html` | Single-file dashboard served at `/dashboard` |
| `trades.json` | Trade ledger (Railway Volume `/data`) |
| `state.json` | Daily/weekly stats + balance (Railway Volume `/data`) |

---

## Strategy — v5 (current)

### Range
- **4H Pivot-Anchored** — `ta.pivothigh/low` with `pivot_len=10` on 4H via `request.security("240", ...)`
- Levels lock once both sides established (`range_locked = true`) and stay fixed
- Range resets only when price closes beyond boundary by 30% of range size (`range_broken`)
- When range first locks: `high_zone_touches` and `low_zone_touches` initialised to 1 each (the locking pivots count as first touches)

### Quality Gate (all 3 required before any entry)
1. **Boundary touches ≥ 3** — combined H+L, wick-based (`high >= range_high - zone_size`)
2. **Mid crosses ≥ 3** — settled side by `close`; wicks trigger the count
3. **30M swing pattern** — pivot_high → pivot_low → pivot_high → pivot_low, each leg ≥ 4 bars (≥ 2 hours)

### Entry Models

| Model | Setup field | Signal |
|-------|------------|--------|
| A — BOS + Rejection | `"bos"` or `""` | 15M BOS near boundary → retest → rejection candle |
| B — Spring / Upthrust | `"spring"` / `"upthrust"` | Wick beyond boundary closes back inside; entry deferred to pullback bar |
| C — BOS + RSI Divergence | `"bos_div"` | Same as A + bearish/bullish RSI divergence at retest (RSI 30–70); takes priority over A |
| D — Mid BOS + Retest | `"mid_bos"` | 15M structural BOS within ±35% of range height from mid → retest entry |

### TP / SL
- **TP1** @ 1:1 RR → close 50% + SL moves to breakeven
- **TP2** @ 1:3 RR → close remaining 50%
- **Trailing SL** after TP1: at 1.5R peak SL → 1R; then trails 0.5R behind peak

### Risk
- `$250` per trade (`RISK_PER_TRADE = 0.0025` in `config.py`)
- Daily loss limit: `$4,000` | Weekly loss limit: `$10,000`
- One open position per instrument at a time (duplicate guard in `server.py`)

---

## Instruments

| Instrument | OANDA Execution | Telegram + Log only |
|-----------|:--------------:|:-------------------:|
| EURUSD, GBPUSD, USDJPY, EURNZD, NZDUSD | ✅ | — |
| XAUUSD, XAGUSD, US100, US30, US500, USOIL, BTCUSD | — | ✅ |

CFDs, metals, and indices are not supported on this OANDA demo account type. Do not add them to `INSTRUMENT_MAP` in `oanda.py`.

---

## Data Storage

### Active (survives Railway restarts)
- `trades.json` — full trade ledger; key: `"paper": [...]`; lives at `DATA_DIR` = `/data` on Railway Volume
- `state.json` — daily/weekly PNL, win/loss counts, balance; auto-resets daily/weekly at CT midnight

### Inactive
- `data/trades.db` — SQLite with schema defined in `logger.py`; path is relative to script dir (NOT on Railway Volume), so it does not survive restarts. Tables exist but `log_signal()` / `log_trade_event()` etc. are not called from `server.py` current flow.

---

## API Endpoints

| Endpoint | Method | Notes |
|----------|--------|-------|
| `/` | GET | Health — returns `{"status":"ok","strategy":"v5"}` |
| `/dashboard` | GET | Serves `dashboard.html` |
| `/state` | GET | Balance, PNL, open trades, per-model stats (A/B/C) |
| `/trades` | GET | Raw `trades.json` content |
| `/news` | GET | Yahoo Finance RSS (15-min cache) |
| `/calendar` | GET | ForexFactory calendar (1-hour cache) |
| `/webhook/paper` | POST | TradingView entry signals + lifecycle events |
| `/admin/reset` | POST | Full state reset — requires `PAPER_WEBHOOK_TOKEN` |

---

## Environment Variables

All stored in Railway. Never commit secrets.

| Variable | Value / Notes |
|----------|--------------|
| `OANDA_API_TOKEN` | OANDA demo API key |
| `OANDA_ACCOUNT_ID` | `101-001-39435783-001` |
| `OANDA_BASE_URL` | `https://api-fxpractice.oanda.com` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |
| `PAPER_WEBHOOK_TOKEN` | Shared secret — TradingView Pine Script token input must match |
| `PAPER_ACCOUNT_SIZE` | `100000` |
| `DATA_DIR` | `/data` — Railway Volume mount point |
| `ANTHROPIC_API_KEY` | Reserved for future Claude integration |

---

## Pine Script Setup (TradingView)

- **Active script:** `pine_script_paper_v5.pine`
- **Chart timeframe:** 15M on all instruments
- **Alert condition:** "Any alert() function call" — Once Per Bar Close
- **Webhook URL:** `https://tradingbot-production-1e5a.up.railway.app/webhook/paper`
- **Token input:** must match `PAPER_WEBHOOK_TOKEN` Railway env var
- **Active charts (12):** EURUSD, GBPUSD, USDJPY, EURNZD, NZDUSD, XAUUSD, XAGUSD, US100, US30, US500, USOIL, BTCUSD

When updating Pine Script: reload script on all 12 charts and recreate alerts.

---

## Critical Constraints — Do Not Violate

1. **Never reset `mid_crosses` in expansion or `range_broken` blocks.** Causes perpetual 0 on volatile instruments (NQ, XAUUSD). Counter only resets on chart load. `mid_approach_from` resets on `range_broken` / expansion — that is correct and separate. (Lesson: v4 Jun 9 2026 — three debugging iterations to fix this.)

2. **Spring/Upthrust zone detection uses `range_high[1]` / `range_low[1]`** (prior bar), not current bar. On live bars, `request.security("60", ta.highest)` incorporates the current open 1H bar's wick immediately — if an upthrust bar creates a new 1H high, `range_high` jumps same bar and `high > range_high` becomes false at bar close. Using `[1]` locks the stable pre-bar level.

3. **OANDA forex pairs only.** `INSTRUMENT_MAP` in `oanda.py` contains only the 5 forex pairs. Non-forex instruments send Telegram + log only — never reach `place_order()`.

4. **`request.get_json(force=True, silent=True)`** in `server.py` webhook handler. TradingView does not always send `Content-Type: application/json`. Using `request.json` causes 415 errors and silent signal loss.

5. **`DATA_DIR` for all file paths** in Railway. Never hardcode relative paths for `trades.json` or `state.json` — they must be on the Railway Volume to survive restarts.

6. **Position sizing uses units (not lots) for forex.** `risk.py` outputs integer units of base currency directly. `_UNITS_PER_LOT` multiplier in `oanda.py` is `1` for all forex pairs — do not change it.

---

## Telegram Mascot

All Telegram messages are prefixed with `🩷👑🤖👑🩷` — this is intentional (daughter's crowned robot design). Do not remove it.

---

## What's Next (known gaps)

- **Cloud DB migration** — move `trades.json` / `state.json` to PostgreSQL (Supabase recommended); wire `logger.py` tables into active flow for full pipeline tracing
- **Entry D in `model_stats`** — `/state` endpoint `_setup_to_model()` does not yet map `"mid_bos"` to model D; falls back to A
- **`config.py` risk values out of sync** — `RISK_PER_TRADE` comment says 0.5% / $500 but value is 0.0025 ($250); update the comment
