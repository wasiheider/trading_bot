from flask import Flask, request, jsonify
from datetime import datetime
import pytz

from config import TPT_WEBHOOK_TOKEN, FTMO_WEBHOOK_TOKEN
from risk import (
    check_tpt_risk, check_ftmo_risk,
    tpt_state, ftmo_state,
    update_ftmo_outcome,
    record_tpt_signal, record_tpt_trade,
    record_ftmo_signal, record_ftmo_trade,
)
from notifier import send_telegram
from logger import log_trade_event, log_ftmo_lifecycle, init_db

app = Flask(__name__)
CT = pytz.timezone("America/Chicago")

# Initialize DB on startup
init_db()

def ct_now():
    return datetime.now(CT)

def is_tpt_killed():
    now = ct_now()
    if tpt_state["killed"]:
        return True
    if (now.hour == 15 and now.minute >= 55) or now.hour == 16:
        return True
    # Weekend: Fri 4pm CT through Sun 5pm CT
    if now.weekday() == 4 and now.hour >= 16:
        return True
    if now.weekday() == 5:
        return True
    if now.weekday() == 6 and now.hour < 17:
        return True
    return False

def is_maintenance_window():
    """True during 3:55–5:00 PM CT exchange close."""
    now = ct_now()
    return (now.hour == 15 and now.minute >= 55) or now.hour == 16


# ── TPT Webhook ────────────────────────────────────────────
@app.route("/webhook/tpt", methods=["POST"])
def webhook_tpt():
    data = request.json or {}

    if data.get("token") != TPT_WEBHOOK_TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    instrument = data.get("instrument", data.get("symbol", "")).upper().replace("1!", "").replace("!", "")
    direction  = data.get("direction", "").upper()
    price      = data.get("price", data.get("entry_price"))
    sl         = data.get("sl", data.get("stop_loss"))
    tp1        = data.get("tp1")
    tp2        = data.get("tp2")
    rr         = data.get("rr_to_tp1")

    # Pine Script calculates contracts using real SL distance — trust it directly
    # Clamp to 2–5 as a hard safety cap in case of payload issues
    contracts  = max(2, min(5, int(data.get("contracts", 2))))

    if is_tpt_killed():
        reason = "maintenance window (3:55–5:00 PM CT)" if is_maintenance_window() else "kill switch active (2 consecutive losses)"
        msg = f"🚫 *TPT Signal Blocked*\n`{instrument} {direction}` — {reason}"
        send_telegram(msg)
        return jsonify({"status": "blocked", "reason": reason}), 200

    # Only check kill switch + drawdown floor — Pine Script handles sizing
    risk = check_tpt_risk(instrument)
    if not risk["allowed"]:
        msg = f"🚫 *TPT Signal Blocked*\n`{instrument} {direction}`\nReason: {risk['reason']}"
        send_telegram(msg)
        return jsonify({"status": "blocked", "reason": risk["reason"]}), 200

    rr_line = f"\nR:R: `{rr}`" if rr else ""
    emoji = "🟢" if direction == "LONG" else "🔴"
    msg = (
        f"{emoji} *TPT Signal — {instrument} {direction}*\n"
        f"Entry: `{price}`\n"
        f"SL: `{sl}`\n"
        f"TP1: `{tp1}`\n"
        f"TP2: `{tp2}`\n"
        f"Contracts: `{contracts}`"
        f"{rr_line}"
    )
    record_tpt_signal(data)
    send_telegram(msg)

    return jsonify({"status": "approved", "contracts": contracts}), 200


# ── FTMO Webhook ───────────────────────────────────────────
@app.route("/webhook/ftmo", methods=["POST"])
def webhook_ftmo():
    data = request.json or {}

    if data.get("token") != FTMO_WEBHOOK_TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    event = data.get("event", "").lower()

    # ── Phase 8: Lifecycle events ──────────────────────────
    if event in ("entry_filled", "tp1_hit", "tp2_hit", "sl_hit"):
        return handle_ftmo_lifecycle(data, event)

    # ── Original: new signal risk check ───────────────────
    return handle_ftmo_signal(data)


def handle_ftmo_signal(data):
    """Original FTMO signal handler — risk check + Telegram alert."""
    instrument = data.get("instrument", data.get("symbol", "")).upper().replace("1!", "").replace("!", "")
    direction  = data.get("direction", "").upper()
    price      = data.get("price", data.get("entry_price"))
    sl         = data.get("sl", data.get("stop_loss"))
    tp1        = data.get("tp1")
    tp2        = data.get("tp2")
    sl_pips    = data.get("sl_pips")

    risk = check_ftmo_risk(instrument, sl_pips)
    if not risk["allowed"]:
        msg = f"🚫 *FTMO Signal Blocked*\n`{instrument} {direction}`\nReason: {risk['reason']}"
        send_telegram(msg)
        return jsonify({"status": "blocked", "reason": risk["reason"]}), 200

    emoji = "🟢" if direction == "LONG" else "🔴"
    msg = (
        f"{emoji} *FTMO Signal — {instrument} {direction}*\n"
        f"Price: `{price}`\n"
        f"SL: `{sl}`\n"
        f"TP1: `{tp1}`\n"
        f"TP2: `{tp2}`\n"
        f"Lot Size: `{risk['lot_size']}`\n"
        f"Risk: `${risk['risk_dollars']}`"
    )
    record_ftmo_signal()
    send_telegram(msg)

    return jsonify({"status": "approved", "lot_size": risk["lot_size"]}), 200


def handle_ftmo_lifecycle(data, event):
    """
    Phase 8 — FTMO order lifecycle handler.
    Called when TradingView fires entry_filled | tp1_hit | tp2_hit | sl_hit.
    Logs to DB, updates risk state, fires Telegram alert.
    """
    instrument = data.get("instrument", data.get("symbol", "")).upper().replace("1!", "").replace("!", "")
    direction  = data.get("direction", "").upper()
    price      = data.get("price", data.get("entry_price"))
    pnl        = data.get("pnl")           # optional — TradingView can pass this
    lot_size   = data.get("lot_size")
    sl         = data.get("stop_loss", data.get("sl"))
    tp1        = data.get("tp1")
    tp2        = data.get("tp2")

    # Log to DB
    log_ftmo_lifecycle(
        event=event,
        symbol=instrument,
        direction=direction,
        price=price,
        pnl=pnl,
        lot_size=lot_size,
        sl=sl,
        tp1=tp1,
        tp2=tp2,
    )

    # Update FTMO consecutive loss counter in risk engine
    if event == "sl_hit":
        update_ftmo_outcome(won=False, pnl=pnl)
    elif event in ("tp1_hit", "tp2_hit"):
        update_ftmo_outcome(won=True, pnl=pnl)

    # Build Telegram message
    emoji_map = {
        "entry_filled": "📥",
        "tp1_hit":      "✅",
        "tp2_hit":      "🏆",
        "sl_hit":       "❌",
    }
    label_map = {
        "entry_filled": "Entry Filled",
        "tp1_hit":      "TP1 Hit",
        "tp2_hit":      "TP2 Hit",
        "sl_hit":       "Stop Loss Hit",
    }

    emoji = emoji_map.get(event, "📊")
    label = label_map.get(event, event.upper())
    dir_emoji = "🟢" if direction == "LONG" else "🔴"

    pnl_line = f"\nP&L: `${pnl:+.2f}`" if pnl is not None else ""
    lots_line = f"\nLots: `{lot_size}`" if lot_size else ""

    msg = (
        f"{emoji} *FTMO {label}*\n"
        f"{dir_emoji} `{instrument} {direction}`\n"
        f"Price: `{price}`"
        f"{lots_line}"
        f"{pnl_line}"
    )
    send_telegram(msg)

    return jsonify({"status": "logged", "event": event, "symbol": instrument}), 200


# ── Manual Reset Endpoint ──────────────────────────────────
@app.route("/reset/tpt", methods=["POST"])
def reset_tpt():
    data = request.json or {}
    if data.get("token") != TPT_WEBHOOK_TOKEN:
        return jsonify({"status": "error", "reason": "invalid token"}), 403
    tpt_state["killed"] = False
    tpt_state["consecutive_losses"] = 0
    send_telegram("🔄 *TPT Reset*\nKill switch cleared — signals active.")
    return jsonify({"status": "ok", "message": "TPT state reset"}), 200


# ── State Inspection Endpoint ───────────────────────────────
@app.route("/state", methods=["GET"])
def state():
    now = ct_now()
    tpt_wins   = tpt_state.get("daily_wins", 0)
    tpt_losses = tpt_state.get("daily_losses", 0)
    tpt_total  = tpt_wins + tpt_losses
    tpt_wr     = round((tpt_wins / tpt_total) * 100) if tpt_total > 0 else 0

    ftmo_wins   = ftmo_state.get("daily_wins", 0)
    ftmo_losses = ftmo_state.get("daily_losses", 0)
    ftmo_total  = ftmo_wins + ftmo_losses
    ftmo_wr     = round((ftmo_wins / ftmo_total) * 100) if ftmo_total > 0 else 0

    return jsonify({
        # ── Meta ──────────────────────────────────────────────
        "time_ct":            now.strftime("%Y-%m-%d %H:%M:%S"),
        "is_weekend":         now.weekday() in (5, 6),
        "is_maintenance":     is_maintenance_window(),

        # ── TPT ───────────────────────────────────────────────
        "tpt_killed":             tpt_state["killed"],
        "is_tpt_killed":          is_tpt_killed(),
        "tpt_consecutive_losses": tpt_state.get("consecutive_losses", 0),
        "tpt_account_balance":    tpt_state.get("account_balance", 0),
        "tpt_daily_pnl":          round(tpt_state.get("daily_pnl", 0.0), 2),
        "tpt_daily_signals":      tpt_state.get("daily_signals", 0),
        "tpt_daily_wins":         tpt_wins,
        "tpt_daily_losses":       tpt_losses,
        "tpt_win_rate":           tpt_wr,
        "tpt_last_signal":        tpt_state.get("last_signal"),
        "tpt_trades":             tpt_state.get("trades", [])[-5:],  # last 5

        # ── FTMO ──────────────────────────────────────────────
        "ftmo_account_balance":    ftmo_state.get("account_balance", 0),
        "ftmo_daily_pnl":          round(ftmo_state.get("daily_pnl", 0.0), 2),
        "ftmo_daily_signals":      ftmo_state.get("daily_signals", 0),
        "ftmo_daily_wins":         ftmo_wins,
        "ftmo_daily_losses":       ftmo_losses,
        "ftmo_win_rate":           ftmo_wr,
        "ftmo_consecutive_losses": ftmo_state.get("consecutive_losses", 0),
        "ftmo_trades":             ftmo_state.get("trades", [])[-5:],
    }), 200


# ── Full Trade History Endpoint ────────────────────────────
@app.route("/trades", methods=["GET"])
def trades():
    return jsonify({
        "tpt":  tpt_state.get("trades", []),
        "ftmo": ftmo_state.get("trades", []),
    }), 200


# ── Health check ───────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": ct_now().isoformat()}), 200
