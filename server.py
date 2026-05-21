from flask import Flask, request, jsonify, send_file, Response
from datetime import datetime
import os
import json
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

init_db()

def ct_now():
    return datetime.now(CT)

def is_maintenance_window():
    now = ct_now()
    return (now.hour == 15 and now.minute >= 55) or now.hour == 16

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


# ── TPT Webhook ────────────────────────────────────────────
@app.route("/webhook/tpt", methods=["POST"])
def webhook_tpt():
    data = request.json or {}

    if data.get("token") != TPT_WEBHOOK_TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    # ── Route lifecycle events (tp1_hit, tp2_hit, sl_hit, range_break) ──────
    event = data.get("event", "").lower()
    if event in ("tp1_hit", "tp2_hit", "sl_hit"):
        return handle_tpt_lifecycle(data, event)
    if event == "range_break":
        return handle_tpt_range_break(data)

    instrument = data.get("instrument", data.get("symbol", "")).upper().replace("1!", "").replace("!", "")
    direction  = data.get("direction", "").upper()
    price      = data.get("price", data.get("entry_price"))
    sl         = data.get("sl", data.get("stop_loss"))
    tp1        = data.get("tp1")
    tp2        = data.get("tp2")
    rr         = data.get("rr_to_tp1")
    contracts  = max(2, min(5, int(data.get("contracts", 2))))

    if is_tpt_killed():
        reason = "maintenance window (3:55–5:00 PM CT)" if is_maintenance_window() else "kill switch active"
        msg = f"🚫 *TPT Signal Blocked*\n`{instrument} {direction}` — {reason}"
        send_telegram(msg)
        return jsonify({"status": "blocked", "reason": reason}), 200

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
    # Log signal entry to trades.json (result/pnl filled in on close)
    _append_trade_log("tpt", {
        "time":       ct_now().strftime("%H:%M"),
        "date":       ct_now().strftime("%Y-%m-%d %H:%M"),
        "instrument": instrument,
        "direction":  direction,
        "price":      price,
        "sl":         sl,
        "tp1":        tp1,
        "tp2":        tp2,
        "contracts":  contracts,
        "result":     "OPEN",
        "pnl":        0,
    })
    send_telegram(msg)
    return jsonify({"status": "approved", "contracts": contracts}), 200


# ── TPT Lifecycle Handler (tp1_hit, tp2_hit, sl_hit from Pine Script) ──────────
def handle_tpt_lifecycle(data, event):
    from risk import record_tpt_result, record_tpt_trade
    instrument = data.get("symbol", "").upper().replace("1!", "").replace("!", "")
    direction  = data.get("direction", "").upper()
    price      = data.get("price")
    pnl        = data.get("pnl")
    contracts  = data.get("contracts")

    won = event in ("tp1_hit", "tp2_hit")
    record_tpt_result(won=won)
    record_tpt_trade({
        "time":       ct_now().strftime("%H:%M"),
        "date":       ct_now().strftime("%Y-%m-%d %H:%M"),
        "instrument": instrument,
        "direction":  direction,
        "result":     "TP1" if event == "tp1_hit" else "TP2" if event == "tp2_hit" else "SL",
        "pnl":        pnl or 0,
        "contracts":  contracts,
    })
    # Update OPEN trade in trades.json to closed result
    _update_open_tpt_trade(instrument, direction, event, pnl)

    emoji_map = {"tp1_hit": "✅", "tp2_hit": "🏆", "sl_hit": "❌"}
    label_map = {"tp1_hit": "TP1 Hit", "tp2_hit": "TP2 Hit", "sl_hit": "Stop Loss Hit"}
    dir_emoji = "🟢" if direction == "LONG" else "🔴"
    pnl_line  = f"\nP&L: `${pnl:+.2f}`" if pnl is not None else ""
    msg = (f"{emoji_map.get(event, '📊')} *TPT {label_map.get(event, event.upper())}*\n"
           f"{dir_emoji} `{instrument} {direction}`\n"
           f"Price: `{price}`{pnl_line}")
    send_telegram(msg)
    return jsonify({"status": "logged", "event": event, "symbol": instrument}), 200


def handle_tpt_range_break(data):
    symbol    = data.get("symbol", "")
    direction = data.get("direction", "")
    price     = data.get("price")
    rh        = data.get("range_high")
    rl        = data.get("range_low")
    msg = (f"⚠️ *TPT Range Break — {symbol}*\n"
           f"Direction: `{direction}`\n"
           f"Price: `{price}`\n"
           f"Old range: `{rl}` – `{rh}`\n"
           f"Update range levels in Pine Script.")
    send_telegram(msg)
    return jsonify({"status": "logged", "event": "range_break", "symbol": symbol}), 200


def _update_open_tpt_trade(instrument: str, direction: str, event: str, pnl):
    """Find the most recent OPEN TPT trade for this instrument/direction and close it."""
    try:
        log_data = _load_trades_log()
        trades = log_data.get("tpt", [])
        result_map = {"tp1_hit": "TP1", "tp2_hit": "TP2", "sl_hit": "SL"}
        result = result_map.get(event, event.upper())
        # Walk backwards to find the most recent OPEN match
        for t in reversed(trades):
            if (t.get("instrument", "").upper() == instrument.upper() and
                    t.get("direction", "").upper() == direction.upper() and
                    t.get("result") == "OPEN"):
                t["result"] = result
                t["pnl"]    = pnl or 0
                break
        with open(TRADES_FILE, "w") as f:
            import json as _json
            _json.dump(log_data, f, indent=2)
    except Exception as e:
        print(f"[trades] WARNING: could not update open trade: {e}", flush=True)


# ── FTMO Webhook ───────────────────────────────────────────
@app.route("/webhook/ftmo", methods=["POST"])
def webhook_ftmo():
    data = request.json or {}
    if data.get("token") != FTMO_WEBHOOK_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    event = data.get("event", "").lower()
    if event in ("entry_filled", "tp1_hit", "tp2_hit", "sl_hit"):
        return handle_ftmo_lifecycle(data, event)
    return handle_ftmo_signal(data)


def handle_ftmo_signal(data):
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
    instrument = data.get("instrument", data.get("symbol", "")).upper().replace("1!", "").replace("!", "")
    direction  = data.get("direction", "").upper()
    price      = data.get("price", data.get("entry_price"))
    pnl        = data.get("pnl")
    lot_size   = data.get("lot_size")
    sl         = data.get("stop_loss", data.get("sl"))
    tp1        = data.get("tp1")
    tp2        = data.get("tp2")

    log_ftmo_lifecycle(event=event, symbol=instrument, direction=direction,
                       price=price, pnl=pnl, lot_size=lot_size, sl=sl, tp1=tp1, tp2=tp2)

    if event == "sl_hit":
        update_ftmo_outcome(won=False, pnl=pnl)
        _append_trade_log("ftmo", {
            "time":       ct_now().strftime("%H:%M"),
            "date":       ct_now().strftime("%Y-%m-%d %H:%M"),
            "instrument": instrument,
            "direction":  direction,
            "result":     "SL",
            "pnl":        pnl or 0,
            "lot_size":   lot_size,
        })
    elif event in ("tp1_hit", "tp2_hit"):
        update_ftmo_outcome(won=True, pnl=pnl)
        _append_trade_log("ftmo", {
            "time":       ct_now().strftime("%H:%M"),
            "date":       ct_now().strftime("%Y-%m-%d %H:%M"),
            "instrument": instrument,
            "direction":  direction,
            "result":     "TP1" if event == "tp1_hit" else "TP2",
            "pnl":        pnl or 0,
            "lot_size":   lot_size,
        })

    emoji_map  = {"entry_filled":"📥","tp1_hit":"✅","tp2_hit":"🏆","sl_hit":"❌"}
    label_map  = {"entry_filled":"Entry Filled","tp1_hit":"TP1 Hit","tp2_hit":"TP2 Hit","sl_hit":"Stop Loss Hit"}
    emoji      = emoji_map.get(event, "📊")
    label      = label_map.get(event, event.upper())
    dir_emoji  = "🟢" if direction == "LONG" else "🔴"
    pnl_line   = f"\nP&L: `${pnl:+.2f}`" if pnl is not None else ""
    lots_line  = f"\nLots: `{lot_size}`" if lot_size else ""

    msg = (f"{emoji} *FTMO {label}*\n{dir_emoji} `{instrument} {direction}`\n"
           f"Price: `{price}`{lots_line}{pnl_line}")
    send_telegram(msg)
    return jsonify({"status": "logged", "event": event, "symbol": instrument}), 200


# ── Manual Reset ───────────────────────────────────────────
@app.route("/reset/tpt", methods=["POST"])
def reset_tpt():
    data = request.json or {}
    if data.get("token") != TPT_WEBHOOK_TOKEN:
        return jsonify({"status": "error", "reason": "invalid token"}), 403
    tpt_state["killed"] = False
    tpt_state["consecutive_losses"] = 0
    from risk import _save_state
    _save_state()
    send_telegram("🔄 *TPT Reset*\nKill switch cleared — signals active.")
    return jsonify({"status": "ok", "message": "TPT state reset"}), 200


# ── State Endpoint ─────────────────────────────────────────
@app.route("/state", methods=["GET"])
def state():
    now = ct_now()
    tpt_wins    = tpt_state.get("daily_wins", 0)
    tpt_losses  = tpt_state.get("daily_losses", 0)
    tpt_total   = tpt_wins + tpt_losses
    tpt_wr      = round((tpt_wins / tpt_total) * 100) if tpt_total > 0 else 0

    ftmo_wins   = ftmo_state.get("daily_wins", 0)
    ftmo_losses = ftmo_state.get("daily_losses", 0)
    ftmo_total  = ftmo_wins + ftmo_losses
    ftmo_wr     = round((ftmo_wins / ftmo_total) * 100) if ftmo_total > 0 else 0

    return jsonify({
        # Meta
        "time_ct":              now.strftime("%Y-%m-%d %H:%M:%S"),
        "is_weekend":           now.weekday() == 5 or (now.weekday() == 6 and now.hour < 17) or (now.weekday() == 4 and now.hour >= 16),
        "is_maintenance":       is_maintenance_window(),
        # TPT
        "tpt_killed":           tpt_state["killed"],
        "is_tpt_killed":        is_tpt_killed(),
        "tpt_consecutive_losses": tpt_state.get("consecutive_losses", 0),
        "tpt_account_balance":  tpt_state.get("account_balance", 0),
        "tpt_daily_pnl":        round(tpt_state.get("daily_pnl", 0.0), 2),
        "tpt_daily_signals":    tpt_state.get("daily_signals", 0),
        "tpt_daily_wins":       tpt_wins,
        "tpt_daily_losses":     tpt_losses,
        "tpt_win_rate":         tpt_wr,
        "tpt_last_signal":      tpt_state.get("last_signal"),
        "tpt_trades":           tpt_state.get("trades", [])[-5:],
        # FTMO
        "ftmo_account_balance": ftmo_state.get("account_balance", 0),
        "ftmo_daily_pnl":       round(ftmo_state.get("daily_pnl", 0.0), 2),
        "ftmo_daily_signals":   ftmo_state.get("daily_signals", 0),
        "ftmo_daily_wins":      ftmo_wins,
        "ftmo_daily_losses":    ftmo_losses,
        "ftmo_win_rate":        ftmo_wr,
        "ftmo_consecutive_losses":        ftmo_state.get("consecutive_losses", 0),
        "ftmo_weekly_consec_losses":      ftmo_state.get("weekly_consec_losses", 0),
        "ftmo_killed":                    ftmo_state.get("killed", False),
        "ftmo_daily_loss_limit":          5000,
        "ftmo_trades":          ftmo_state.get("trades", [])[-5:],
    }), 200


# ── Trades Endpoint ────────────────────────────────────────
TRADES_FILE = os.path.join(os.path.dirname(__file__), "trades.json")

def _load_trades_log():
    if os.path.exists(TRADES_FILE):
        try:
            with open(TRADES_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"tpt": [], "ftmo": []}

def _append_trade_log(account: str, trade: dict):
    """Append a trade to trades.json (persistent across restarts, unlimited history)."""
    log = _load_trades_log()
    log.setdefault(account, []).append(trade)
    try:
        with open(TRADES_FILE, "w") as f:
            json.dump(log, f, indent=2)
    except Exception as e:
        print(f"[trades] WARNING: could not write trades.json: {e}", flush=True)

@app.route("/trades", methods=["GET"])
def trades():
    log = _load_trades_log()
    return jsonify({
        "tpt":  log.get("tpt", []),
        "ftmo": log.get("ftmo", []),
    }), 200


# ── News Endpoint — Yahoo Finance RSS proxy ────────────────
import urllib.request
import xml.etree.ElementTree as ET
import time as _time

_news_cache = {"data": [], "ts": 0}
NEWS_TTL = 900  # 15 minutes

YAHOO_FEEDS = [
    ("Markets",      "https://finance.yahoo.com/rss/topstories"),
    ("Gold/Comm",    "https://finance.yahoo.com/rss/industry?industry=gold"),
    ("US Indices",   "https://finance.yahoo.com/rss/industry?industry=financial"),
]

def _fetch_rss(url: str, label: str, n: int = 2) -> list:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as r:
            tree = ET.parse(r)
        items = tree.findall(".//item")[:n]
        results = []
        for item in items:
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link")  or "").strip()
            pub   = (item.findtext("pubDate") or "").strip()
            if title:
                results.append({"title": title, "link": link, "pub": pub, "category": label})
        return results
    except Exception as e:
        print(f"[news] RSS fetch failed ({label}): {e}", flush=True)
        return []

@app.route("/news", methods=["GET"])
def news():
    global _news_cache
    now = _time.time()
    if now - _news_cache["ts"] < NEWS_TTL and _news_cache["data"]:
        return jsonify({"articles": _news_cache["data"], "cached": True}), 200

    articles = []
    for label, url in YAHOO_FEEDS:
        articles += _fetch_rss(url, label, n=2)

    # Deduplicate by title, keep max 6
    seen = set()
    unique = []
    for a in articles:
        if a["title"] not in seen:
            seen.add(a["title"])
            unique.append(a)
        if len(unique) >= 6:
            break

    _news_cache = {"data": unique, "ts": now}
    return jsonify({"articles": unique, "cached": False}), 200



@app.route("/dashboard", methods=["GET"])
def dashboard():
    dash_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if os.path.exists(dash_path):
        return send_file(dash_path, mimetype="text/html")
    return Response("<h1>dashboard.html not found</h1>", mimetype="text/html"), 404


# ── Manual Balance Update ──────────────────────────────────
@app.route("/admin/set_balance", methods=["POST"])
def set_balance():
    data = request.json or {}
    # Reuse FTMO token as admin token
    if data.get("token") not in (TPT_WEBHOOK_TOKEN, FTMO_WEBHOOK_TOKEN):
        return jsonify({"status": "unauthorized"}), 401
    changed = []
    if "tpt_balance" in data:
        tpt_state["account_balance"] = float(data["tpt_balance"])
        changed.append(f"TPT → ${data['tpt_balance']:,.2f}")
    if "ftmo_balance" in data:
        ftmo_state["account_balance"] = float(data["ftmo_balance"])
        changed.append(f"FTMO → ${data['ftmo_balance']:,.2f}")
    if not changed:
        return jsonify({"status": "error", "reason": "no balance fields provided"}), 400
    from risk import _save_state
    _save_state()
    summary = " | ".join(changed)
    send_telegram(f"💰 *Manual Balance Update*\n{summary}")
    return jsonify({
        "status": "updated",
        "tpt_balance":  tpt_state.get("account_balance"),
        "ftmo_balance": ftmo_state.get("account_balance"),
    }), 200


# ── Health ─────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "time": ct_now().isoformat()}), 200
