import db
db.init_db()  # must run before risk import triggers module-level state load

from flask import Flask, request, jsonify, send_file, Response
from datetime import datetime
import os
import json
import pytz

from config import PAPER_WEBHOOK_TOKEN, PAPER_ACCOUNT_SIZE
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
import oanda

_MASCOT = "🩷👑🤖👑🩷"

_OANDA_REVERSE_MAP = {v: k for k, v in oanda.INSTRUMENT_MAP.items()}

_OANDA_MAP = {
    "NAS100USD": "US100",
    "US30USD":   "US30",
    "SPX500USD": "US500",
    "WTICOUSD":  "USOIL",
    "XAGUSD":    "XAGUSD",
    "XAUUSD":    "XAUUSD",
}

# Micro futures don't get their own signal generation — they mirror the
# already-proven v5 BOS/mid_bos detection running on the parent CFD instrument's
# TradingView chart. A webhook arriving directly for a micro symbol (from its
# own, separately-alerting chart) is ignored; mirrored trades are derived
# server-side from the parent's own signal/lifecycle events instead.
MICRO_MIRROR_MAP = {
    "US100":  "MNQ",
    "US500":  "MES",
    "US30":   "MYM",
    "XAUUSD": "MGC",
    "USOIL":  "MCL",
}
MICRO_MIRROR_TARGETS = set(MICRO_MIRROR_MAP.values())


def _normalize_instrument(raw: str) -> str:
    s = raw.upper().replace("1!", "").replace("!", "")
    if ":" in s:
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


def _sync_oanda_on_startup():
    """On every startup, add any OANDA open positions missing from the DB."""
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
            if db.get_oanda_trade_id(bot_instrument, direction):
                continue
            open_time = t.get("openTime", "")[:16].replace("T", " ")
            db.append_trade({
                "time":           open_time[11:16] if len(open_time) >= 16 else "--",
                "date":           open_time,
                "instrument":     bot_instrument,
                "direction":      direction,
                "price":          float(t.get("price", 0)),
                "sl":             None,
                "tp1":            None,
                "tp2":            None,
                "lot_size":       int(abs(units)),
                "result":         "OPEN",
                "pnl":            0,
                "oanda_trade_id": trade_id,
            })
            print(f"[startup] Synced OANDA trade {trade_id} ({bot_instrument} {direction}) → DB", flush=True)
    except Exception as e:
        print(f"[startup] OANDA sync failed: {e}", flush=True)

    # Close stale OPEN records for log-only instruments (no OANDA trade ID, unresolvable)
    forex_instruments = set(oanda.INSTRUMENT_MAP.keys())
    db.close_stale_non_forex_opens(forex_instruments)


app = Flask(__name__)
CT = pytz.timezone("America/Chicago")

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

    instrument = _normalize_instrument(data.get("symbol", data.get("instrument", "")))
    if instrument in MICRO_MIRROR_TARGETS:
        print(f"[mirror] Ignoring direct webhook for {instrument} — mirrored from its parent instrument", flush=True)
        return jsonify({"status": "ignored", "reason": f"{instrument} is mirrored from its parent instrument"}), 200

    event = data.get("event", "").lower()
    if event in ("tp1_hit", "tp2_hit", "sl_hit"):
        return handle_paper_lifecycle(data, event)

    return handle_paper_signal(data)


def handle_paper_signal(data):
    instrument  = _normalize_instrument(data.get("symbol", data.get("instrument", "")))
    direction   = data.get("direction", "").upper()
    setup       = data.get("setup", "").lower()
    price       = data.get("entry_price", data.get("price"))
    sl          = data.get("stop_loss",   data.get("sl"))
    tp1         = data.get("tp1")
    tp2         = data.get("tp2")
    rr          = data.get("rr_to_tp1")
    timeframe   = data.get("timeframe", "?")
    bos_level   = data.get("bos_level")
    range_high  = data.get("range_high")
    range_low   = data.get("range_low")
    sl_pips     = _calc_sl_pips(instrument, price, sl) or data.get("sl_pips")

    risk = check_paper_risk(instrument, sl_pips)
    if not risk["allowed"]:
        msg = f"{_MASCOT}\n🚫 <b>Paper Signal Blocked</b>\n<code>{instrument} {direction}</code>\nReason: {risk['reason']}"
        send_telegram(msg)
        return jsonify({"status": "blocked", "reason": risk["reason"]}), 200

    if db.has_open_trade(instrument):
        msg = f"{_MASCOT}\n🚫 <b>Paper Signal Blocked</b>\n<code>{instrument} {direction}</code>\nReason: position already open on {instrument}"
        send_telegram(msg)
        return jsonify({"status": "blocked", "reason": f"position already open: {instrument}"}), 200

    daily_hit,  daily_reason  = is_daily_limit_hit()
    weekly_hit, weekly_reason = is_weekly_limit_hit()
    limit_hit    = daily_hit or weekly_hit
    limit_reason = daily_reason or weekly_reason

    oanda_trade_id   = None
    oanda_fill_price = price
    oanda_error      = None
    oanda_pending    = False
    oanda_supported  = instrument.upper() in oanda.INSTRUMENT_MAP
    if not limit_hit and oanda_supported:
        try:
            if setup == "box_break":
                fill = oanda.place_stop_order(instrument, direction, risk["lot_size"], price=price, sl_price=sl)
            else:
                try:
                    fill = oanda.place_limit_order(instrument, direction, risk["lot_size"], price=price, sl_price=sl)
                except RuntimeError as limit_err:
                    # Limit rejected (price already past entry level) — fall back to market
                    print(f"[oanda] Limit order rejected, falling back to market: {limit_err}", flush=True)
                    fill = oanda.place_order(instrument, direction, risk["lot_size"], sl_price=sl)
            oanda_trade_id   = fill.get("trade_id")
            oanda_fill_price = fill.get("price", price)
            oanda_pending    = fill.get("pending", False)
        except Exception as e:
            oanda_error = str(e)
            print(f"[oanda] ERROR placing order: {e}", flush=True)
            send_telegram(f"{_MASCOT}\n🔴 <b>OANDA ORDER FAILED</b>\n<code>{instrument} {direction}</code>\nError: <code>{oanda_error}</code>")

    emoji    = "🟢" if direction == "LONG" else "🔴"
    rr_line  = f"\nR:R: <code>{rr}</code>" if rr else ""
    bos_line = f"\nBOS: <code>{bos_level}</code>" if bos_level else ""
    tf_label = f"15M" if str(timeframe) == "15" else f"{timeframe}M"
    setup_label = {"spring": " · SPRING", "upthrust": " · UPTHRUST", "bos_div": " · BOS+DIV", "mid_bos": " · MID BOS", "box_break": " · BOX BREAK"}.get(setup, "")

    if limit_hit:
        exec_line = f"\n⚠️ <b>NOT EXECUTED — {limit_reason}</b>"
    elif not oanda_supported:
        exec_line = "\n📋 Log + Telegram only (not a forex pair)"
    elif oanda_pending:
        order_type = "STOP" if setup == "box_break" else "LIMIT"
        exec_line = f"\nOANDA: ⏳ {order_type} ORDER PENDING @ <code>{oanda_fill_price}</code>"
    elif oanda_trade_id:
        exec_line = f"\nOANDA ID: <code>{oanda_trade_id}</code> @ <code>{oanda_fill_price}</code>"
    else:
        exec_line = f"\nOANDA: FAILED ({oanda_error})"

    msg = (
        f"{_MASCOT}\n"
        f"{emoji} <b>Paper — {instrument} {direction}</b> [{tf_label}{setup_label}]\n"
        f"Entry: <code>{price}</code>\n"
        f"SL: <code>{sl}</code>\n"
        f"TP1: <code>{tp1}</code>\n"
        f"TP2: <code>{tp2}</code>\n"
        f"Units: <code>{risk['lot_size']}</code>\n"
        f"Risk: <code>${risk['risk_dollars']}</code>"
        f"{rr_line}"
        f"{bos_line}"
        f"{exec_line}"
    )

    db.log_signal(data)
    record_paper_signal(data)

    if not limit_hit:
        db.append_trade({
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

    mirror_symbol = MICRO_MIRROR_MAP.get(instrument)
    if mirror_symbol:
        mirror_data = dict(data)
        mirror_data["symbol"] = mirror_symbol
        mirror_data["instrument"] = mirror_symbol
        handle_paper_signal(mirror_data)

    return jsonify({"status": "approved", "lot_size": risk["lot_size"], "oanda_trade_id": oanda_trade_id, "limit_hit": limit_hit}), 200


def handle_paper_lifecycle(data, event):
    instrument = _normalize_instrument(data.get("symbol", data.get("instrument", "")))
    direction  = data.get("direction", "").upper()
    price      = data.get("price", data.get("entry_price"))
    pine_pnl   = data.get("pnl")
    lot_size   = data.get("lot_size")

    oanda_trade_id = db.get_oanda_trade_id(instrument, direction)
    if not oanda_trade_id and instrument.upper() in oanda.INSTRUMENT_MAP:
        try:
            open_trade = oanda.get_open_trade(instrument, direction)
            if open_trade:
                oanda_trade_id = open_trade["id"]
                print(f"[oanda] Recovered trade ID {oanda_trade_id} from OANDA API", flush=True)
        except Exception as e:
            print(f"[oanda] Could not query open trades: {e}", flush=True)

    oanda_pnl = None
    if oanda_trade_id:
        try:
            close_resp = oanda.close_trade(oanda_trade_id)
            oanda_pnl  = close_resp.get("realizedPL")
            print(f"[oanda] Closed trade {oanda_trade_id} ({event}), PNL: ${oanda_pnl:.2f}", flush=True)
        except Exception as e:
            print(f"[oanda] ERROR closing trade {oanda_trade_id}: {e}", flush=True)

    final_pnl = oanda_pnl if oanda_pnl is not None else pine_pnl

    locally_tracked = bool(oanda_trade_id) or db.has_open_trade(instrument, direction, include_unknown=True)
    if locally_tracked:
        won = event in ("tp1_hit", "tp2_hit")
        update_paper_outcome(won=won, pnl=final_pnl)
        result_map = {"tp1_hit": "TP1", "tp2_hit": "TP2", "sl_hit": "SL"}
        record_paper_trade({
            "instrument": instrument,
            "direction":  direction,
            "result":     result_map.get(event, event.upper()),
            "pnl":        final_pnl or 0,
        })
        db.update_trade(instrument, direction, result_map.get(event, event.upper()), final_pnl)

    emoji_map = {"tp1_hit": "✅", "tp2_hit": "🏆", "sl_hit": "❌"}
    label_map = {"tp1_hit": "TP1 Hit", "tp2_hit": "TP2 Hit", "sl_hit": "Stop Loss Hit"}
    dir_emoji = "🟢" if direction == "LONG" else "🔴"
    pnl_line  = f"\nP&L: <code>${final_pnl:+.2f}</code>" if final_pnl is not None else ""

    msg = (
        f"{_MASCOT}\n"
        f"{emoji_map.get(event, '📊')} <b>Paper {label_map.get(event, event.upper())}</b>\n"
        f"{dir_emoji} <code>{instrument} {direction}</code>\n"
        f"Price: <code>{price}</code>{pnl_line}"
    )
    send_telegram(msg)

    mirror_symbol = MICRO_MIRROR_MAP.get(instrument)
    if mirror_symbol:
        mirror_data = dict(data)
        mirror_data["symbol"] = mirror_symbol
        mirror_data["instrument"] = mirror_symbol
        handle_paper_lifecycle(mirror_data, event)

    return jsonify({"status": "logged", "event": event, "symbol": instrument}), 200


# ── State Endpoint ─────────────────────────────────────────

@app.route("/state", methods=["GET"])
def state():
    now = ct_now()
    wins   = paper_state.get("daily_wins",   0)
    losses = paper_state.get("daily_losses", 0)
    total  = wins + losses
    wr     = round((wins / total) * 100) if total > 0 else 0

    total_wins   = paper_state.get("total_wins",   0)
    total_losses = paper_state.get("total_losses", 0)
    total_trades = total_wins + total_losses
    total_wr     = round((total_wins / total_trades) * 100) if total_trades > 0 else 0

    # paper_account_balance is the all-instrument simulated balance (every
    # instrument's realized PNL, log-only ones included) — this is what the
    # dashboard's Balance/all-time PNL/equity curve are anchored to.
    # oanda_demo_balance is the real OANDA demo account balance, which only
    # ever reflects the 5 forex pairs actually executed there — kept
    # separately for verification, never used as the headline number.
    paper_balance       = paper_state.get("account_balance", 100000)
    oanda_demo_balance  = None
    unrealized_pnl      = 0.0
    open_trade_details  = []

    try:
        acct_summary       = oanda.get_account_summary()
        oanda_demo_balance = acct_summary["balance"]
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

    def _setup_to_model(s):
        s = (s or "").lower()
        if s in ("bos", ""):            return "A"
        if s in ("spring", "upthrust"): return "B"
        if s == "bos_div":              return "C"
        if s == "mid_bos":              return "D"
        if s == "box_break":            return "E"
        return "A"

    model_stats = {
        "A": {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0},
        "B": {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0},
        "C": {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0},
        "D": {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0},
        "E": {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0},
    }
    try:
        for t in db.load_trades():
            res = t.get("result", "OPEN")
            if res in ("OPEN", "UNKNOWN"):
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
    except Exception as e:
        print(f"[state] model_stats failed: {e}", flush=True)

    open_count = db.open_trade_count()

    return jsonify({
        "time_ct":                now.strftime("%Y-%m-%d %H:%M:%S"),
        "paper_account_balance":  round(paper_balance, 2),
        "oanda_demo_balance":     round(oanda_demo_balance, 2) if oanda_demo_balance is not None else None,
        "paper_total_pnl":        round(paper_balance - PAPER_ACCOUNT_SIZE, 2),
        "paper_daily_pnl":        round(paper_state.get("daily_pnl",    0.0), 2),
        "paper_weekly_pnl":       round(paper_state.get("weekly_pnl",   0.0), 2),
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
        "paper_trades":           db.load_recent_trades(10),
        "model_stats":            model_stats,
    }), 200


# ── Trades Endpoint ────────────────────────────────────────

@app.route("/trades", methods=["GET"])
def trades():
    return jsonify({"paper": db.load_trades()}), 200


# ── News Endpoint ──────────────────────────────────────────

import urllib.request
import xml.etree.ElementTree as ET
import time as _time
import concurrent.futures

_news_cache = {"data": [], "ts": 0}
NEWS_TTL = 900

YAHOO_FEEDS = [
    ("Markets",    "https://finance.yahoo.com/rss/topstories"),
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


# ── Calendar Endpoint ──────────────────────────────────────

_cal_cache = {"data": [], "ts": 0}
CAL_TTL = 3600

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


# ── COT Endpoint ───────────────────────────────────────────
# Source 1: CFTC TFF (Lev Funds) + Disaggregated (Managed Money) — batch fetched
# Source 2: Tradingster Legacy Non-Commercial — per-instrument, parallel fetched
# Both use Larry Williams COT Index: (net - 52wk_min) / (52wk_max - 52wk_min) * 100

_cot_cache = {"data": None, "ts": 0}
COT_TTL = 3600

_COT_TFF = {
    "EUR": "099741", "GBP": "096742", "JPY": "097741", "NZD": "112741",
    "USD": "098662", "ES":  "13874%2B", "NQ": "209742", "US30": "12460%2B",
    "BTC": "133741", "VIX": "1170E1",
}
_COT_DISAGG = {"GOLD": "088691", "SILVER": "084691", "CRUDE": "067651"}
_COT_INVERT = {"VIX"}


def _cot_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def _cot_index(nets):
    mn, mx = min(nets), max(nets)
    rng = mx - mn or 1
    return [round(((n - mn) / rng) * 100) for n in nets]


def _lev_signal(deltas):
    if len(deltas) < 2:
        return None
    if deltas[-2] < 0 and deltas[-1] > 0:
        return "flipped"
    if len(deltas) < 4:
        return None
    w = deltas[-4:]
    avg = sum(w) / 4
    std = (sum((x - avg) ** 2 for x in w) / 4) ** 0.5 or 1
    if deltas[-1] > avg + std * 0.75:
        return "covering"
    if deltas[-1] < avg - std * 0.75:
        return "pressing"
    return None


def _mm_signal(changes):
    if len(changes) < 4:
        return None
    w = changes[-4:]
    avg = sum(w) / 4
    std = (sum((x - avg) ** 2 for x in w) / 4) ** 0.5 or 1
    if w[-1] < avg - std * 0.75:
        return "closing"
    if w[-1] > avg + std * 0.75:
        return "adding"
    return None


def _near_term(bias, delta, lev):
    d_pos = delta > 500
    d_neg = delta < -500
    if lev == "flipped":
        return "↑ REVERSAL — Shorts Covering" if bias != "Bullish" else "↓ REVERSAL — Longs Rolling"
    if lev == "covering":
        if bias == "Bearish": return "↑ Shorts Fading"
        if bias == "Bullish": return "↑ Bulls Adding"
        if d_pos: return "↑ Accumulation"
    if lev == "pressing":
        if bias == "Bearish": return "↓ Bears Pressing"
        if bias == "Bullish": return "↓ Longs Fading — Caution"
        if d_neg: return "↓ Distribution"
    if d_pos and bias == "Bearish": return "↑ Delta Improving vs Bias"
    if d_neg and bias == "Bullish": return "↓ Delta Weakening vs Bias"
    if d_pos and bias == "Bullish": return "↑ Bias + Delta Aligned"
    if d_neg and bias == "Bearish": return "↓ Bias + Delta Aligned"
    return None


def _bias(idx):
    return "Bullish" if idx >= 60 else "Bearish" if idx <= 40 else "Neutral"


def _alignment(bias1, bias2):
    if not bias1 or not bias2:
        return "unknown"
    d = {"Bullish": 1, "Neutral": 0, "Bearish": -1}
    if d[bias1] == d[bias2] and bias1 != "Neutral":
        return "confirmed"
    if d[bias1] != d[bias2]:
        return "diverge"
    return "neutral"


def _fetch_tradingster_legacy(sym_code):
    sym, code = sym_code
    try:
        rows = _cot_get(f"https://www.tradingster.com/api/cot/legacy-futures/{code}")
        rows.sort(key=lambda r: r.get("As_of_Date", ""))
        return sym, rows
    except Exception as e:
        print(f"[cot] Tradingster legacy failed {sym}: {e}", flush=True)
        return sym, []


@app.route("/cot", methods=["GET"])
def cot():
    global _cot_cache
    now = _time.time()
    if now - _cot_cache["ts"] < COT_TTL and _cot_cache["data"]:
        return jsonify(_cot_cache["data"]), 200

    results = []

    # ── Source 1a: CFTC TFF batch ─────────────────────────────────────────
    tff_rows = []
    try:
        codes_q = ",".join(f"'{c}'" for c in _COT_TFF.values())
        tff_rows = _cot_get(
            f"https://publicreporting.cftc.gov/resource/gpe5-46if.json"
            f"?$where=cftc_contract_market_code%20in({codes_q})"
            f"&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=700"
        )
    except Exception as e:
        print(f"[cot] CFTC TFF fetch failed: {e}", flush=True)

    # ── Source 1b: CFTC Disaggregated batch ───────────────────────────────
    disagg_rows = []
    try:
        disagg_q = ",".join(f"'{c}'" for c in _COT_DISAGG.values())
        disagg_rows = _cot_get(
            f"https://publicreporting.cftc.gov/resource/kh3c-gbw2.json"
            f"?$where=cftc_contract_market_code%20in({disagg_q})"
            f"&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=200"
        )
    except Exception as e:
        print(f"[cot] CFTC Disagg fetch failed: {e}", flush=True)

    # ── Source 2: Tradingster Legacy Non-Commercial — parallel ────────────
    all_codes = {**_COT_TFF, **_COT_DISAGG}
    leg2 = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=13) as ex:
        for sym, rows in ex.map(_fetch_tradingster_legacy, all_codes.items()):
            leg2[sym] = rows

    # ── Process TFF instruments ───────────────────────────────────────────
    for sym, code in _COT_TFF.items():
        raw_code = code.replace("%2B", "+")
        rows = sorted(
            [r for r in tff_rows if r.get("cftc_contract_market_code") == raw_code],
            key=lambda r: r.get("report_date_as_yyyy_mm_dd", ""),
        )
        if len(rows) < 4:
            continue

        invert = sym in _COT_INVERT

        lev_nets = [
            int(r.get("lev_money_positions_long") or 0) - int(r.get("lev_money_positions_short") or 0)
            for r in rows
        ]
        idxs = _cot_index(lev_nets)
        if invert:
            idxs = [100 - i for i in idxs]
        idx = idxs[-1]
        bias1 = _bias(idx)

        lev_deltas = [
            int(r.get("change_in_lev_money_long") or 0) - int(r.get("change_in_lev_money_short") or 0)
            for r in rows
        ]
        if invert:
            lev_deltas = [-d for d in lev_deltas]
        weekly_delta = lev_deltas[-1]
        lev = _lev_signal(lev_deltas)

        am_changes = [int(r.get("change_in_asset_mgr_long") or 0) for r in rows]
        mm = None if invert else _mm_signal(am_changes)

        latest_date = rows[-1].get("report_date_as_yyyy_mm_dd", "")[:10]

        # Source 2: NonCommercial from Tradingster Legacy
        leg_idx = leg_bias1 = leg_lev = leg_net_now = leg_net_1wk = None
        leg_rows = leg2.get(sym, [])
        if len(leg_rows) >= 4:
            nc_nets = [
                int(r.get("Noncommercial_Positions_Long_All") or 0) - int(r.get("Noncommercial_Positions_Short_All") or 0)
                for r in leg_rows
            ]
            nc_idxs = _cot_index(nc_nets)
            if invert:
                nc_idxs = [100 - i for i in nc_idxs]
            leg_idx = nc_idxs[-1]
            leg_bias1 = _bias(leg_idx)
            nc_deltas = [nc_nets[i] - nc_nets[i - 1] for i in range(1, len(nc_nets))]
            if invert:
                nc_deltas = [-d for d in nc_deltas]
            leg_lev = _lev_signal(nc_deltas)
            leg_net_now = nc_nets[-1]
            leg_net_1wk = nc_nets[-2]

        results.append({
            "sym": sym, "date": latest_date,
            "net_now": lev_nets[-1], "net_1wk": lev_nets[-2],
            "net_2wk": lev_nets[-3] if len(lev_nets) >= 3 else None,
            "weekly_delta": weekly_delta,
            "cot_idx": idx, "bias": bias1,
            "lev_signal": lev, "mm_signal": mm,
            "near_term": _near_term(bias1, weekly_delta, lev),
            "src1_label": "LevF (inv)" if invert else "Lev Funds",
            "leg_idx": leg_idx, "leg_bias": leg_bias1,
            "leg_lev": leg_lev,
            "leg_net_now": leg_net_now, "leg_net_1wk": leg_net_1wk,
            "alignment": _alignment(bias1, leg_bias1),
        })

    # ── Process commodity instruments ─────────────────────────────────────
    for sym, code in _COT_DISAGG.items():
        rows = sorted(
            [r for r in disagg_rows if r.get("cftc_contract_market_code") == code],
            key=lambda r: r.get("report_date_as_yyyy_mm_dd", ""),
        )
        if len(rows) < 4:
            continue

        mm_nets = [
            int(r.get("m_money_positions_long_all") or 0) - int(r.get("m_money_positions_short_all") or 0)
            for r in rows
        ]
        idxs = _cot_index(mm_nets)
        idx = idxs[-1]
        bias1 = _bias(idx)

        mm_deltas = [
            int(r.get("change_in_m_money_long_all") or 0) - int(r.get("change_in_m_money_short_all") or 0)
            for r in rows
        ]
        weekly_delta = mm_deltas[-1]
        lev = _lev_signal(mm_deltas)
        mm_long_changes = [int(r.get("change_in_m_money_long_all") or 0) for r in rows]
        mm = _mm_signal(mm_long_changes)

        latest_date = rows[-1].get("report_date_as_yyyy_mm_dd", "")[:10]

        leg_idx = leg_bias1 = leg_lev = leg_net_now = leg_net_1wk = None
        leg_rows = leg2.get(sym, [])
        if len(leg_rows) >= 4:
            nc_nets = [
                int(r.get("Noncommercial_Positions_Long_All") or 0) - int(r.get("Noncommercial_Positions_Short_All") or 0)
                for r in leg_rows
            ]
            nc_idxs = _cot_index(nc_nets)
            leg_idx = nc_idxs[-1]
            leg_bias1 = _bias(leg_idx)
            nc_deltas = [nc_nets[i] - nc_nets[i - 1] for i in range(1, len(nc_nets))]
            leg_lev = _lev_signal(nc_deltas)
            leg_net_now = nc_nets[-1]
            leg_net_1wk = nc_nets[-2]

        results.append({
            "sym": sym, "date": latest_date,
            "net_now": mm_nets[-1], "net_1wk": mm_nets[-2],
            "net_2wk": mm_nets[-3] if len(mm_nets) >= 3 else None,
            "weekly_delta": weekly_delta,
            "cot_idx": idx, "bias": bias1,
            "lev_signal": lev, "mm_signal": mm,
            "near_term": _near_term(bias1, weekly_delta, lev),
            "src1_label": "Mgd Money",
            "leg_idx": leg_idx, "leg_bias": leg_bias1,
            "leg_lev": leg_lev,
            "leg_net_now": leg_net_now, "leg_net_1wk": leg_net_1wk,
            "alignment": _alignment(bias1, leg_bias1),
        })

    latest = max((r["date"] for r in results if r.get("date")), default="")
    payload = {"updated": latest, "instruments": results}
    _cot_cache = {"data": payload, "ts": now}
    return jsonify(payload), 200


# ── Dashboard ──────────────────────────────────────────────

@app.route("/dashboard", methods=["GET"])
def dashboard():
    dash_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    if os.path.exists(dash_path):
        return send_file(dash_path, mimetype="text/html")
    return Response("<h1>dashboard.html not found</h1>", mimetype="text/html"), 404


# ── Daily Report / Monitor ────────────────────────────────

@app.route("/report", methods=["GET"])
def report():
    """Generates a monitoring summary and sends it to Telegram. Called by scheduled cloud agent."""
    try:
        # Balance + live OANDA data
        oanda_balance  = paper_state.get("account_balance", 100000)
        unrealized_pnl = 0.0
        open_details   = []
        try:
            acct = oanda.get_account_summary()
            oanda_balance = acct["balance"]
        except Exception:
            pass
        try:
            for t in oanda.get_all_open_trades():
                upnl      = float(t.get("unrealizedPL", 0) or 0)
                units     = float(t.get("currentUnits", 0))
                direction = "LONG" if units > 0 else "SHORT"
                instr     = _OANDA_REVERSE_MAP.get(t.get("instrument", ""), t.get("instrument", ""))
                unrealized_pnl += upnl
                open_details.append(f"`{instr} {direction}` uPNL: `${upnl:+.2f}`")
        except Exception:
            pass

        daily_pnl  = paper_state.get("daily_pnl",  0.0)
        weekly_pnl = paper_state.get("weekly_pnl", 0.0)
        total_wins   = paper_state.get("total_wins",   0)
        total_losses = paper_state.get("total_losses", 0)
        total_trades = total_wins + total_losses
        total_wr     = round(total_wins / total_trades * 100) if total_trades else 0
        open_count   = db.open_trade_count()

        # Loss limit proximity flags
        flags = []
        daily_loss  = -daily_pnl
        weekly_loss = -weekly_pnl
        from config import MAX_DAILY_LOSS, MAX_WEEKLY_LOSS
        daily_pct  = round(daily_loss  / MAX_DAILY_LOSS  * 100) if MAX_DAILY_LOSS  else 0
        weekly_pct = round(weekly_loss / MAX_WEEKLY_LOSS * 100) if MAX_WEEKLY_LOSS else 0
        if daily_pct >= 75:
            flags.append(f"⚠️ Daily loss `${daily_loss:,.0f}` is {daily_pct}% of `${MAX_DAILY_LOSS:,.0f}` limit")
        if weekly_pct >= 75:
            flags.append(f"⚠️ Weekly loss `${weekly_loss:,.0f}` is {weekly_pct}% of `${MAX_WEEKLY_LOSS:,.0f}` limit")

        open_section = "\n".join(open_details) if open_details else "_None_"
        flag_section = "\n".join(flags) if flags else "✅ All limits clear"

        daily_emoji  = "🟢" if daily_pnl  >= 0 else "🔴"
        weekly_emoji = "🟢" if weekly_pnl >= 0 else "🔴"

        msg = (
            f"{_MASCOT}\n"
            f"📊 <b>Daily Monitor — {ct_now().strftime('%b %d, %Y %H:%M CT')}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"💰 Balance: <code>${oanda_balance:,.2f}</code>\n"
            f"{daily_emoji} Daily PNL: <code>${daily_pnl:+,.2f}</code>\n"
            f"{weekly_emoji} Weekly PNL: <code>${weekly_pnl:+,.2f}</code>\n"
            f"📈 Unrealized: <code>${unrealized_pnl:+,.2f}</code>\n"
            f"🔄 Open Trades: <code>{open_count}</code>\n"
            f"🏆 All-Time: <code>{total_wins}W / {total_losses}L</code> ({total_wr}% WR)\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Open Positions:</b>\n{open_section}\n"
            f"━━━━━━━━━━━━━━━━━━━\n"
            f"<b>Risk Flags:</b>\n{flag_section}"
        )
        ok = send_telegram(msg)
        status = "report_sent" if ok else "telegram_failed"
        return jsonify({"status": status, "balance": oanda_balance, "daily_pnl": daily_pnl}), 200

    except Exception as e:
        error_msg = f"{_MASCOT}\n🔴 <b>Monitor Error</b>\nCould not generate report: <code>{e}</code>"
        send_telegram(error_msg)
        return jsonify({"status": "error", "error": str(e)}), 500


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
    return jsonify({"status": "ok", "mode": "paper-trading", "strategy": "v5", "time": ct_now().isoformat()}), 200
