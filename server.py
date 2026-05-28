from flask import Flask, request, jsonify, send_file, Response
from datetime import datetime
import os
import json
import pytz

from config import PAPER_WEBHOOK_TOKEN
from risk import (
    check_paper_risk,
    paper_state,
    record_paper_signal,
    record_paper_trade,
    update_paper_outcome,
    reset_paper_full,
)
from notifier import send_telegram
from logger import init_db
import oanda

app = Flask(__name__)
CT = pytz.timezone("America/Chicago")

init_db()

def ct_now():
    return datetime.now(CT)


# ── Paper Trading Webhook ──────────────────────────────────
@app.route("/webhook/paper", methods=["POST"])
def webhook_paper():
    data = request.json or {}

    if data.get("token") != PAPER_WEBHOOK_TOKEN:
        return jsonify({"error": "unauthorized"}), 401

    event = data.get("event", "").lower()
    if event in ("tp1_hit", "tp2_hit", "sl_hit"):
        return handle_paper_lifecycle(data, event)

    return handle_paper_signal(data)


_OANDA_MAP = {
    "NAS100USD": "US100",
    "US30USD":   "US30",
    "SPX500USD": "US500",
    "WTICOUSD":  "USOIL",
    "XAGUSD":    "XAGUSD",
    "XAUUSD":    "XAUUSD",
}

def _normalize_instrument(raw: str) -> str:
    s = raw.upper().replace("1!", "").replace("!", "")
    if ":" in s:                          # strip broker prefix e.g. "OANDA:"
        s = s.split(":")[-1]
    return _OANDA_MAP.get(s, s)


def handle_paper_signal(data):
    instrument = _normalize_instrument(data.get("symbol", data.get("instrument", "")))
    direction  = data.get("direction", "").upper()
    price      = data.get("entry_price", data.get("price"))
    sl         = data.get("stop_loss", data.get("sl"))
    tp1        = data.get("tp1")
    tp2        = data.get("tp2")
    rr         = data.get("rr_to_tp1")
    sl_pips    = data.get("sl_pips")

    open_count = sum(1 for t in _load_trades_log().get("paper", []) if t.get("result") == "OPEN")
    if open_count >= 5:
        reason = f"max open trades reached ({open_count}/5)"
        send_telegram(f"🚫 *Paper Signal Blocked — Max Open Trades*\n`{instrument} {direction}`\n{reason}")
        return jsonify({"status": "blocked", "reason": reason}), 200

    risk = check_paper_risk(instrument, sl_pips)
    if not risk["allowed"]:
        msg = f"🚫 *Paper Signal Blocked*\n`{instrument} {direction}`\nReason: {risk['reason']}"
        send_telegram(msg)
        return jsonify({"status": "blocked", "reason": risk["reason"]}), 200

    # ── Execute on OANDA ──────────────────────────────────────
    oanda_trade_id = None
    oanda_fill_price = price
    oanda_error = None
    try:
        fill = oanda.place_order(instrument, direction, risk["lot_size"])
        oanda_trade_id   = fill["trade_id"]
        oanda_fill_price = fill["price"]
    except Exception as e:
        oanda_error = str(e)
        print(f"[oanda] ERROR placing order: {e}", flush=True)

    emoji   = "🟢" if direction == "LONG" else "🔴"
    rr_line = f"\nR:R: `{rr}`" if rr else ""
    exec_line = f"\nOANDA ID: `{oanda_trade_id}` @ `{oanda_fill_price}`" if oanda_trade_id else f"\nOANDA: FAILED ({oanda_error})"
    msg = (
        f"{emoji} *Paper — {instrument} {direction}*\n"
        f"Entry: `{price}`\n"
        f"SL: `{sl}`\n"
        f"TP1: `{tp1}`\n"
        f"TP2: `{tp2}`\n"
        f"Lots: `{risk['lot_size']}`\n"
        f"Risk: `${risk['risk_dollars']}`"
        f"{rr_line}"
        f"{exec_line}"
    )

    record_paper_signal(data)
    _append_trade_log({
        "time":            ct_now().strftime("%H:%M"),
        "date":            ct_now().strftime("%Y-%m-%d %H:%M"),
        "instrument":      instrument,
        "direction":       direction,
        "price":           oanda_fill_price,
        "sl":              sl,
        "tp1":             tp1,
        "tp2":             tp2,
        "lot_size":        risk["lot_size"],
        "result":          "OPEN",
        "pnl":             0,
        "oanda_trade_id":  oanda_trade_id,
    })
    send_telegram(msg)
    return jsonify({"status": "approved", "lot_size": risk["lot_size"], "oanda_trade_id": oanda_trade_id}), 200


def handle_paper_lifecycle(data, event):
    instrument = _normalize_instrument(data.get("symbol", data.get("instrument", "")))
    direction  = data.get("direction", "").upper()
    price      = data.get("price", data.get("entry_price"))
    pnl        = data.get("pnl")
    lot_size   = data.get("lot_size")

    # ── Close on OANDA ────────────────────────────────────────
    oanda_trade_id = _get_oanda_trade_id(instrument, direction)
    if oanda_trade_id:
        try:
            oanda.close_trade(oanda_trade_id)
            print(f"[oanda] Closed trade {oanda_trade_id} ({event})", flush=True)
        except Exception as e:
            print(f"[oanda] ERROR closing trade {oanda_trade_id}: {e}", flush=True)

    won = event in ("tp1_hit", "tp2_hit")
    update_paper_outcome(won=won, pnl=pnl)

    result_map = {"tp1_hit": "TP1", "tp2_hit": "TP2", "sl_hit": "SL"}
    record_paper_trade({
        "time":       ct_now().strftime("%H:%M"),
        "date":       ct_now().strftime("%Y-%m-%d %H:%M"),
        "instrument": instrument,
        "direction":  direction,
        "result":     result_map.get(event, event.upper()),
        "pnl":        pnl or 0,
        "lot_size":   lot_size,
    })
    _update_open_trade(instrument, direction, event, pnl)

    emoji_map = {"tp1_hit": "✅", "tp2_hit": "🏆", "sl_hit": "❌"}
    label_map = {"tp1_hit": "TP1 Hit", "tp2_hit": "TP2 Hit", "sl_hit": "Stop Loss Hit"}
    dir_emoji = "🟢" if direction == "LONG" else "🔴"
    pnl_line  = f"\nP&L: `${pnl:+.2f}`" if pnl is not None else ""

    msg = (
        f"{emoji_map.get(event, '📊')} *Paper {label_map.get(event, event.upper())}*\n"
        f"{dir_emoji} `{instrument} {direction}`\n"
        f"Price: `{price}`{pnl_line}"
    )
    send_telegram(msg)
    return jsonify({"status": "logged", "event": event, "symbol": instrument}), 200


# ── Trade log helpers ──────────────────────────────────────
TRADES_FILE = os.path.join(os.path.dirname(__file__), "trades.json")

def _load_trades_log():
    if os.path.exists(TRADES_FILE):
        try:
            with open(TRADES_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {"paper": []}

def _append_trade_log(trade: dict):
    log = _load_trades_log()
    log.setdefault("paper", []).append(trade)
    try:
        with open(TRADES_FILE, "w") as f:
            json.dump(log, f, indent=2)
    except Exception as e:
        print(f"[trades] WARNING: could not write trades.json: {e}", flush=True)

def _get_oanda_trade_id(instrument: str, direction: str):
    try:
        trades = _load_trades_log().get("paper", [])
        for t in reversed(trades):
            if (t.get("instrument", "").upper() == instrument.upper() and
                    t.get("direction", "").upper() == direction.upper() and
                    t.get("result") == "OPEN"):
                return t.get("oanda_trade_id")
    except Exception:
        pass
    return None

def _update_open_trade(instrument: str, direction: str, event: str, pnl):
    try:
        log = _load_trades_log()
        trades = log.get("paper", [])
        result_map = {"tp1_hit": "TP1", "tp2_hit": "TP2", "sl_hit": "SL"}
        result = result_map.get(event, event.upper())
        for t in reversed(trades):
            if (t.get("instrument", "").upper() == instrument.upper() and
                    t.get("direction", "").upper() == direction.upper() and
                    t.get("result") == "OPEN"):
                t["result"] = result
                t["pnl"]    = pnl or 0
                break
        with open(TRADES_FILE, "w") as f:
            json.dump(log, f, indent=2)
    except Exception as e:
        print(f"[trades] WARNING: could not update open trade: {e}", flush=True)


# ── State Endpoint ─────────────────────────────────────────
@app.route("/state", methods=["GET"])
def state():
    now = ct_now()
    wins   = paper_state.get("daily_wins", 0)
    losses = paper_state.get("daily_losses", 0)
    total  = wins + losses
    wr     = round((wins / total) * 100) if total > 0 else 0

    open_count = sum(1 for t in _load_trades_log().get("paper", []) if t.get("result") == "OPEN")

    return jsonify({
        "time_ct":              now.strftime("%Y-%m-%d %H:%M:%S"),
        "paper_account_balance": paper_state.get("account_balance", 0),
        "paper_daily_pnl":       round(paper_state.get("daily_pnl", 0.0), 2),
        "paper_daily_signals":   paper_state.get("daily_signals", 0),
        "paper_daily_wins":      wins,
        "paper_daily_losses":    losses,
        "paper_win_rate":        wr,
        "paper_open_trades":     open_count,
        "paper_last_signal":     paper_state.get("last_signal"),
        "paper_trades":          paper_state.get("trades", [])[-10:],
    }), 200


# ── Trades Endpoint ────────────────────────────────────────
@app.route("/trades", methods=["GET"])
def trades():
    log = _load_trades_log()
    return jsonify({"paper": log.get("paper", [])}), 200


# ── News Endpoint — Yahoo Finance RSS proxy ────────────────
import urllib.request
import xml.etree.ElementTree as ET
import time as _time

_news_cache = {"data": [], "ts": 0}
NEWS_TTL = 900

YAHOO_FEEDS = [
    ("Markets",   "https://finance.yahoo.com/rss/topstories"),
    ("Gold/Comm",  "https://finance.yahoo.com/rss/industry?industry=gold"),
    ("US Indices", "https://finance.yahoo.com/rss/industry?industry=financial"),
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

    seen, unique = set(), []
    for a in articles:
        if a["title"] not in seen:
            seen.add(a["title"])
            unique.append(a)
        if len(unique) >= 6:
            break

    _news_cache = {"data": unique, "ts": now}
    return jsonify({"articles": unique, "cached": False}), 200


# ── Dashboard ──────────────────────────────────────────────
@app.route("/dashboard", methods=["GET"])
def dashboard():
    dash_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if os.path.exists(dash_path):
        return send_file(dash_path, mimetype="text/html")
    return Response("<h1>dashboard.html not found</h1>", mimetype="text/html"), 404


# ── Admin Reset ────────────────────────────────────────────
@app.route("/admin/reset", methods=["POST"])
def admin_reset():
    token = request.json.get("token") if request.is_json else None
    if token != PAPER_WEBHOOK_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    reset_paper_full()
    return jsonify({"status": "reset", "balance": paper_state["account_balance"]}), 200


# ── Health ─────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "mode": "paper-trading", "time": ct_now().isoformat()}), 200
