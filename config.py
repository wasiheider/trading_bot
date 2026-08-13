import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# ── Anthropic ─────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL      = "claude-sonnet-4-20250514"

# ── Paper Trading ─────────────────────────────────────────
PAPER_WEBHOOK_TOKEN = os.getenv("PAPER_WEBHOOK_TOKEN")
PAPER_ACCOUNT_SIZE  = float(os.getenv("PAPER_ACCOUNT_SIZE", "100000"))

# ── OANDA ─────────────────────────────────────────────────
OANDA_API_TOKEN  = os.getenv("OANDA_API_TOKEN")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
OANDA_BASE_URL   = os.getenv("OANDA_BASE_URL", "https://api-fxpractice.oanda.com")

# ── PickMyTrade (Apex Trader Funding via Tradovate) ────────
# Separate real-money account -- no shared state with risk.py/paper_state.
PICKMYTRADE_TOKEN      = os.getenv("PICKMYTRADE_TOKEN", "")
PICKMYTRADE_ACCOUNT_ID = os.getenv("PICKMYTRADE_ACCOUNT_ID", "")

# ── Risk ──────────────────────────────────────────────────
RISK_PER_TRADE = 0.005   # 0.5% per trade ($500 on $100k)

# ── Risk Limits (breach = signals still fire, OANDA skipped) ──
MAX_DAILY_LOSS  = 4000.0  # $4,000 daily loss (4% of $100k)
MAX_WEEKLY_LOSS = 10000.0  # $10,000 weekly loss (10% of $100k)

# ── Entry D (mid_bos) LONG confirmation filter — added 2026-07-22 ──
# Backtest on 150 trades (2026-06-19 to 2026-07-22) found mid_bos LONG entries
# that fill too close to the range midpoint lose money as a group (skipped-
# bucket PNL negative at every threshold tested), while mid_bos SHORT shows
# the opposite pattern (skipped-bucket PNL strongly positive) — so this is
# intentionally LONG-only. Value is the minimum entry-price distance above
# the range midpoint, as % of range size, required for a mid_bos LONG signal
# to be taken. Market regime (currently choppy/downtrending) can shift this
# relationship — revisit in a few months once there's a fresh batch of
# mid_bos LONG trades to re-check against.
#
# Disabled 2026-08-13 (user call: market sentiment/regime has moved on from
# the backtest window above) — threshold kept for reference / easy re-enable,
# not deleted.
MID_BOS_LONG_FILTER_ENABLED = False
MID_BOS_LONG_MIN_MID_DIST_PCT = 5.0
