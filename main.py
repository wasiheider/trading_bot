# =============================================================================
# MAIN.PY — TRADING BOT ENTRY POINT
# Apex  : Intraday — hard kill at 2:55 PM CT, reset midnight CT
# FTMO  : Swing    — 24/5, weekend block only, no kill switch
# =============================================================================

import asyncio
import logging
import os
from datetime import datetime

import pytz
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
from server import app
from notifier import send_telegram

# -----------------------------------------------------------------------------
# LOGGING
# -----------------------------------------------------------------------------
os.makedirs("logs", exist_ok=True)
os.makedirs("data", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(__name__)

CT = pytz.timezone("America/Chicago")

# -----------------------------------------------------------------------------
# APEX KILL SWITCH STATE
# -----------------------------------------------------------------------------
apex_trading_allowed = True

def is_apex_allowed() -> bool:
    return apex_trading_allowed

# Inject into server so webhook can check it
app.state.apex_allowed = True

# -----------------------------------------------------------------------------
# SCHEDULED JOBS
# -----------------------------------------------------------------------------

def apex_kill():
    """Hard kill Apex trading at 2:55 PM CT — intraday cutoff."""
    global apex_trading_allowed
    apex_trading_allowed = False
    app.state.apex_allowed = False
    now_ct = datetime.now(CT).strftime("%Y-%m-%d %H:%M:%S CT")
    log.warning(f"[APEX] Trading KILLED at {now_ct} — intraday cutoff 2:55 PM CT")
    send_telegram(
        "🛑 *APEX TRADING KILLED*\n"
        f"Time: `{now_ct}`\n"
        "Reason: 2:55 PM CT intraday cutoff\n"
        "No new Apex signals will be processed today."
    )

def apex_reset():
    """Re-enable Apex trading at midnight CT — new trading day."""
    global apex_trading_allowed
    apex_trading_allowed = True
    app.state.apex_allowed = True
    now_ct = datetime.now(CT).strftime("%Y-%m-%d %H:%M:%S CT")
    log.info(f"[APEX] Trading RESET at {now_ct} — new day, signals re-enabled")
    send_telegram(
        "✅ *APEX TRADING RESET*\n"
        f"Time: `{now_ct}`\n"
        "New trading day — signals enabled."
    )

def daily_pnl_reset():
    """Reset daily P&L tracking at midnight CT for both accounts."""
    now_ct = datetime.now(CT).strftime("%Y-%m-%d %H:%M:%S CT")
    log.info(f"[BOT] Daily P&L reset at {now_ct}")

    # Reset risk engine daily counters
    try:
        from risk import reset_daily_pnl
        reset_daily_pnl()
        log.info("[BOT] Risk engine daily P&L reset complete")
    except ImportError:
        log.info("[BOT] No reset_daily_pnl function found in risk.py — skipping")

    send_telegram(
        "🔄 *DAILY P&L RESET*\n"
        f"Time: `{now_ct}`\n"
        "Both accounts reset for new trading day."
    )

def health_ping():
    """Log a heartbeat every hour so Railway doesn't idle."""
    now_ct = datetime.now(CT).strftime("%Y-%m-%d %H:%M:%S CT")
    apex_status = "✅ ACTIVE" if apex_trading_allowed else "🛑 KILLED"
    log.info(f"[HEARTBEAT] {now_ct} | Apex: {apex_status} | FTMO: ✅ 24/5 ACTIVE")

def ftmo_weekend_check():
    """Log FTMO weekend status — no kill, just awareness."""
    now_ct  = datetime.now(CT)
    weekday = now_ct.weekday()  # 5=Sat, 6=Sun
    if weekday >= 5:
        log.info("[FTMO] Weekend — markets closed, no signals expected")
    else:
        log.info("[FTMO] Weekday — swing trading active 24/5")

# -----------------------------------------------------------------------------
# SCHEDULER SETUP
# -----------------------------------------------------------------------------
def setup_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=CT)

    # Apex: hard kill at 2:55 PM CT Monday–Friday
    scheduler.add_job(
        apex_kill,
        trigger="cron",
        day_of_week="mon-fri",
        hour=14,
        minute=55,
        id="apex_kill",
        name="Apex Hard Kill 2:55 PM CT"
    )

    # Apex: re-enable at midnight CT Monday–Friday
    scheduler.add_job(
        apex_reset,
        trigger="cron",
        day_of_week="mon-fri",
        hour=0,
        minute=0,
        id="apex_reset",
        name="Apex Reset Midnight CT"
    )

    # Daily P&L reset at midnight CT
    scheduler.add_job(
        daily_pnl_reset,
        trigger="cron",
        hour=0,
        minute=1,
        id="daily_pnl_reset",
        name="Daily P&L Reset"
    )

    # Hourly heartbeat
    scheduler.add_job(
        health_ping,
        trigger="interval",
        hours=1,
        id="health_ping",
        name="Hourly Heartbeat"
    )

    # FTMO weekend check — every Friday at 5 PM CT
    scheduler.add_job(
        ftmo_weekend_check,
        trigger="cron",
        day_of_week="fri",
        hour=17,
        minute=0,
        id="ftmo_weekend",
        name="FTMO Weekend Check"
    )

    return scheduler

# -----------------------------------------------------------------------------
# STARTUP EVENT
# -----------------------------------------------------------------------------
@app.on_event("startup")
async def startup_event():
    now_ct = datetime.now(CT).strftime("%Y-%m-%d %H:%M:%S CT")
    log.info("=" * 60)
    log.info("  TRADING BOT STARTING UP")
    log.info(f"  Time    : {now_ct}")
    log.info("  Apex    : Intraday — kill 2:55 PM CT")
    log.info("  FTMO    : Swing   — 24/5 active")
    log.info("=" * 60)

    scheduler = setup_scheduler()
    scheduler.start()
    log.info("[SCHEDULER] All jobs scheduled")

    send_telegram(
        "🚀 *TRADING BOT ONLINE*\n"
        f"Time: `{now_ct}`\n\n"
        "📊 *Apex*: Intraday | Kill @ 2:55 PM CT\n"
        "📈 *FTMO*: Swing | 24/5 Active\n\n"
        "Webhooks ready:\n"
        "`/webhook/apex` — MNQ, MCL, MGC\n"
        "`/webhook/ftmo` — Gold, Forex, Indices"
    )

# -----------------------------------------------------------------------------
# ENTRY POINT
# -----------------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    log.info(f"[MAIN] Starting server on port {port}")
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
        log_level="info"
    )
