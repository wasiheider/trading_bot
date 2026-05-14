from flask import Flask, request, jsonify
from datetime import datetime
import pytz

from config import TPT_WEBHOOK_TOKEN, FTMO_WEBHOOK_TOKEN
from risk import check_tpt_risk, check_ftmo_risk, tpt_state, update_ftmo_outcome
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
    if now.hour > 15 or (now.hour == 15 and now.minute >= 55):
        return True
    return False


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
    sl_ticks   = data.get("sl_ticks")

    if is_tpt_killed():
        msg = f"🚫 *TPT Signal Blocked*\n`{instrument} {direction}` — kill switch active or past 3:55 PM CT"
        send_telegram(msg)
        return jsonify({"status": "blocked", "reason": "kill switch"}), 200

    risk = check_tpt_risk(instrument, sl_ticks)
    if not risk["allowed"]:
        msg = f"🚫 *TPT Signal Blocked*\n`{instrument} {direction}`\nReason: {risk['reason']}"
        send_telegram(msg)
        return jsonify({"status": "blocked", "reason": risk["reason"]}), 200

    emoji = "🟢" if direction == "LONG" else "🔴"
    msg = (
        f"{emoji} *TPT Signal — {instrument} {direction}*\n"
        f"Price: `{price}`\n"
        f"SL: `{sl}`\n"
        f"TP1: `{tp1}`\n"
        f"TP2: `{tp2}`\n"
        f"Contracts: `{risk['contracts']}`\n"
        f"Risk: `${risk['risk_dollars']}`"
    )
    send_telegram(msg)

    return jsonify({"status": "approved", "contracts": risk["contracts"]}), 200


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


# ── Health check ───────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": ct_now().isoformat()}), 200
