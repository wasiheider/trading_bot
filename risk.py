import json
import os

from config import RISK_PER_TRADE, PAPER_ACCOUNT_SIZE

STATE_FILE  = "state.json"
TRADES_FILE = "trades.json"

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

def _save_state():
    try:
        import pytz
        from datetime import datetime
        ct = pytz.timezone("America/Chicago")
        today = datetime.now(ct).strftime("%Y-%m-%d")
        paper_state["_date"] = today
        with open(STATE_FILE, "w") as f:
            json.dump({"paper": paper_state}, f, indent=2)
    except Exception as e:
        print(f"[state] WARNING: could not save state: {e}", flush=True)

_saved = _load_state()
_paper_date  = _saved.get("paper", {}).get("_date", "")
_paper_fresh = not _paper_date or not _is_today(_paper_date)

paper_state = {
    "account_balance": _saved.get("paper", {}).get("account_balance", PAPER_ACCOUNT_SIZE),
    "daily_pnl":       0.0 if _paper_fresh else _saved.get("paper", {}).get("daily_pnl", 0.0),
    "daily_signals":   0   if _paper_fresh else _saved.get("paper", {}).get("daily_signals", 0),
    "daily_wins":      0   if _paper_fresh else _saved.get("paper", {}).get("daily_wins", 0),
    "daily_losses":    0   if _paper_fresh else _saved.get("paper", {}).get("daily_losses", 0),
    "last_signal":     _saved.get("paper", {}).get("last_signal", None),
    "trades":          _saved.get("paper", {}).get("trades", []),
}

_save_state()

# ── Instrument config (pip values for lot sizing) ──────────
PAPER_INSTRUMENT_CONFIG = {
    # Metals
    "XAUUSD": {"pip_value": 1.00,  "pip_size": 0.01, "default_sl_pips": 20},
    "XAGUSD": {"pip_value": 0.50,  "pip_size": 0.01, "default_sl_pips": 20},
    # Indices
    "US100":  {"pip_value": 1.00,  "pip_size": 1.0,  "default_sl_pips": 20},
    "US30":   {"pip_value": 1.00,  "pip_size": 1.0,  "default_sl_pips": 20},
    "US500":  {"pip_value": 1.00,  "pip_size": 0.1,  "default_sl_pips": 20},
    # Commodities
    "USOIL":  {"pip_value": 1.00,  "pip_size": 0.01, "default_sl_pips": 20},
    # Forex
    "EURUSD": {"pip_value": 10.00, "pip_size": 0.0001, "default_sl_pips": 20},
    "GBPUSD": {"pip_value": 10.00, "pip_size": 0.0001, "default_sl_pips": 20},
    "USDJPY": {"pip_value": 9.00,  "pip_size": 0.01,   "default_sl_pips": 20},
    "EURNZD": {"pip_value": 10.00, "pip_size": 0.0001, "default_sl_pips": 20},
    # Crypto
    "BTCUSD": {"pip_value": 1.00,  "pip_size": 1.0,  "default_sl_pips": 20},
}


def check_paper_risk(instrument: str, sl_pips: int = None) -> dict:
    cfg = PAPER_INSTRUMENT_CONFIG.get(instrument.upper())
    if not cfg:
        return {"allowed": False, "reason": f"Unknown instrument: {instrument}"}

    sl = sl_pips or cfg["default_sl_pips"]
    risk_dollars = paper_state["account_balance"] * RISK_PER_TRADE
    lot_size = round(risk_dollars / (sl * cfg["pip_value"]), 2)
    lot_size = max(0.01, lot_size)

    return {
        "allowed":      True,
        "lot_size":     lot_size,
        "risk_dollars": round(risk_dollars, 2),
        "sl_pips":      sl,
    }


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
        paper_state["account_balance"] += pnl
    if won:
        paper_state["daily_wins"] += 1
    else:
        paper_state["daily_losses"] += 1
    _save_state()


def reset_paper_daily():
    paper_state["daily_pnl"]     = 0.0
    paper_state["daily_signals"] = 0
    paper_state["daily_wins"]    = 0
    paper_state["daily_losses"]  = 0
    paper_state["last_signal"]   = None
    _save_state()
