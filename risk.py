import json
import os

import os as _os
from config import (
    RISK_PER_TRADE, PAPER_ACCOUNT_SIZE,
    MAX_DAILY_LOSS, MAX_WEEKLY_LOSS,
    DATA_DIR,
)

STATE_FILE  = _os.path.join(DATA_DIR, "state.json")
TRADES_FILE = _os.path.join(DATA_DIR, "trades.json")

def _load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _is_today(date_str: str) -> bool:
    try:
        import pytz
        from datetime import datetime
        ct = pytz.timezone("America/Chicago")
        today = datetime.now(ct).strftime("%Y-%m-%d")
        return date_str == today
    except Exception:
        return False

def _get_week_start() -> str:
    """Monday date (CT) of the current week — used to detect a new week."""
    try:
        import pytz
        from datetime import datetime, timedelta
        ct = pytz.timezone("America/Chicago")
        now = datetime.now(ct)
        monday = now - timedelta(days=now.weekday())
        return monday.strftime("%Y-%m-%d")
    except Exception:
        return ""

def _save_state():
    try:
        import pytz
        from datetime import datetime
        ct = pytz.timezone("America/Chicago")
        paper_state["_date"] = datetime.now(ct).strftime("%Y-%m-%d")
        paper_state["_week"] = _get_week_start()
        with open(STATE_FILE, "w") as f:
            json.dump({"paper": paper_state}, f, indent=2)
    except Exception as e:
        print(f"[state] WARNING: could not save state: {e}", flush=True)


_saved       = _load_state()
_paper_date  = _saved.get("paper", {}).get("_date", "")
_paper_week  = _saved.get("paper", {}).get("_week", "")
_paper_fresh = not _paper_date or not _is_today(_paper_date)
_week_fresh  = not _paper_week or _paper_week != _get_week_start()

paper_state = {
    "account_balance": _saved.get("paper", {}).get("account_balance", PAPER_ACCOUNT_SIZE),
    # daily — reset each midnight CT
    "daily_pnl":       0.0 if _paper_fresh else _saved.get("paper", {}).get("daily_pnl", 0.0),
    "daily_signals":   0   if _paper_fresh else _saved.get("paper", {}).get("daily_signals", 0),
    "daily_wins":      0   if _paper_fresh else _saved.get("paper", {}).get("daily_wins", 0),
    "daily_losses":    0   if _paper_fresh else _saved.get("paper", {}).get("daily_losses", 0),
    "sl_hits_today":   {}  if _paper_fresh else _saved.get("paper", {}).get("sl_hits_today", {}),
    # weekly — reset each Monday midnight CT
    "weekly_pnl":      0.0 if _week_fresh  else _saved.get("paper", {}).get("weekly_pnl", 0.0),
    "weekly_sl_hits":  0   if _week_fresh  else _saved.get("paper", {}).get("weekly_sl_hits", 0),
    # all-time — never reset (persists across days/weeks)
    "total_wins":      _saved.get("paper", {}).get("total_wins", 0),
    "total_losses":    _saved.get("paper", {}).get("total_losses", 0),
    "last_signal":     _saved.get("paper", {}).get("last_signal", None),
    "trades":          _saved.get("paper", {}).get("trades", []),
}

_save_state()

# ── Instrument config ──────────────────────────────────────
# Non-forex: pip_value = USD per pip per CONTRACT. lot_size output = contracts.
# Forex:     pip_value = USD per pip per UNIT (base currency).
#            lot_size output = UNITS of base currency (integer).
#            "forex": True flag triggers integer rounding in check_paper_risk().
PAPER_INSTRUMENT_CONFIG = {
    # Metals
    "XAUUSD": {"pip_value": 1.00,  "pip_size": 0.01,   "default_sl_pips": 20},
    "GC":     {"pip_value": 1.00,  "pip_size": 0.01,   "default_sl_pips": 20},
    "MGC":    {"pip_value": 1.00,  "pip_size": 0.01,   "default_sl_pips": 20},
    "XAGUSD": {"pip_value": 0.50,  "pip_size": 0.01,   "default_sl_pips": 20},
    # Indices
    "US100":  {"pip_value": 1.00,  "pip_size": 1.0,    "default_sl_pips": 20},
    "NQ":     {"pip_value": 1.00,  "pip_size": 1.0,    "default_sl_pips": 20},
    "MNQ":    {"pip_value": 2.00,  "pip_size": 0.25,   "default_sl_pips": 20},
    "US30":   {"pip_value": 1.00,  "pip_size": 1.0,    "default_sl_pips": 20},
    "YM":     {"pip_value": 1.00,  "pip_size": 1.0,    "default_sl_pips": 20},
    "MYM":    {"pip_value": 0.50,  "pip_size": 1.0,    "default_sl_pips": 20},
    "US500":  {"pip_value": 1.00,  "pip_size": 0.1,    "default_sl_pips": 20},
    "ES":     {"pip_value": 50.00, "pip_size": 0.25,   "default_sl_pips": 20},
    "MES":    {"pip_value": 5.00,  "pip_size": 0.25,   "default_sl_pips": 20},
    # Commodities
    "USOIL":  {"pip_value": 1.00,  "pip_size": 0.01,   "default_sl_pips": 20},
    "CL":     {"pip_value": 1.00,  "pip_size": 0.01,   "default_sl_pips": 20},
    "MCL":    {"pip_value": 1.00,  "pip_size": 0.01,   "default_sl_pips": 20},
    # Forex — pip_value per UNIT per pip; output = UNITS (integer)
    # min_sl_pips: minimum SL width used for sizing — prevents tiny SLs from
    # creating enormous unit counts that blow through the $500 risk cap on slippage.
    # OANDA stopLossOnFill is always set at the actual Pine Script SL price.
    # USD-quoted pairs: 1 pip = $0.0001 per unit (always fixed)
    "EURUSD": {"pip_value": 0.0001,    "pip_size": 0.0001, "default_sl_pips": 20, "min_sl_pips": 15, "forex": True},
    "GBPUSD": {"pip_value": 0.0001,    "pip_size": 0.0001, "default_sl_pips": 20, "min_sl_pips": 15, "forex": True},
    "NZDUSD": {"pip_value": 0.0001,    "pip_size": 0.0001, "default_sl_pips": 20, "min_sl_pips": 15, "forex": True},
    # JPY-quoted: pip = 0.01; pip_value = 0.01 / USDJPY_rate  (~0.0000625 at 160 JPY)
    "USDJPY": {"pip_value": 0.0000625, "pip_size": 0.01,   "default_sl_pips": 20, "min_sl_pips": 15, "forex": True},
    # Cross with NZD quote: pip_value = 0.0001 × NZDUSD  (~0.0000583 at NZDUSD 0.583)
    "EURNZD": {"pip_value": 0.0000583, "pip_size": 0.0001, "default_sl_pips": 20, "min_sl_pips": 20, "forex": True},
    # Crypto
    "BTCUSD": {"pip_value": 1.00,  "pip_size": 1.0,    "default_sl_pips": 20},
}


def check_paper_risk(instrument: str, sl_pips: int = None) -> dict:
    cfg = PAPER_INSTRUMENT_CONFIG.get(instrument.upper())
    if not cfg:
        return {"allowed": False, "reason": f"Unknown instrument: {instrument}"}

    sl = sl_pips or cfg["default_sl_pips"]
    # Enforce minimum SL for sizing — keeps position size small even when Pine
    # Script sends a tight SL. OANDA stopLossOnFill still uses the actual SL price.
    sl_for_sizing = max(sl, cfg.get("min_sl_pips", 0))
    risk_dollars = paper_state["account_balance"] * RISK_PER_TRADE
    raw = risk_dollars / (sl_for_sizing * cfg["pip_value"])

    if cfg.get("forex"):
        # Forex: output is integer units of base currency (e.g. 125000 for EURUSD)
        lot_size = max(1, round(raw))
    else:
        # Futures/indices/metals: output is contracts (decimal lots)
        lot_size = max(0.01, round(raw, 2))

    return {
        "allowed":      True,
        "lot_size":     lot_size,
        "risk_dollars": round(risk_dollars, 2),
        "sl_pips":      sl,
    }


# ── Limit checks ───────────────────────────────────────────

def is_daily_limit_hit() -> tuple:
    """Returns (hit: bool, reason: str)."""
    daily_loss = -(paper_state.get("daily_pnl", 0.0))
    if daily_loss >= MAX_DAILY_LOSS:
        return True, f"daily loss limit ${daily_loss:,.0f} / ${MAX_DAILY_LOSS:,.0f}"
    return False, ""


def is_weekly_limit_hit() -> tuple:
    """Returns (hit: bool, reason: str)."""
    weekly_loss = -(paper_state.get("weekly_pnl", 0.0))
    if weekly_loss >= MAX_WEEKLY_LOSS:
        return True, f"weekly loss limit ${weekly_loss:,.0f} / ${MAX_WEEKLY_LOSS:,.0f}"
    return False, ""


# ── State mutation ─────────────────────────────────────────

def record_sl_hit(instrument: str):
    hits = paper_state.setdefault("sl_hits_today", {})
    hits[instrument.upper()] = hits.get(instrument.upper(), 0) + 1
    paper_state["weekly_sl_hits"] = paper_state.get("weekly_sl_hits", 0) + 1
    _save_state()


def get_sl_hits(instrument: str) -> int:
    return paper_state.get("sl_hits_today", {}).get(instrument.upper(), 0)


def record_paper_signal(payload: dict):
    from datetime import datetime
    import pytz
    ct = pytz.timezone("America/Chicago")
    paper_state["daily_signals"] += 1
    paper_state["last_signal"] = {
        "time":       datetime.now(ct).strftime("%H:%M"),
        "instrument": payload.get("symbol", payload.get("instrument", "")),
        "direction":  payload.get("direction", ""),
        "price":      payload.get("entry_price", payload.get("price")),
        "sl":         payload.get("stop_loss", payload.get("sl")),
        "tp1":        payload.get("tp1"),
        "tp2":        payload.get("tp2"),
        "lot_size":   payload.get("lot_size"),
        "rr":         payload.get("rr_to_tp1"),
        "bos_level":  payload.get("bos_level"),
    }
    _save_state()


def record_paper_trade(trade: dict):
    paper_state["trades"].append(trade)
    paper_state["trades"] = paper_state["trades"][-50:]
    _save_state()


def update_paper_outcome(won: bool, pnl: float = None):
    if pnl is not None:
        paper_state["daily_pnl"]       += pnl
        paper_state["weekly_pnl"]      += pnl
        paper_state["account_balance"] += pnl
    if won:
        paper_state["daily_wins"]   += 1
        paper_state["total_wins"]   += 1
    else:
        paper_state["daily_losses"] += 1
        paper_state["total_losses"] += 1
    _save_state()


def reset_paper_daily():
    paper_state["daily_pnl"]     = 0.0
    paper_state["daily_signals"] = 0
    paper_state["daily_wins"]    = 0
    paper_state["daily_losses"]  = 0
    paper_state["sl_hits_today"] = {}
    paper_state["last_signal"]   = None
    _save_state()


def reset_paper_weekly():
    paper_state["weekly_pnl"]     = 0.0
    paper_state["weekly_sl_hits"] = 0
    _save_state()


def reset_paper_full():
    paper_state["account_balance"] = PAPER_ACCOUNT_SIZE
    paper_state["daily_pnl"]       = 0.0
    paper_state["daily_signals"]   = 0
    paper_state["daily_wins"]      = 0
    paper_state["daily_losses"]    = 0
    paper_state["sl_hits_today"]   = {}
    paper_state["weekly_pnl"]      = 0.0
    paper_state["weekly_sl_hits"]  = 0
    paper_state["total_wins"]      = 0
    paper_state["total_losses"]    = 0
    paper_state["last_signal"]     = None
    paper_state["trades"]          = []
    _save_state()
    try:
        with open(TRADES_FILE, "w") as f:
            json.dump({"paper": []}, f)
    except Exception:
        pass
