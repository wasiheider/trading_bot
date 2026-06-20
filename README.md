# Trading Bot — Wyckoff Range Strategy (Paper Trading POC)

A TradingView-to-OANDA paper trading bot validating a Wyckoff range strategy before any manual live prop firm trading. The bot cannot execute on prop firms (FTMO, TPT/Apex have no order API) — OANDA demo is the only live execution target.

## Architecture

```
TradingView Pine Script v5 (15M chart)
        │
        │  webhook alert (JSON)
        ▼
Railway Flask Server (server.py)
        │
        ├─► OANDA Demo API  — forex pairs: place/close orders
        ├─► Telegram        — all signals + lifecycle events
        └─► trades.json     — persistent trade log (Railway Volume)
```

**Live deployment:** `tradingbot-production-1e5a.up.railway.app`  
**Branch:** `paper-trading`  
**Dashboard:** `/dashboard`  
**Health:** `/` → `{"status":"ok","strategy":"v5"}`

---

## Strategy — v5 (active)

### Range Logic — 4H Pivot-Anchored
- Range High/Low anchored to confirmed 4H pivot structure (`ta.pivothigh/low`, pivot_len=10)
- Levels lock once both sides are established (`range_locked = true`) and stay fixed
- Range only resets when price closes beyond boundary by 30% of range size (`range_broken`)
- Eliminates range drift that the v4 rolling 20-bar 1H window had during major runs

### Quality Gate — must pass before any entry
All three required:
1. **Boundary touches ≥ 3** (combined H+L, wick-based) — initialised to 1 each side when range first locks
2. **Mid crosses ≥ 3** (settled side determined by close; wicks trigger the count)
3. **30M swing pattern** — pivot_high → pivot_low → pivot_high → pivot_low, each leg ≥ 4 bars (2 hours minimum)

> **Do not reset `mid_crosses` in expansion or range_broken blocks** — causes perpetual 0 on volatile instruments (NQ, XAUUSD). Lesson from v4 Jun 9 2026 debugging.

### Entry Models

| Model | Trigger | Direction |
|-------|---------|-----------|
| **A — BOS + Rejection** | 15M Break of Structure near boundary → retest → rejection candle | Long or Short |
| **B — Spring / Upthrust** | Wick beyond boundary closes back inside range; entry deferred to pullback bar | Long (spring) or Short (upthrust) |
| **C — BOS + RSI Divergence** | Same as A but requires bearish/bullish RSI divergence at retest (RSI 30–70); higher confluence, takes priority over A | Long or Short |
| **D — Mid BOS + Retest** | 15M structural BOS near range mid (±35% of range height from mid) → retest of broken level | Long or Short |

### TP / SL Cascade
- **TP1** @ 1:1 RR → close 50% of position + move SL to breakeven
- **TP2** @ 1:3 RR → close remaining 50%
- **Trailing SL** kicks in after TP1: at 1.5R peak SL moves to 1R, then trails 0.5R behind peak

### Risk
- $250 per trade (0.25% of $100k demo account)
- Daily loss limit: $4,000 (4%)
- Weekly loss limit: $10,000 (10%)
- One open position per instrument at a time

---

## Instruments

| Instrument | OANDA Execution | Telegram / Log |
|-----------|----------------|----------------|
| EURUSD, GBPUSD, USDJPY, EURNZD, NZDUSD | ✅ | ✅ |
| XAUUSD, XAGUSD, US100, US30, US500, USOIL, BTCUSD | ❌ (CFDs not on demo account) | ✅ |

---

## Webhook Payload

TradingView sends JSON to `/webhook/paper`. Required fields:

```json
{
  "token": "<PAPER_WEBHOOK_TOKEN>",
  "symbol": "EURUSD",
  "direction": "LONG",
  "setup": "bos",
  "entry_price": 1.08500,
  "stop_loss": 1.08200,
  "tp1": 1.08800,
  "tp2": 1.09100,
  "rr_to_tp1": 1.5,
  "timeframe": "15",
  "bos_level": 1.08450,
  "range_high": 1.09200,
  "range_low": 1.08100
}
```

**`setup` values:** `bos` (A), `spring` / `upthrust` (B), `bos_div` (C), `mid_bos` (D)

**Lifecycle events** (TP1/TP2/SL hit) send `"event": "tp1_hit"` | `"tp2_hit"` | `"sl_hit"`.

---

## Pine Script Setup

- **Active script:** `pine_script_paper_v5.pine`
- **Chart timeframe:** 15M
- **Alert condition:** "Any alert() function call" — Once Per Bar Close
- **Webhook URL:** `https://tradingbot-production-1e5a.up.railway.app/webhook/paper`
- **Token:** set via `PAPER_WEBHOOK_TOKEN` input in Pine Script
- **Charts active (12):** EURUSD, GBPUSD, USDJPY, EURNZD, NZDUSD, XAUUSD, XAGUSD, US100, US30, US500, USOIL, BTCUSD
- **TradingView runs 24/7** on Vultr VPS — Dallas, 207.148.7.167 ($5/mo)

---

## Data Storage

All persistent data lives on the **Railway Volume** mounted at `/data`:

| File | Contents |
|------|----------|
| `trades.json` | Full trade ledger — every entry, exit, PNL, OANDA trade ID |
| `state.json` | Daily/weekly PNL, win/loss counts, account balance — auto-resets at midnight CT / Monday CT |

`data/trades.db` (SQLite) exists but is not actively used — schema is defined in `logger.py` for future pipeline tracing.

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check — returns strategy version and CT time |
| `/dashboard` | GET | Live trading dashboard (HTML) |
| `/state` | GET | Full bot state — balance, PNL, open trades, per-model stats |
| `/trades` | GET | Raw trade log from `trades.json` |
| `/news` | GET | Yahoo Finance RSS headlines (15-min cache) |
| `/calendar` | GET | ForexFactory economic calendar (1-hour cache) |
| `/webhook/paper` | POST | TradingView signal receiver |
| `/admin/reset` | POST | Reset all paper trading state (requires token) |

---

## Environment Variables (Railway)

| Variable | Description |
|----------|-------------|
| `OANDA_API_TOKEN` | OANDA demo API key |
| `OANDA_ACCOUNT_ID` | `101-001-39435783-001` |
| `OANDA_BASE_URL` | `https://api-fxpractice.oanda.com` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_CHAT_ID` | Telegram chat ID |
| `PAPER_WEBHOOK_TOKEN` | Shared secret between TradingView and bot |
| `PAPER_ACCOUNT_SIZE` | Starting balance (default `100000`) |
| `DATA_DIR` | `/data` — Railway Volume mount path |
| `ANTHROPIC_API_KEY` | Claude API key (reserved for future use) |

---

## Local Development

```bash
git clone https://github.com/wasiheider/trading_bot
cd trading_bot
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
python server.py
```

Railway auto-deploys on push to `paper-trading` branch.

---

## Project Context

This bot is a **paper trading POC** to validate the Wyckoff range strategy before manual live trading on prop firm accounts (FTMO / TPT Apex). Prop firms have no order API, so all automated execution is OANDA demo only. Live prop firm trades are placed manually using the bot's signals as confirmation.
