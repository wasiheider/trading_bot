from config import (
    RISK_PER_TRADE,
    MAX_CONSEC_LOSSES,
    TPT_ACCOUNT_SIZE,
    TPT_MAX_CONTRACTS,
    TPT_DRAWDOWN_FLOOR,
    FTMO_ACCOUNT_SIZE,
)

# ── State tracking ─────────────────────────────────────────
tpt_state = {
    "consecutive_losses": 0,
    "killed":             False,
    "daily_pnl":          0.0,
    "account_balance":    TPT_ACCOUNT_SIZE,
}

ftmo_state = {
    "daily_pnl":          0.0,
    "consecutive_losses": 0,
    "account_balance":    FTMO_ACCOUNT_SIZE,
}

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

    # Hard kill switch (2 consecutive losses)
    if tpt_state["killed"]:
        return {"allowed": False, "reason": "TPT hard stop — 2 consecutive losses. Resets at midnight CT."}

    # Drawdown floor check
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
    else:
        tpt_state["consecutive_losses"] += 1
        if tpt_state["consecutive_losses"] >= MAX_CONSEC_LOSSES:
            tpt_state["killed"] = True


def reset_tpt_daily():
    """Called at midnight CT by scheduler."""
    tpt_state["consecutive_losses"] = 0
    tpt_state["killed"]             = False
    tpt_state["daily_pnl"]          = 0.0


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


def update_ftmo_outcome(won: bool, pnl: float = None) -> None:
    """
    Phase 8 — Called when TradingView fires tp1_hit, tp2_hit, or sl_hit.
    Updates FTMO daily P&L and consecutive loss counter.
    Note: TP1 and TP2 both count as wins — counter resets on either.
    """
    if pnl is not None:
        ftmo_state["daily_pnl"] += pnl

    if won:
        ftmo_state["consecutive_losses"] = 0
    else:
        ftmo_state["consecutive_losses"] += 1


def reset_ftmo_daily():
    """Called at midnight CT by scheduler."""
    ftmo_state["daily_pnl"]          = 0.0
    ftmo_state["consecutive_losses"] = 0
