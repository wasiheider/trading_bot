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

# ── Data persistence — set DATA_DIR=/data in Railway after mounting a Volume ──
DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))

# ── Risk ──────────────────────────────────────────────────
RISK_PER_TRADE = 0.0025  # 0.25% per trade ($250 on $100k)

# ── Risk Limits (breach = signals still fire, OANDA skipped) ──
MAX_DAILY_LOSS     = 3000.0  # $3,000 daily loss
MAX_DAILY_SL_HITS  = 6       # SL hits across all instruments per day
MAX_WEEKLY_LOSS    = 5000.0  # $5,000 weekly loss
MAX_WEEKLY_SL_HITS = 10      # SL hits across all instruments per week
