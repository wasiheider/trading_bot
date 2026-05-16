import json
import os

from config import (
    RISK_PER_TRADE,
    MAX_CONSEC_LOSSES,
    TPT_ACCOUNT_SIZE,
    TPT_MAX_CONTRACTS,
    TPT_DRAWDOWN_FLOOR,
    FTMO_ACCOUNT_SIZE,
)

# ── Persistent state file ───────────────────────────────────
STATE_FILE = "state.json"

def _load_state():
    """Load state from disk on startup. Falls back to defaults if file missing."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass  # Corrupted file — fall back to defaults
    return {}

def _save_state():
    """Write current state to disk."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"tpt": tpt_state, "ftmo": ftmo_state}, f, indent=2)
    except Exception as e:
        print(f"[state] WARNING: could not save state: {e}", flush=True)

# ── State tracking (loaded from disk on startup) ────────────
_saved = _load_state()

tpt_state = {
    "consecutive_losses": _saved.get("tpt", {}).get("consecutive_losses", 0),
    "killed":             _saved.get("tpt", {}).get("killed", False),
    "daily_pnl":          _saved.get("tpt", {}).get("daily_pnl", 0.0),
    "account_balance":    _saved.get("tpt", {}).get("account_balance", TPT_ACCOUNT_SIZE),
    # Dashboard additions
    "daily_signals":      _saved.get("tpt", {}).get("daily_signals", 0),
    "daily_wins":         _saved.get("tpt", {}).get("daily_wins", 0),
    "daily_losses":       _saved.get("tpt", {}).get("daily_losses", 0),
    "last_signal":        _saved.get("tpt", {}).get("last_signal", None),   # full payload from Pine Script
    "trades":             _saved.get("tpt", {}).get("trades", []),           # last 20 trades
}

ftmo_state = {
    "daily_pnl":          _saved.get("ftmo", {}).get("daily_pnl", 0.0),
    "consecutive_losses": _saved.get("ftmo", {}).get("consecutive_losses", 0),
    "account_balance":    _saved.get("ftmo", {}).get("account_balance", FTMO_ACCOUNT_SIZE),
    # Dashboard additions
    "daily_signals":      _saved.get("ftmo", {}).get("daily_signals", 0),
    "daily_wins":         _saved.get("ftmo", {}).get("daily_wins", 0),
    "daily_losses":       _saved.get("ftmo", {}).get("daily_losses", 0),
    "trades":             _saved.get("ftmo", {}).get("trades", []),           # last 20 trades
}

# Save immediately so file always exists after first startup
_save_state()

# ── TPT pip/tick values ────────────────────────────────────
TPT_INSTRUMENT_CONFIG = {
    "MNQ": {"tick_size": 0.25, "tick_value": 0.50,  "default_sl_ticks": 20},
    "MCL": {"tick_size": 0.01, "tick_value": 1.00,  "default_sl_ticks": 20},
    "MGC": {"tick_size": 0.10, "tick_value": 1.00,  "default_sl_ticks": 20},
}

# ── FTMO pip values ────────────────────────────────────────
FTMO_INSTRUMENT_CONFIG = {
    "XAUUSD": {"pip_value": 1.00,  "default_sl_pips": 20},
    "USOIL":  {"pip_value": 1.00,  "default_sl_pips": 20},
    "US100":  {"pip_value": 1.00,  "default_sl_pips": 20},
    "GBPUSD": {"pip_value": 10.00, "default_sl_pips": 20},
    "USDJPY": {"pip_value": 9.00,  "default_sl_pips": 20},
    "EURNZD": {"pip_value": 10.00, "default_sl_pips": 20},
    "EURUSD": {"pip_value": 10.00, "default_sl_pips": 20},
}


def calculate_tpt_position_size(instrument: str, sl_ticks: int = None) -> dict:
    """Calculate TPT position size based on 0.25% risk per trade."""
    cfg = TPT_INSTRUMENT_CONFIG.get(instrument.upper())
    if not cfg:
        return {"error": f"Unknown TPT instrument: {instrument}"}

    sl = sl_ticks or cfg["default_sl_ticks"]
    risk_dollars = tpt_state["account_balance"] * RISK_PER_TRADE
    risk_per_contract = sl * cfg["tick_value"]
    contracts = int(risk_dollars / risk_per_contract)
    contracts = max(1, min(contracts, TPT_MAX_CONTRACTS))

    return {
        "contracts":    contracts,
        "risk_dollars": round(risk_dollars, 2),
        "sl_ticks":     sl,
        "tick_value":   cfg["tick_value"],
    }


def check_tpt_risk(instrument: str, sl_ticks: int = None) -> dict:
    """Gate check before allowing a TPT signal through."""

    if tpt_state["killed"]:
        return {"allowed": False, "reason": "TPT hard stop — 2 consecutive losses. Resets at midnight CT."}

    if tpt_state["account_balance"] <= TPT_DRAWDOWN_FLOOR:
        return {"allowed": False, "reason": f"TPT account at/below drawdown floor ${TPT_DRAWDOWN_FLOOR:,.0f}"}

    sizing = calculate_tpt_position_size(instrument, sl_ticks)
    if "error" in sizing:
        return {"allowed": False, "reason": sizing["error"]}

    return {
        "allowed":      True,
        "contracts":    sizing["contracts"],
        "risk_dollars": sizing["risk_dollars"],
        "sl_ticks":     sizing["sl_ticks"],
    }


def record_tpt_result(won: bool):
    """Call this after each TPT trade closes."""
    if won:
        tpt_state["consecutive_losses"] = 0
        tpt_state["killed"] = False
        tpt_state["daily_wins"] += 1
    else:
        tpt_state["consecutive_losses"] += 1
        tpt_state["daily_losses"] += 1
        if tpt_state["consecutive_losses"] >= MAX_CONSEC_LOSSES:
            tpt_state["killed"] = True
    _save_state()


def record_tpt_signal(payload: dict):
    """Store the last incoming Pine Script payload so dashboard can show live levels."""
    from datetime import datetime
    import pytz
    ct = pytz.timezone("America/Chicago")
    tpt_state["daily_signals"] += 1
    tpt_state["last_signal"] = {
        "time":        datetime.now(ct).strftime("%H:%M"),
        "instrument":  payload.get("instrument", payload.get("symbol", "MNQ")),
        "direction":   payload.get("direction", ""),
        "price":       payload.get("price", payload.get("entry_price")),
        "sl":          payload.get("sl", payload.get("stop_loss")),
        "tp1":         payload.get("tp1"),
        "tp2":         payload.get("tp2"),
        "contracts":   payload.get("contracts"),
        "retrace_pts": payload.get("retrace_pts"),
        "atr":         payload.get("atr"),
        "rr":          payload.get("rr_to_tp1"),
        "bos_level":   payload.get("bos_level"),
    }
    _save_state()


def record_tpt_trade(trade: dict):
    """Append completed trade to rolling log (max 20 kept)."""
    tpt_state["trades"].append(trade)
    tpt_state["trades"] = tpt_state["trades"][-20:]  # keep last 20 only
    _save_state()


def record_ftmo_signal():
    """Increment FTMO daily signal count."""
    ftmo_state["daily_signals"] += 1
    _save_state()


def record_ftmo_trade(trade: dict):
    """Append completed FTMO trade to rolling log (max 20 kept)."""
    ftmo_state["trades"].append(trade)
    ftmo_state["trades"] = ftmo_state["trades"][-20:]
    _save_state()


def reset_tpt_daily():
    """Called at midnight CT by scheduler."""
    tpt_state["consecutive_losses"] = 0
    tpt_state["killed"]             = False
    tpt_state["daily_pnl"]          = 0.0
    tpt_state["daily_signals"]      = 0
    tpt_state["daily_wins"]         = 0
    tpt_state["daily_losses"]       = 0
    _save_state()


def reset_ftmo_daily():
    """Called at midnight CT by scheduler."""
    ftmo_state["consecutive_losses"] = 0
    ftmo_state["daily_pnl"]          = 0.0
    ftmo_state["daily_signals"]      = 0
    ftmo_state["daily_wins"]         = 0
    ftmo_state["daily_losses"]       = 0
    _save_state()


def check_ftmo_risk(instrument: str, sl_pips: int = None) -> dict:
    """Gate check before allowing an FTMO signal through."""
    cfg = FTMO_INSTRUMENT_CONFIG.get(instrument.upper())
    if not cfg:
        return {"allowed": False, "reason": f"Unknown FTMO instrument: {instrument}"}

    sl = sl_pips or cfg["default_sl_pips"]
    risk_dollars = ftmo_state["account_balance"] * RISK_PER_TRADE
    lot_size = round(risk_dollars / (sl * cfg["pip_value"]), 2)
    lot_size = max(0.01, lot_size)

    return {
        "allowed":      True,
        "lot_size":     lot_size,
        "risk_dollars": round(risk_dollars, 2),
        "sl_pips":      sl,
    }


def update_ftmo_outcome(won: bool, pnl: float = None):
    """Call this after each FTMO trade closes."""
    if pnl is not None:
        ftmo_state["daily_pnl"] += pnl
        ftmo_state["account_balance"] += pnl
    if won:
        ftmo_state["consecutive_losses"] = 0
        ftmo_state["daily_wins"] += 1
    else:
        ftmo_state["consecutive_losses"] += 1
        ftmo_state["daily_losses"] += 1
    _save_state()
