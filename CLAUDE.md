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
| E — Box Break | `"box_break"` | NY session close beyond pre-session box boundary |

### OANDA Order Types (forex entries only)

All entry prices are `close` of the 15M signal bar. By the time the webhook arrives, a new bar has opened. Order types are chosen to avoid chasing and improve fill quality:

| Setup | OANDA Order Type | Time In Force | Rationale |
|-------|-----------------|---------------|-----------|
| `bos`, `bos_div`, `spring`, `upthrust`, `mid_bos` | **LIMIT** at `entry_price` | GFD | Enter at the confirmation close or better; if price runs without retesting, order expires |
| `box_break` | **STOP** at `entry_price` | GFD | Enter only if price confirms at or above the breakout close; protects against false breaks |
| Limit rejected (price already past level) | **MARKET** fallback | — | Logged in Railway console; fills at current market |

Pending orders (not yet filled at signal time) show as "⏳ LIMIT ORDER PENDING" or "⏳ STOP ORDER PENDING" in Telegram.

**`GFD` is valid for LIMIT and STOP orders only — never use it with MARKET orders** (OANDA returns HTTP 400 `TIME_IN_FORCE_INVALID`).

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

### Micro Futures Mirroring

Micro futures (MNQ, MES, MYM, MGC, MCL) do not have their own signal generation. They previously ran a separate, less mature TradingView chart/script (the TopstepX/futures track) that frequently disagreed in direction with the proven v5 script — e.g. MNQ went long into a falling market that US100's script correctly read as short, 3 straight SL losses (found 2026-07-03).

As of `MICRO_MIRROR_MAP` in `server.py`, each micro instrument mirrors its parent CFD instrument's signal instead:

| Parent (signal source) | Micro (mirrored) |
|---|---|
| US100 | MNQ |
| US500 | MES |
| US30  | MYM |
| XAUUSD | MGC |
| USOIL | MCL |

- `webhook_paper()` **ignores** any webhook arriving directly for a micro symbol (`MICRO_MIRROR_TARGETS`) — those charts' alerts should be turned off in TradingView, but are ignored server-side as a backstop either way.
- When the parent's entry signal or lifecycle event (`tp1_hit`/`tp2_hit`/`sl_hit`) is processed, `handle_paper_signal`/`handle_paper_lifecycle` recurse once with a copy of the payload with `symbol`/`instrument` swapped to the micro symbol — same direction/entry/SL/TP/setup, independently sized via the micro's own `PAPER_INSTRUMENT_CONFIG` entry in `risk.py`, independently blocked by `db.has_open_trade()` per instrument.
- Mirrored trades' PNL reuses the parent's realized PNL dollar figure directly (both are risk-normalized to the same `$` target via `RISK_PER_TRADE`, so this is a good approximation, not an exact re-derivation from tick data).
- Mirrored trades post to the **same shared** `paper_state` account balance/daily/weekly PNL as every other instrument — this was a deliberate choice, not an oversight.

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
| `/latest-signal` | GET | Latest entry signal per instrument (12 non-micro instruments), polled by the FTMO MT5 EA — see below |

---

## MT5 EA (FTMO account, added 2026-07-04)

`mt5_ea/FTMO_Signal_EA.mq5` polls `GET /latest-signal` and trades the same 12 instruments on the FTMO MT5 account (separate from the OANDA-demo paper account above — no shared state, no shared risk limits). Full spec/history in `MT5_EA_handoff.md` at repo root.

- **Entry-only polling.** The EA never polls for TP1/TP2/SL lifecycle events — exit management is 100% local, driven by the EA watching live broker price. This was a deliberate choice: Pine Script's lifecycle webhooks lag real price by up to 15 min (bar-close driven) and could be missed entirely if a poll cycle or the server has any hiccup, which is an acceptable risk for the paper account's PNL tracking but not for a real funded eval account.
- **Broker-side safety net:** every order sets SL = signal's `stop_loss` and TP = signal's `tp2` at placement time. Even if the EA process crashes, the position still has a hard floor and ceiling enforced by the broker.
- **While running**, the EA improves on that: closes 50% at TP1 + moves SL to breakeven, then trails (1.5R → lock 1R, then 0.5R behind peak) — matching the v5 strategy's documented TP/SL behavior, computed locally from live price, not from a server poll.
- **Position sizing** is broker-generic (uses `SYMBOL_TRADE_TICK_VALUE`/`SYMBOL_TRADE_TICK_SIZE`, not hardcoded pip values like `risk.py`) — works uniformly across forex/indices/metals/oil/crypto and scales to whatever the live FTMO account balance actually is. `InpRiskPercent` input, default 0.5% (matches the paper bot's $500/$100K convention).
- **Signal tracking is local-only** (MT5 `GlobalVariable`, last-seen `signal_id` per instrument) — server stays stateless, no "consumed" endpoint.
- **Assumes a Hedging account** (not Netting) — the code relies on the filled order's ticket carrying through to the resulting position for per-ticket state (TP1 price, phase, trailing peak, stored as `GlobalVariable`s keyed by ticket). Confirm FTMO account `600063135` is in Hedge mode before running live (still an open item — see Known Gaps).
- **Setup required before running:** whitelist the Railway URL under Tools > Options > Expert Advisors > Allow WebRequest; edit the `InpSymbolMap_*` inputs to match this specific broker's actual Market Watch symbol names (may differ from the bot's names, e.g. `US100` vs `US100.cash`).
- Micro futures (MNQ/MES/MYM/MGC/MCL) are intentionally excluded — same reasoning as the paper bot's mirroring, no point double-trading the same underlying signal.

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
