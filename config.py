import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")

# ── Anthropic ─────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

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

# ── TPT Rule: max 15 contracts at any time ─────────────────
TPT_MAX_CONTRACTS = 15

# ── TPT Rule: account floor $145,500 ──────────────────────
TPT_DRAWDOWN_FLOOR = 145500.0