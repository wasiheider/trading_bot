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
import pickmytrade

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
        exec_line = "\n🏦 FTMO Tradingview Eval Trade"
    elif oanda_pending:
        order_type = "STOP" if setup == "box_break" else "LIMIT"
        exec_line = f"\nOANDA: ⏳ {order_type} ORDER PENDING @ <code>{oanda_fill_price}</code>\n🏦 Paper/FTMO MT5 Trade"
    elif oanda_trade_id:
        exec_line = f"\nOANDA ID: <code>{oanda_trade_id}</code> @ <code>{oanda_fill_price}</code>\n🏦 Paper/FTMO MT5 Trade"
    else:
        exec_line = f"\nOANDA: FAILED ({oanda_error})\n🏦 Paper/FTMO MT5 Trade"

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

    db.log_signal({**data, "symbol": instrument})
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
            # A hard OANDA order failure (both limit and market fallback threw)
            # must never be logged as OPEN -- no real position exists, and an
            # OPEN row with no oanda_trade_id blocks db.has_open_trade() from
            # ever allowing a new signal on this instrument again, with no
            # existing cleanup job for forex (close_stale_non_forex_opens /
            # delete_stale_non_forex_opens are both scoped to non-forex only).
            "result":         "FAILED" if oanda_error else "OPEN",
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

        # DISABLED 2026-07-06: Apex support (ticket #1628893) confirmed fully
        # automated order entry/modification is a strict user-agreement
        # violation, grounds for account closure -- "semi-automated" tools
        # need written approval first, which this pipeline does not have.
        # Do not re-enable without that written approval from Apex.
        #
        # try:
        #     pickmytrade.send_entry(
        #         instrument=mirror_symbol,
        #         direction=direction,
        #         setup=setup,
        #         price=price,
        #         sl=sl,
        #         tp2=tp2,
        #     )
        # except Exception as e:
        #     print(f"[pickmytrade] ERROR relaying to Apex: {e}", flush=True)
        #     send_telegram(f"{_MASCOT}\n🔴 <b>APEX/PICKMYTRADE RELAY FAILED</b>\n<code>{mirror_symbol} {direction}</code>\nError: <code>{e}</code>")

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
    trade_label = "\n🏦 Paper/FTMO MT5 Trade" if instrument.upper() in oanda.INSTRUMENT_MAP else "\n🏦 FTMO Tradingview Eval Trade"

    msg = (
        f"{_MASCOT}\n"
        f"{emoji_map.get(event, '📊')} <b>Paper {label_map.get(event, event.upper())}</b>\n"
        f"{dir_emoji} <code>{instrument} {direction}</code>\n"
        f"Price: <code>{price}</code>{pnl_line}{trade_label}"
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


# ── Latest Signal Endpoint (MT5 EA polling) ─────────────────
# Polled by the FTMO MT5 EA — entry signals only. Exit management (TP1/TP2/
# trailing) is handled by the EA itself watching live price, not by polling
# this endpoint, so no lifecycle events are exposed here. Micro futures are
# intentionally excluded — they're just server-side mirrors of these same 12.
EA_INSTRUMENTS = [
    "EURUSD", "GBPUSD", "USDJPY", "EURNZD", "NZDUSD",
    "XAUUSD", "XAGUSD", "US100", "US30", "US500", "USOIL", "BTCUSD",
]
SIGNAL_MAX_AGE_HOURS = 6

@app.route("/latest-signal", methods=["GET"])
def latest_signal():
    signals = db.get_latest_signals(EA_INSTRUMENTS, max_age_hours=SIGNAL_MAX_AGE_HOURS)
    return jsonify({
        "time_ct": ct_now().strftime("%Y-%m-%d %H:%M:%S"),
        "signals": signals,
    }), 200


# ── News Endpoint ──────────────────────────────────────────

import urllib.request
import xml.etree.ElementTree as ET
import time as _time

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


# ── FOMC Endpoint ─────────────────────────────────────────
# Schedule published annually by the Fed — update each December for the next year.
# Rate field: update manually after each FOMC decision.

_FOMC_RATE = "3.50–3.75%"

_FOMC_2026 = [
    {"dates": "Jan 27–28", "decision": "2026-01-28", "projections": False, "outcome": "HOLD"},
    {"dates": "Mar 17–18", "decision": "2026-03-18", "projections": True,  "outcome": "HOLD"},
    {"dates": "Apr 28–29", "decision": "2026-04-29", "projections": False, "outcome": "HOLD"},
    {"dates": "Jun 16–17", "decision": "2026-06-17", "projections": True,  "outcome": "HOLD"},
    {"dates": "Jul 28–29", "decision": "2026-07-29", "projections": False, "outcome": None},
    {"dates": "Sep 15–16", "decision": "2026-09-16", "projections": True,  "outcome": None},
    {"dates": "Oct 27–28", "decision": "2026-10-28", "projections": False, "outcome": None},
    {"dates": "Dec 8–9",   "decision": "2026-12-09", "projections": True,  "outcome": None},
]

@app.route("/fomc", methods=["GET"])
def fomc():
    from datetime import date as _date
    today = _date.today().isoformat()
    meetings = []
    next_found = False
    for m in _FOMC_2026:
        is_past = m["decision"] < today
        is_next = not is_past and not next_found
        if is_next:
            next_found = True
        meetings.append({
            "dates":      m["dates"],
            "decision":   m["decision"],
            "projections": m["projections"],
            "outcome":    m["outcome"],
            "past":       is_past,
            "next":       is_next,
        })
    return jsonify({"meetings": meetings, "rate": _FOMC_RATE}), 200


# ── COT Endpoint ───────────────────────────────────────────
# Served from Postgres (cot_cache table), not fetched live -- Railway's
# outbound IP is blocked (HTTP 403) by CFTC's Socrata-hosted API, confirmed
# not a header/User-Agent issue (see project memory 2026-07-07). CFTC only
# publishes new data weekly, so cot_fetch.py runs from an unblocked
# environment (on a weekly schedule) and POSTs the refreshed payload to
# /admin/cot-update below -- see cot_fetch.py's docstring for details.

@app.route("/cot", methods=["GET"])
def cot():
    return jsonify(db.get_cot_cache()), 200


@app.route("/admin/cot-update", methods=["POST"])
def admin_cot_update():
    token = request.json.get("token") if request.is_json else None
    if token != PAPER_WEBHOOK_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    payload = request.json.get("payload")
    if not isinstance(payload, dict) or "instruments" not in payload:
        return jsonify({"error": "invalid payload"}), 400
    db.save_cot_cache(payload)
    return jsonify({"status": "saved", "count": len(payload["instruments"])}), 200


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


@app.route("/admin/resolve-trade", methods=["POST"])
def admin_resolve_trade():
    """Manually resolve a single trade stuck at OPEN/UNKNOWN (e.g. a hard OANDA
    order failure logged before it, or any future case with no auto-cleanup)."""
    token = request.json.get("token") if request.is_json else None
    if token != PAPER_WEBHOOK_TOKEN:
        return jsonify({"error": "unauthorized"}), 401
    trade_id = request.json.get("trade_id")
    if not isinstance(trade_id, int):
        return jsonify({"error": "trade_id (int) required"}), 400
    result = request.json.get("result", "FAILED")
    pnl = request.json.get("pnl", 0)
    updated = db.force_resolve_trade(trade_id, result, pnl)
    if not updated:
        return jsonify({"error": f"no trade with id={trade_id}"}), 404
    return jsonify({"status": "resolved", "trade_id": trade_id, "result": result}), 200


# ── Health ─────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "mode": "paper-trading", "strategy": "v5", "time": ct_now().isoformat()}), 200
