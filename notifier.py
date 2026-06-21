# =============================================================================
# NOTIFIER.PY — TELEGRAM ALERT SYSTEM (Phase 5 — updated Phase 6)
# =============================================================================
# Sends structured Telegram alerts at every stage of the signal pipeline.
# All alerts are non-blocking — failures are logged but never crash the server.
#
# Alert stages:
#   1. Signal received
#   2. Claude approved / rejected
#   3. Risk engine passed / blocked
#   4. Ready to execute (with sizing)
#   5. Hard kill triggered
#   6. Daily loss limit hit
# =============================================================================

import logging
import requests
from datetime import datetime
from typing import Optional
import pytz

import config

log = logging.getLogger(__name__)

CT = pytz.timezone("America/Chicago")

# -----------------------------------------------------------------------------
# CORE SEND FUNCTION
# -----------------------------------------------------------------------------

def send_telegram(message: str, account: str = "SYSTEM", alert_type: str = "GENERAL") -> bool:
    """
    Send a message to the configured Telegram chat.
    Returns True if successful, False if failed.
    Never raises — failures are swallowed and logged.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.warning("[TELEGRAM] Bot token or chat ID not configured — skipping alert")
        return False

    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }

    try:
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code == 200:
            log.info("[TELEGRAM] Alert sent successfully")
            return True
        else:
            log.warning(f"[TELEGRAM] Send failed — HTTP {response.status_code}: {response.text}")
            return False
    except requests.exceptions.Timeout:
        log.warning("[TELEGRAM] Alert timed out — Telegram may be slow")
        return False
    except Exception as e:
        log.warning(f"[TELEGRAM] Alert failed — {e}")
        return False


def now_ct_str() -> str:
    """Return current CT time as formatted string."""
    return datetime.now(CT).strftime("%H:%M:%S CT")


# -----------------------------------------------------------------------------
# ALERT 1 — SIGNAL RECEIVED
# -----------------------------------------------------------------------------

def alert_signal_received(signal) -> None:
    """Fired immediately when a webhook signal arrives."""
    message = (
        f"📡 <b>SIGNAL RECEIVED</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 Account  : <b>{signal.account}</b>\n"
        f"📊 Symbol   : <b>{signal.symbol}</b>\n"
        f"{'🔴' if signal.direction == 'SHORT' else '🟢'} Direction : <b>{signal.direction}</b>\n"
        f"⏱ Timeframe : {signal.timeframe}m\n"
        f"🕐 Session  : {signal.session}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📈 Range H  : {signal.range_high}\n"
        f"➖ Range Mid : {signal.range_mid}\n"
        f"📉 Range L  : {signal.range_low}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Entry    : {signal.entry_price}\n"
        f"🛑 Stop     : {signal.stop_loss}\n"
        f"✅ TP1      : {signal.tp1}\n"
        f"🏆 TP2      : {signal.tp2}\n"
        f"📐 R:R      : {signal.rr_to_tp1:.1f}:1\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {now_ct_str()}"
    )
    send_telegram(message, account=signal.account, alert_type="SIGNAL_RECEIVED")


# -----------------------------------------------------------------------------
# ALERT 2 — CLAUDE DECISION
# -----------------------------------------------------------------------------

def alert_claude_approved(signal, decision: dict) -> None:
    """Fired when Claude approves a signal."""
    message = (
        f"🤖 <b>CLAUDE APPROVED</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 {signal.account} | {signal.symbol} {signal.direction}\n"
        f"💯 Confidence : <b>{decision.get('confidence', '?')}%</b>\n"
        f"📋 Action     : <b>{decision.get('action', '?')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💬 {decision.get('reasoning', '')}\n"
    )

    warnings = decision.get("warnings", [])
    if warnings:
        message += "━━━━━━━━━━━━━━━━━━━━\n"
        for w in warnings:
            message += f"⚠️ {w}\n"

    message += f"━━━━━━━━━━━━━━━━━━━━\n🕒 {now_ct_str()}"
    send_telegram(message, account=signal.account, alert_type="CLAUDE_APPROVED")


def alert_claude_rejected(signal, decision: dict) -> None:
    """Fired when Claude rejects a signal."""
    message = (
        f"🚫 <b>CLAUDE REJECTED</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 {signal.account} | {signal.symbol} {signal.direction}\n"
        f"💯 Confidence : {decision.get('confidence', '?')}%\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"❌ Reason: {decision.get('reasoning', 'No reason provided')}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {now_ct_str()}"
    )
    send_telegram(message, account=signal.account, alert_type="CLAUDE_REJECTED")


# -----------------------------------------------------------------------------
# ALERT 3 — RISK ENGINE
# -----------------------------------------------------------------------------

def alert_risk_passed(signal, reason: str, sizing: dict) -> None:
    """Fired when the risk engine passes a signal."""
    if signal.account == "APEX":
        sizing_line = f"📦 Contracts : <b>{sizing.get('contracts', '?')}</b>"
    else:
        sizing_line = f"📦 Lots      : <b>{sizing.get('lots', '?')}</b>"

    dollar_risk = sizing.get('dollar_risk', 0)

    message = (
        f"✅ <b>RISK ENGINE PASSED</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 {signal.account} | {signal.symbol} {signal.direction}\n"
        f"{sizing_line}\n"
        f"💵 $ Risk    : <b>${dollar_risk:.2f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {now_ct_str()}"
    )
    send_telegram(message, account=signal.account, alert_type="RISK_PASSED")


def alert_risk_blocked(signal, reason: str) -> None:
    """Fired when the risk engine blocks a signal."""
    message = (
        f"🛡 <b>RISK ENGINE BLOCKED</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 {signal.account} | {signal.symbol} {signal.direction}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"❌ Reason: {reason}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {now_ct_str()}"
    )
    send_telegram(message, account=signal.account, alert_type="RISK_BLOCKED")


# -----------------------------------------------------------------------------
# ALERT 4 — READY TO EXECUTE
# -----------------------------------------------------------------------------

def alert_ready_to_execute(signal, decision: dict, sizing: dict) -> None:
    """Fired when signal clears all checks and is ready for execution."""
    action = decision.get("action", "?")

    if signal.account == "APEX":
        sizing_line = f"📦 Contracts : <b>{sizing.get('contracts', '?')}</b>"
        session_line = f"🕐 Session   : {sizing.get('session', signal.session)}\n"
    else:
        sizing_line = f"📦 Lots      : <b>{sizing.get('lots', '?')}</b>"
        session_line = ""

    dollar_risk = sizing.get('dollar_risk', 0)

    action_desc = {
        "FULL_CLOSE_TP1": "Full close at TP1 (R:R ≥ 1:3)",
        "PARTIAL_CLOSE_TP1": "50% at TP1 → SL to BE → run to TP2",
    }.get(action, action)

    message = (
        f"🚀 <b>READY TO EXECUTE</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 Account   : <b>{signal.account}</b>\n"
        f"📊 Symbol    : <b>{signal.symbol}</b>\n"
        f"{'🔴' if signal.direction == 'SHORT' else '🟢'} Direction  : <b>{signal.direction}</b>\n"
        f"{session_line}"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Entry     : <b>{signal.entry_price}</b>\n"
        f"🛑 Stop      : {signal.stop_loss}\n"
        f"✅ TP1       : {signal.tp1}\n"
        f"🏆 TP2       : {signal.tp2}\n"
        f"📐 R:R       : <b>{signal.rr_to_tp1:.1f}:1</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{sizing_line}\n"
        f"💵 $ Risk    : ${dollar_risk:.2f}\n"
        f"📋 Plan      : {action_desc}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {now_ct_str()}"
    )
    send_telegram(message, account=signal.account, alert_type="READY_TO_EXECUTE")


# -----------------------------------------------------------------------------
# ALERT 5 — HARD KILL
# -----------------------------------------------------------------------------

def alert_hard_kill(account: str, reason: str, positions_closed: int = 0) -> None:
    """Fired when the hard kill switch is triggered (e.g. 2:55pm CT force close)."""
    message = (
        f"🔴 <b>HARD KILL TRIGGERED</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 Account         : <b>{account}</b>\n"
        f"❌ Reason          : {reason}\n"
        f"📦 Positions closed: {positions_closed}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚠️ All trading halted for this session.\n"
        f"🕒 {now_ct_str()}"
    )
    send_telegram(message, account=account, alert_type="HARD_KILL")


# -----------------------------------------------------------------------------
# ALERT 6 — DAILY LOSS LIMIT
# -----------------------------------------------------------------------------

def alert_daily_loss_limit(account: str, current_loss: float, limit: float) -> None:
    """Fired when the daily loss limit is breached."""
    message = (
        f"🚨 <b>DAILY LOSS LIMIT HIT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 Account     : <b>{account}</b>\n"
        f"📉 Current Loss: <b>${abs(current_loss):.2f}</b>\n"
        f"🛑 Limit       : ${abs(limit):.2f}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⛔ No further trades will be taken today.\n"
        f"🔄 Resets at next session open.\n"
        f"🕒 {now_ct_str()}"
    )
    send_telegram(message, account=account, alert_type="DAILY_LOSS_LIMIT")


# -----------------------------------------------------------------------------
# CONVENIENCE — GENERIC ERROR ALERT
# -----------------------------------------------------------------------------

def alert_error(account: str, context: str, error: str) -> None:
    """Generic error alert for unexpected failures."""
    message = (
        f"⚠️ <b>BOT ERROR</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🏦 Account : {account}\n"
        f"📍 Context : {context}\n"
        f"❌ Error   : {error}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🕒 {now_ct_str()}"
    )
    send_telegram(message, account=account, alert_type="ERROR")
