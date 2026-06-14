from flask import Flask, request, jsonify, send_file, Response
from datetime import datetime
import os
import json
import pytz

from config import PAPER_WEBHOOK_TOKEN, DATA_DIR
from risk import (
    check_paper_risk,
    paper_state,
    record_paper_signal,
    record_paper_trade,
    update_paper_outcome,
    reset_paper_full,
    is_daily_limit_hit,
    is_weekly_limit_hit,
)
from notifier import send_telegram
from logger import init_db
import oanda

# Mascot header for all Telegram notifications (daughter's crowned robot design)
_MASCOT = "🩷👑🤖👑🩷"

# Reverse lookup: OANDA instrument name → bot instrument name
_OANDA_REVERSE_MAP = {v: k for k, v in oanda.INSTRUMENT_MAP.items()}

def _patch_stuck_trades():
    """One-time: fix non-forex OPEN trades whose lifecycle event fired before the fix was deployed."""
    patches = [
        # US100 LONG — SL hit 2026-06-04 03:15 CT, PNL -128.54 (Telegram confirmed)
        {"instrument": "US100", "direction": "LONG", "date_prefix": "2026-06-04", "result": "SL", "pnl": -128.54},
    ]
    try:
        log = _load_trades_log()
        modified = False
        for p in patches:
            for t in log.get("paper", []):
                if (t.get("instrument", "").upper() == p["instrument"] and
                        t.get("direction", "").upper()  == p["direction"] and
                        t.get("result") == "OPEN" and
                        (t.get("date", "") or "").startswith(p["date_prefix"])):
                    t["result"] = p["result"]
                    t["pnl"]    = p["pnl"]
                    modified = True
                    print(f"[startup] Patched stuck {p['instrument']} {p['direction']} → {p['result']} ${p['pnl']}", flush=True)
        if modified:
            with open(TRADES_FILE, "w") as f:
                json.dump(log, f, indent=2)
    except Exception as e:
        print(f"[startup] Stuck trade patch failed: {e}", flush=True)


def _sync_oanda_on_startup():
    """On every startup, add any OANDA open positions missing from trades.json."""
    _patch_stuck_trades()
    try:
        open_trades = oanda.get_all_open_trades()
        for t in open_trades:
            oanda_instrument = t.get("instrument", "")
            bot_instrument   = _OANDA_REVERSE_MAP.get(oanda_instrument)
            if not bot_instrument:
                continue
            units     = float(t.get("currentUnits", 0))
            direction = "LONG" if units > 0 else "SHORT"
            trade_id  = t.get("id")
            # Skip if already tracked as OPEN in trades.json
            if _get_oanda_trade_id(bot_instrument, direction):
                continue
            open_time = t.get("openTime", "")[:16].replace("T", " ")
            _append_trade_log({
                "time":           open_time[11:16] if len(open_time) >= 16 else "--",
                "date":           open_time,
                "instrument":     bot_instrument,
                "direction":      direction,
                "price":          float(t.get("price", 0)),
                "sl":             None,
                "tp1":            None,
                "tp2":            None,
                "lot_size":       round(abs(units) / 100000, 2),
                "result":         "OPEN",
                "pnl":            0,
                "oanda_trade_id": trade_id,
            })
            print(f"[startup] Synced OANDA trade {trade_id} ({bot_instrument} {direction}) → trades.json", flush=True)
    except Exception as e:
        print(f"[startup] OANDA sync failed: {e}", flush=True)


app = Flask(__name__)
CT = pytz.timezone("America/Chicago")

init_db()

_startup_done = False

@app.before_request
def _on_first_request():
    global _startup_done
    if not _startup_done:
        _startup_done = True
        _sync_oanda_on_startup()

def ct_now():
    return datetime.now(CT)


# ── Paper Trading Webhook ──────────────────────────────────
@app.route("/webhook/paper", methods=["POST"])
def webhook_paper():
    data = request.get_json(force=True, silent=True) or {}

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


def _calc_sl_pips(instrument: str, entry_price, sl_price):
    from risk import PAPER_INSTRUMENT_CONFIG
    cfg = PAPER_INSTRUMENT_CONFIG.get(instrument.upper())
    if not cfg or entry_price is None or sl_price is None:
        return None
    try:
        return round(abs(float(entry_price) - float(sl_price)) / cfg["pip_size"], 1)
    except Exception:
        return None


def handle_paper_signal(data):
    instrument  = _normalize_instrument(data.get("symbol", data.get("instrument", "")))
    direction   = data.get("direction", "").upper()
    setup       = data.get("setup", "").lower()    # "spring" | "upthrust" | "" (BOS)
    price       = data.get("entry_price", data.get("price"))
    sl          = data.get("stop_loss", data.get("sl"))
    tp1         = data.get("tp1")
    tp2         = data.get("tp2")
    rr          = data.get("rr_to_tp1")
    timeframe   = data.get("timeframe", "?")       # "15" for v4, "5" for v3
    bos_level   = data.get("bos_level")
    range_high  = data.get("range_high")
    range_low   = data.get("range_low")
    sl_pips     = _calc_sl_pips(instrument, price, sl) or data.get("sl_pips")

    risk = check_paper_risk(instrument, sl_pips)
    if not risk["allowed"]:
        msg = f"{_MASCOT}\n🚫 *Paper Signal Blocked*\n`{instrument} {direction}`\nReason: {risk['reason']}"
        send_telegram(msg)
        return jsonify({"status": "blocked", "reason": risk["reason"]}), 200

    # ── Daily / weekly limit check (signal fires, OANDA skipped) ─
    daily_hit,  daily_reason  = is_daily_limit_hit()
    weekly_hit, weekly_reason = is_weekly_limit_hit()
    limit_hit    = daily_hit or weekly_hit
    limit_reason = daily_reason or weekly_reason

    # ── Execute on OANDA (forex pairs only) ───────────────────
    oanda_trade_id   = None
    oanda_fill_price = price
    oanda_error      = None
    oanda_supported  = instrument.upper() in oanda.INSTRUMENT_MAP
    if not limit_hit and oanda_supported:
        try:
            fill = oanda.place_order(instrument, direction, risk["lot_size"])
            oanda_trade_id   = fill["trade_id"]
            oanda_fill_price = fill["price"]
        except Exception as e:
            oanda_error = str(e)
            print(f"[oanda] ERROR placing order: {e}", flush=True)
            send_telegram(f"{_MASCOT}\n🔴 *OANDA ORDER FAILED*\n`{instrument} {direction}`\nError: `{oanda_error}`")

    emoji    = "🟢" if direction == "LONG" else "🔴"
    rr_line  = f"\nR:R: `{rr}`" if rr else ""
    bos_line = f"\nBOS: `{bos_level}`" if bos_level else ""
    tf_label = f"15M" if str(timeframe) == "15" else f"{timeframe}M"
    setup_label = {"spring": " · SPRING", "upthrust": " · UPTHRUST", "bos_div": " · BOS+DIV"}.get(setup, "")
    if limit_hit:
        exec_line = f"\n⚠️ *NOT EXECUTED — {limit_reason}*"
    elif not oanda_supported:
        exec_line = "\n📋 Log + Telegram only (not a forex pair)"
    elif oanda_trade_id:
        exec_line = f"\nOANDA ID: `{oanda_trade_id}` @ `{oanda_fill_price}`"
    else:
        exec_line = f"\nOANDA: FAILED ({oanda_error})"

    msg = (
        f"{_MASCOT}\n"
        f"{emoji} *Paper — {instrument} {direction}* [{tf_label}{setup_label}]\n"
        f"Entry: `{price}`\n"
        f"SL: `{sl}`\n"
        f"TP1: `{tp1}`\n"
        f"TP2: `{tp2}`\n"
        f"Lots: `{risk['lot_size']}`\n"
        f"Risk: `${risk['risk_dollars']}`"
        f"{rr_line}"
        f"{bos_line}"
        f"{exec_line}"
    )

    record_paper_signal(data)
    if not limit_hit:
        _append_trade_log({
            "time":           ct_now().strftime("%H:%M"),
            "date":           ct_now().strftime("%Y-%m-%d %H:%M"),
            "instrument":     instrument,
            "direction":      direction,
            "timeframe":      timeframe,
            "price":          oanda_fill_price,
            "sl":             sl,
            "tp1":            tp1,
            "tp2":            tp2,
            "bos_level":      bos_level,
            "range_high":     range_high,
            "range_low":      range_low,
            "lot_size":       risk["lot_size"],
            "setup":          setup,
            "result":         "OPEN",
            "pnl":            0,
            "oanda_trade_id": oanda_trade_id,
        })
    send_telegram(msg)
    return jsonify({"status": "approved", "lot_size": risk["lot_size"], "oanda_trade_id": oanda_trade_id, "limit_hit": limit_hit}), 200


def handle_paper_lifecycle(data, event):
    instrument = _normalize_instrument(data.get("symbol", data.get("instrument", "")))
    direction  = data.get("direction", "").upper()
    price      = data.get("price", data.get("entry_price"))
    pine_pnl   = data.get("pnl")  # Pine Script PNL — fallback only
    lot_size   = data.get("lot_size")

    # ── Find OANDA trade ID — local trades.json first, then query OANDA ──
    oanda_trade_id = _get_oanda_trade_id(instrument, direction)
    if not oanda_trade_id and instrument.upper() in oanda.INSTRUMENT_MAP:
        try:
            open_trade = oanda.get_open_trade(instrument, direction)
            if open_trade:
                oanda_trade_id = open_trade["id"]
                print(f"[oanda] Recovered trade ID {oanda_trade_id} from OANDA API", flush=True)
        except Exception as e:
            print(f"[oanda] Could not query open trades: {e}", flush=True)

    # ── Close on OANDA and capture actual realized PNL ────────
    oanda_pnl = None
    if oanda_trade_id:
        try:
            close_resp = oanda.close_trade(oanda_trade_id)
            oanda_pnl  = close_resp.get("realizedPL")
            print(f"[oanda] Closed trade {oanda_trade_id} ({event}), PNL: ${oanda_pnl:.2f}", flush=True)
        except Exception as e:
            print(f"[oanda] ERROR closing trade {oanda_trade_id}: {e}", flush=True)

    # OANDA realizedPL is authoritative; Pine Script PNL is the fallback
    final_pnl = oanda_pnl if oanda_pnl is not None else pine_pnl

    # ── Update stats + trades.json (forex and log-only) ──────
    locally_tracked = bool(oanda_trade_id) or _has_local_open_trade(instrument, direction)
    if locally_tracked:
        won = event in ("tp1_hit", "tp2_hit")
        update_paper_outcome(won=won, pnl=final_pnl)
        result_map = {"tp1_hit": "TP1", "tp2_hit": "TP2", "sl_hit": "SL"}
        record_paper_trade({
            "time":       ct_now().strftime("%H:%M"),
            "date":       ct_now().strftime("%Y-%m-%d %H:%M"),
            "instrument": instrument,
            "direction":  direction,
            "result":     result_map.get(event, event.upper()),
            "pnl":        final_pnl or 0,
            "lot_size":   lot_size,
        })
        _update_open_trade(instrument, direction, event, final_pnl)

    emoji_map = {"tp1_hit": "✅", "tp2_hit": "🏆", "sl_hit": "❌"}
    label_map = {"tp1_hit": "TP1 Hit", "tp2_hit": "TP2 Hit", "sl_hit": "Stop Loss Hit"}
    dir_emoji = "🟢" if direction == "LONG" else "🔴"
    pnl_line  = f"\nP&L: `${final_pnl:+.2f}`" if final_pnl is not None else ""

    msg = (
        f"{_MASCOT}\n"
        f"{emoji_map.get(event, '📊')} *Paper {label_map.get(event, event.upper())}*\n"
        f"{dir_emoji} `{instrument} {direction}`\n"
        f"Price: `{price}`{pnl_line}"
    )
    send_telegram(msg)
    return jsonify({"status": "logged", "event": event, "symbol": instrument}), 200


# ── Trade log helpers ──────────────────────────────────────
TRADES_FILE = os.path.join(DATA_DIR, "trades.json")

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

def _has_local_open_trade(instrument: str, direction: str) -> bool:
    """Return True if trades.json has an OPEN entry for this instrument/direction."""
    try:
        for t in reversed(_load_trades_log().get("paper", [])):
            if (t.get("instrument", "").upper() == instrument.upper() and
                    t.get("direction", "").upper() == direction.upper() and
                    t.get("result") == "OPEN"):
                return True
    except Exception:
        pass
    return False

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

    total_wins   = paper_state.get("total_wins", 0)
    total_losses = paper_state.get("total_losses", 0)
    total_trades = total_wins + total_losses
    total_wr     = round((total_wins / total_trades) * 100) if total_trades > 0 else 0

    # ── Live balance + unrealized PNL from OANDA (authoritative) ─
    oanda_balance      = paper_state.get("account_balance", 100000)
    unrealized_pnl     = 0.0
    open_trade_details = []

    try:
        acct_summary  = oanda.get_account_summary()
        oanda_balance = acct_summary["balance"]
    except Exception as e:
        print(f"[state] OANDA account summary failed: {e}", flush=True)

    try:
        for t in oanda.get_all_open_trades():
            upnl      = float(t.get("unrealizedPL", 0) or 0)
            units     = float(t.get("currentUnits", 0))
            direction = "LONG" if units > 0 else "SHORT"
            instr     = _OANDA_REVERSE_MAP.get(t.get("instrument", ""), t.get("instrument", ""))
            unrealized_pnl += upnl
            open_trade_details.append({
                "trade_id":       t.get("id"),
                "instrument":     instr,
                "direction":      direction,
                "unrealized_pnl": round(upnl, 2),
            })
    except Exception as e:
        print(f"[state] unrealized PNL fetch failed: {e}", flush=True)

    # ── Per-model breakdown from trades.json ─────────────────
    def _setup_to_model(s):
        s = (s or "").lower()
        if s in ("bos", ""):           return "A"
        if s in ("spring", "upthrust"): return "B"
        if s == "bos_div":             return "C"
        return "A"  # fallback for legacy trades with no setup field

    model_stats = {"A": {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0},
                   "B": {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0},
                   "C": {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0}}
    for t in _load_trades_log().get("paper", []):
        res = t.get("result", "OPEN")
        if res == "OPEN":
            continue
        m = _setup_to_model(t.get("setup", ""))
        model_stats[m]["trades"] += 1
        if res in ("TP1", "TP2"):
            model_stats[m]["wins"] += 1
        else:
            model_stats[m]["losses"] += 1
        model_stats[m]["pnl"] = round(model_stats[m]["pnl"] + (t.get("pnl") or 0), 2)
    for m in model_stats:
        n = model_stats[m]["trades"]
        model_stats[m]["win_rate"] = round(model_stats[m]["wins"] / n * 100) if n else 0

    return jsonify({
        "time_ct":                now.strftime("%Y-%m-%d %H:%M:%S"),
        "paper_account_balance":  round(oanda_balance, 2),
        "paper_daily_pnl":        round(paper_state.get("daily_pnl", 0.0), 2),
        "paper_weekly_pnl":       round(paper_state.get("weekly_pnl", 0.0), 2),
        "paper_unrealized_pnl":   round(unrealized_pnl, 2),
        "paper_open_details":     open_trade_details,
        "paper_daily_signals":    paper_state.get("daily_signals", 0),
        "paper_daily_wins":       wins,
        "paper_daily_losses":     losses,
        "paper_win_rate":         wr,
        "paper_total_wins":       total_wins,
        "paper_total_losses":     total_losses,
        "paper_total_win_rate":   total_wr,
        "paper_open_trades":      open_count,
        "paper_last_signal":      paper_state.get("last_signal"),
        "paper_trades":           paper_state.get("trades", [])[-10:],
        "model_stats":            model_stats,
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


# ── Calendar Endpoint — ForexFactory proxy ────────────────
_cal_cache = {"data": [], "ts": 0}
CAL_TTL = 3600  # 1 hour

@app.route("/calendar", methods=["GET"])
def calendar():
    global _cal_cache
    now = _time.time()
    if now - _cal_cache["ts"] < CAL_TTL and _cal_cache["data"]:
        return jsonify(_cal_cache["data"]), 200
    try:
        req = urllib.request.Request(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        _cal_cache = {"data": data, "ts": now}
        return jsonify(data), 200
    except Exception as e:
        print(f"[calendar] fetch failed: {e}", flush=True)
        if _cal_cache["data"]:
            return jsonify(_cal_cache["data"]), 200
        return jsonify([]), 200


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
    return jsonify({"status": "ok", "mode": "paper-trading", "strategy": "v4", "time": ct_now().isoformat()}), 200
