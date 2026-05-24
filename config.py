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

# ── Risk ──────────────────────────────────────────────────
RISK_PER_TRADE = 0.01   # 1% per trade (paper account — no prop firm limits)
