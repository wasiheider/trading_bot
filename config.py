import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# ── Anthropic ─────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL      = "claude-sonnet-4-20250514"   # update when model changes

# ── TPT (Take Profit Trader) ───────────────────────────────
TPT_WEBHOOK_TOKEN = os.getenv("TPT_WEBHOOK_TOKEN")
TPT_ACCOUNT_SIZE  = float(os.getenv("TPT_ACCOUNT_SIZE", "150000"))

# ── FTMO ──────────────────────────────────────────────────
FTMO_WEBHOOK_TOKEN = os.getenv("FTMO_WEBHOOK_TOKEN")
FTMO_ACCOUNT_SIZE  = float(os.getenv("FTMO_ACCOUNT_SIZE", "100000"))

# ── Risk ──────────────────────────────────────────────────
RISK_PER_TRADE    = 0.0025   # 0.25%
MAX_CONSEC_LOSSES = 2
KILL_HOUR_CT      = 15
KILL_MINUTE_CT    = 55

# ── TPT Rule: 2–5 contracts (Pine Script sizes, Railway caps) ──
TPT_MAX_CONTRACTS = 5
TPT_MIN_CONTRACTS = 2

# ── TPT Rule: drawdown basis $45k (TPT evaluation threshold) ──
# Pine Script uses this same basis: risk_dollars = $112.50 (0.25% of $45k)
TPT_DRAWDOWN_BASIS = 45000.0
TPT_RISK_DOLLARS   = TPT_DRAWDOWN_BASIS * RISK_PER_TRADE  # $112.50

# ── TPT Rule: account floor $145,500 ──────────────────────
TPT_DRAWDOWN_FLOOR = 145500.0
