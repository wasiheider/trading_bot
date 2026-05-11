# =============================================================================
# TRADING BOT — MASTER CONFIGURATION (FINAL)
# =============================================================================
# FTMO  → TradingView Premium (Pine Script places orders on OANDA broker)
# Apex  → Tradovate REST API (Python places orders directly)
# =============================================================================
# Never share this file or commit it to GitHub.
# All times are CT (Central Time).
# =============================================================================
import os
from dotenv import load_dotenv

load_dotenv()

# -----------------------------------------------------------------------------
# ANTHROPIC (CLAUDE API)
# -----------------------------------------------------------------------------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
CLAUDE_MODEL      = "claude-sonnet-4-5"


# -----------------------------------------------------------------------------
# TELEGRAM
# -----------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID")


# -----------------------------------------------------------------------------
# FTMO — TRADINGVIEW PREMIUM + OANDA BROKER
# -----------------------------------------------------------------------------
# Orders placed directly by Pine Script strategy.entry() on TradingView
# Python server receives webhook AFTER order is placed — for validation,
# logging, risk monitoring, and Telegram alerts only.

FTMO_TV_ACCOUNT_ID = "650000258"          # TradingView connected OANDA account
FTMO_ACCOUNT_LABEL = "FTMO"              # Label for Telegram alerts

# Markets — TradingView symbol names for OANDA broker
FTMO_MARKETS = ["EURUSD", "GBPUSD", "XAUUSD", "XAGUSD"]

# FTMO risk rules — confirm exact numbers from your FTMO dashboard
FTMO_ACCOUNT_BALANCE         = 100000.0  # Your account size in USD
FTMO_DAILY_LOSS_LIMIT_PCT    = 5.0       # 5% = $5,000 on $100k
FTMO_MAX_DRAWDOWN_PCT        = 10.0      # 10% = $10,000 on $100k
FTMO_RISK_PER_TRADE_PCT      = 0.5       # 0.5% = $1,000 per trade
FTMO_MIN_RR_FOR_FULL_CLOSE   = 3.0       # R:R >= 1:3 → full close at TP1
FTMO_PARTIAL_CLOSE_RR        = 2.0       # R:R = 1:2 → 50% close, move SL to BE
FTMO_CLOSE_BEFORE_NEWS_MINS  = 30        # Close positions N mins before red news

# FTMO BOS timeframe
FTMO_BOS_TIMEFRAME = 60                 # 1H chart (minutes)


# -----------------------------------------------------------------------------
# APEX — TRADOVATE REST API
# -----------------------------------------------------------------------------
# Orders placed by Python via Tradovate REST API
# Python server receives TradingView webhook, validates with Claude,
# checks risk rules, then places order on Tradovate

TRADOVATE_USERNAME    = "your_tradovate_username"
TRADOVATE_PASSWORD    = "your_tradovate_password"
TRADOVATE_APP_ID      = "your_app_id"        # From Tradovate API settings
TRADOVATE_APP_VERSION = "1.0"
TRADOVATE_CID         = "your_client_id"     # Numeric client ID
TRADOVATE_SEC         = "your_client_secret" # Client secret

# Use "demo" for paper trading, "live" for real money
TRADOVATE_ENVIRONMENT = "demo"               # KEEP AS DEMO UNTIL FULLY TESTED

APEX_ACCOUNT_LABEL = "APEX"                  # Label for Telegram alerts

# Markets — Tradovate contract names
APEX_MARKETS = ["ES", "NQ", "MES", "MNQ", "CL", "NG", "GC", "SI"]

# Apex risk rules — confirm exact numbers from your Apex dashboard
APEX_ACCOUNT_BALANCE       = 50000.0   # Your Apex account size in USD
APEX_DAILY_LOSS_LIMIT_USD  = 1000.0    # Replace with your plan's exact $ limit
APEX_MAX_DRAWDOWN_USD      = 2500.0    # Replace with your plan's exact $ limit
APEX_RISK_PER_TRADE_PCT    = 0.25       # 0.25% risk per trade
APEX_MAX_CONTRACTS         = 2         # Max contracts per trade
APEX_MIN_RR_FOR_FULL_CLOSE = 3.0       # R:R >= 1:3 → full close at TP1
APEX_PARTIAL_CLOSE_RR      = 2.0       # R:R = 1:2 → 50% close, move SL to BE

# Apex BOS timeframe
APEX_BOS_TIMEFRAME = 5                 # 5-minute chart


# -----------------------------------------------------------------------------
# SESSION RULES — APEX
# -----------------------------------------------------------------------------
# New entries only accepted during these CT windows
APEX_SESSIONS = [
    {"name": "London",   "start": "01:00", "end": "04:00"},
    {"name": "New York", "start": "08:00", "end": "11:00"},
]

# Hard kill — ALL Apex positions force-closed at this time daily (CT)
APEX_FORCE_CLOSE_TIME = "15:55"   # 3:55pm CT — 5 min before Apex 3pm cutoff

# No-trade zone — bot blocks all new Apex signals in this window
APEX_NO_TRADE_START = "15:00"     # 3:00pm CT
APEX_NO_TRADE_END   = "17:00"     # 5:00pm CT


# -----------------------------------------------------------------------------
# SESSION RULES — FTMO
# -----------------------------------------------------------------------------
# No entry gate — 24/5 signals accepted
# Positions held multi-day
# Only closes: TP hit, SL hit, news event, or manual Telegram command


# -----------------------------------------------------------------------------
# WYCKOFF STRATEGY — SHARED SETTINGS
# -----------------------------------------------------------------------------
BOS_CONFIRMATION_CANDLES = 2       # Consecutive closes beyond range boundary
MIN_MIDPOINT_TOUCHES     = 2       # Touches required to validate range


# -----------------------------------------------------------------------------
# WEBHOOK SECURITY
# -----------------------------------------------------------------------------
# Secret token sent by TradingView with every webhook request
# Add to your TV alert URL as: ?token=YOUR_SECRET_TOKEN
WEBHOOK_SECRET_APEX = "apex_xK9mP2qL8nR4vT7w"
WEBHOOK_SECRET_FTMO = "ftmo_xK9mP2qL8nR4vT7w"


# -----------------------------------------------------------------------------
# LOGGING
# -----------------------------------------------------------------------------
LOG_FILE      = "logs/bot.log"
DATABASE_FILE = "data/trades.db"


# -----------------------------------------------------------------------------
# NEWS FILTER
# -----------------------------------------------------------------------------
NEWS_IMPACTS_TO_WATCH = ["High"]
NEWS_CURRENCIES       = ["USD", "EUR", "GBP", "XAU"]
