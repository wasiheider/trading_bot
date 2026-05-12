# =============================================================================
# SERVER.PY — FASTAPI WEBHOOK RECEIVER (Phase 4 — Risk engine added)
# =============================================================================

import logging
import os
from datetime import datetime
from fastapi import FastAPI, HTTPException, BackgroundTasks, Request
from pydantic import BaseModel, validator
from typing import Optional
import pytz

import config
from validator import validate_signal
from risk import run_risk_checks
from notifier import (
    alert_signal_received,
    alert_claude_approved,
    alert_claude_rejected,
    alert_risk_passed,
    alert_risk_blocked,
    alert_ready_to_execute,
)
from logger import (
    init_db,
    log_signal,
    log_decision,
    log_risk_result,
    log_trade_event,
    log_alert,
    get_recent_logs,
)

# -----------------------------------------------------------------------------
# LOGGING SETUP
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

# -----------------------------------------------------------------------------
# FASTAPI APP
# -----------------------------------------------------------------------------
app = FastAPI(title="Trading Bot Webhook Server", version="1.0.0")
init_db()

# -----------------------------------------------------------------------------
# SIGNAL PAYLOAD MODEL
# -----------------------------------------------------------------------------
class SignalPayload(BaseModel):
    account:     str
    symbol:      str
    direction:   str
    token:       str
    timeframe:   Optional[str]   = "5m"
    range_high:  Optional[float] = 0.0
    range_low:   Optional[float] = 0.0
    range_mid:   Optional[float] = 0.0
    bos_extreme: Optional[float] = 0.0
    entry_price: Optional[float] = 0.0
    stop_loss:   Optional[float] = 0.0
    tp1:         Optional[float] = 0.0
    tp2:         Optional[float] = 0.0
    rr_to_tp1:   Optional[float] = 0.0
    session:     Optional[str]   = "unknown"
    bar_time:    Optional[str]   = ""
    notes:       Optional[str]   = ""
    test_mode:   Optional[bool]  = False

    @validator("direction")
    def direction_must_be_valid(cls, v):
        if v.upper() not in ["LONG", "SHORT"]:
            raise ValueError("direction must be LONG or SHORT")
        return v.upper()

    @validator("account")
    def account_must_be_valid(cls, v):
        if v.upper() not in ["APEX", "FTMO"]:
            raise ValueError("account must be APEX or FTMO")
        return v.upper()

# -----------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------
def validate_token(account: str, token: str):
    expected = config.WEBHOOK_SECRET_APEX if account == "APEX" else config.WEBHOOK_SECRET_FTMO
    if token != expected:
        log.warning(f"Invalid token received for {account} account")
        raise HTTPException(status_code=401, detail="Invalid webhook token")

def log_signal(signal: SignalPayload, status: str = "RECEIVED"):
    ct = pytz.timezone("America/Chicago")
    now_ct = datetime.now(ct).strftime("%Y-%m-%d %H:%M:%S CT")
    log.info(
        f"\n"
        f"{'='*60}\n"
        f"  SIGNAL {status}\n"
        f"  Time     : {now_ct}\n"
        f"  Account  : {signal.account}\n"
        f"  Symbol   : {signal.symbol}\n"
        f"  Direction: {signal.direction}\n"
        f"  Timeframe: {signal.timeframe}m\n"
        f"  Session  : {signal.session}\n"
        f"  Range H  : {signal.range_high}\n"
        f"  Range Mid: {signal.range_mid}\n"
        f"  Range L  : {signal.range_low}\n"
        f"  Entry    : {signal.entry_price}\n"
        f"  Stop     : {signal.stop_loss}\n"
        f"  TP1      : {signal.tp1}  (R:R {signal.rr_to_tp1:.1f})\n"
        f"  TP2      : {signal.tp2}\n"
        f"{'='*60}"
    )

# -----------------------------------------------------------------------------
# PIPELINES
# -----------------------------------------------------------------------------
async def route_signal(signal: SignalPayload):
    if signal.account == "APEX":
        await process_apex_signal(signal)
    else:
        await process_ftmo_signal(signal)

async def process_apex_signal(signal: SignalPayload):
    log.info(f"[APEX] Signal received — {signal.symbol} {signal.direction}")
    log_signal(signal)
    alert_signal_received(signal)

    # Step 1 — Claude validation
    decision = await validate_signal(signal)
    log_decision(signal, decision)
    if not decision["approve"]:
        log.info(f"[APEX] Signal REJECTED by Claude — {decision['reasoning']}")
        alert_claude_rejected(signal, decision)
        return

    log.info(f"[APEX] Claude APPROVED — confidence {decision['confidence']}% | action: {decision['action']}")
    alert_claude_approved(signal, decision)

    # Step 2 — Risk engine
    passed, reason, sizing = run_risk_checks(signal)
    log_risk_result(signal, passed, reason, sizing)
    if not passed:
        log.warning(f"[APEX] Signal BLOCKED by risk engine — {reason}")
        alert_risk_blocked(signal, reason)
        return

    log.info(f"[APEX] Risk engine PASSED — {reason}")
    log.info(f"[APEX] Sizing: {sizing}")
    alert_risk_passed(signal, reason, sizing)

    # Step 3 — Order execution (Phase 7)
    log_trade_event(signal, decision, sizing)
    alert_ready_to_execute(signal, decision, sizing)
    log.info(f"[APEX] Ready to execute — {sizing.get('contracts', 0)} contract(s). Execution coming in Phase 7.")

async def process_ftmo_signal(signal: SignalPayload):
    log.info(f"[FTMO] Signal received — {signal.symbol} {signal.direction}")
    log_signal(signal)
    alert_signal_received(signal)

    # Step 1 — Claude validation
    decision = await validate_signal(signal)
    log_decision(signal, decision)
    if not decision["approve"]:
        log.info(f"[FTMO] Signal REJECTED by Claude — {decision['reasoning']}")
        alert_claude_rejected(signal, decision)
        return

    log.info(f"[FTMO] Claude APPROVED — confidence {decision['confidence']}% | action: {decision['action']}")
    alert_claude_approved(signal, decision)

    # Step 2 — Risk engine
    passed, reason, sizing = run_risk_checks(signal)
    log_risk_result(signal, passed, reason, sizing)
    if not passed:
        log.warning(f"[FTMO] Signal BLOCKED by risk engine — {reason}")
        alert_risk_blocked(signal, reason)
        return

    log.info(f"[FTMO] Risk engine PASSED — {reason}")
    log.info(f"[FTMO] Sizing: {sizing}")
    alert_risk_passed(signal, reason, sizing)

    # Step 3 — Order execution (Phase 7)
    log_trade_event(signal, decision, sizing)
    alert_ready_to_execute(signal, decision, sizing)
    log.info(f"[FTMO] Ready to execute — {sizing.get('lots', 0)} lot(s). Execution coming in Phase 7.")

# -----------------------------------------------------------------------------
# WEBHOOK ENDPOINTS
# -----------------------------------------------------------------------------
@app.post("/webhook/apex")
async def webhook_apex(signal: SignalPayload, background_tasks: BackgroundTasks, request: Request):
    validate_token("APEX", signal.token)
    if signal.account != "APEX":
        raise HTTPException(status_code=400, detail="Account mismatch — expected APEX")
    if not getattr(request.app.state, "apex_allowed", True):
        raise HTTPException(status_code=403, detail="Apex trading killed — past 2:55 PM CT cutoff")
    log_signal(signal, "RECEIVED — APEX")
    background_tasks.add_task(route_signal, signal)
    return {
        "status": "received",
        "account": signal.account,
        "symbol": signal.symbol,
        "direction": signal.direction,
        "message": "Signal accepted and queued for processing"
    }

@app.post("/webhook/ftmo")
async def webhook_ftmo(signal: SignalPayload, background_tasks: BackgroundTasks):
    validate_token("FTMO", signal.token)
    if signal.account != "FTMO":
        raise HTTPException(status_code=400, detail="Account mismatch — expected FTMO")
    log_signal(signal, "RECEIVED — FTMO")
    background_tasks.add_task(route_signal, signal)
    return {
        "status": "received",
        "account": signal.account,
        "symbol": signal.symbol,
        "direction": signal.direction,
        "message": "Signal accepted and queued for processing"
    }

@app.get("/health")
async def health_check():
    ct = pytz.timezone("America/Chicago")
    now_ct = datetime.now(ct).strftime("%Y-%m-%d %H:%M:%S CT")
    return {
        "status": "online",
        "server": "Trading Bot Webhook Server",
        "time_ct": now_ct,
        "accounts": ["APEX", "FTMO"],
        "endpoints": ["/webhook/apex", "/webhook/ftmo", "/health"]
    }

@app.get("/")
async def root():
    return {"message": "Trading bot server is running. POST signals to /webhook/apex or /webhook/ftmo"}

@app.get("/logs")
async def get_logs(limit: int = 20):
    """Return recent log entries from all pipeline stages."""
    return get_recent_logs(limit=limit)
