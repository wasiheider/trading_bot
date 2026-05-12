# =============================================================================
# RISK.PY — PROP FIRM RISK ENGINE
# =============================================================================
# Hard guardrails for both Apex and FTMO accounts.
# This runs AFTER Claude validation and BEFORE any order execution.
#
# Apex rules:  daily loss limit, max drawdown, session gate, force-close time,
#              no-trade zone, max contracts, position sizing,
#              HARD STOP after 2 consecutive losses
# FTMO rules:  daily loss limit, max drawdown, news pre-close, lot sizing
# =============================================================================

import logging
from datetime import datetime, time
from typing import Tuple
import pytz
import requests

import config

log = logging.getLogger(__name__)

# -----------------------------------------------------------------------------
# TIMEZONE
# -----------------------------------------------------------------------------
CT = pytz.timezone("America/Chicago")

# -----------------------------------------------------------------------------
# DAILY P&L TRACKER (in-memory — resets at midnight CT via main.py scheduler)
# -----------------------------------------------------------------------------
daily_pnl = {
    "APEX": 0.0,
    "FTMO": 0.0,
}

# -----------------------------------------------------------------------------
# CONSECUTIVE LOSS TRACKER — APEX ONLY
# -----------------------------------------------------------------------------
# Tracks back-to-back losses for the current trading day.
# Hard stop fires after 2 consecutive losses.
# Resets: midnight CT, or when a winning trade closes.

consecutive_losses = {
    "APEX": 0,
}

APEX_MAX_CONSECUTIVE_LOSSES = 2

def record_trade_result(account: str, pnl: float):
    """
    Call this when a trade closes.
    Updates daily P&L and consecutive loss counter.
    """
    daily_pnl[account] += pnl
    log.info(f"[{account}] Trade closed: ${pnl:.2f} | Daily P&L: ${daily_pnl[account]:.2f}")

    if account == "APEX":
        if pnl < 0:
            consecutive_losses["APEX"] += 1
            log.warning(
                f"[APEX] Loss recorded — consecutive losses: "
                f"{consecutive_losses['APEX']}/{APEX_MAX_CONSECUTIVE_LOSSES}"
            )
        else:
            # Winning trade resets the counter
            if consecutive_losses["APEX"] > 0:
                log.info(f"[APEX] Winning trade — consecutive loss counter reset to 0")
            consecutive_losses["APEX"] = 0

def update_daily_pnl(account: str, pnl: float):
    """Legacy helper — use record_trade_result instead."""
    daily_pnl[account] += pnl
    log.info(f"[{account}] Daily P&L updated: ${daily_pnl[account]:.2f}")

def reset_daily_pnl(account: str = None):
    """Reset daily P&L at session open. Called by main.py scheduler."""
    if account:
        daily_pnl[account] = 0.0
        log.info(f"[{account}] Daily P&L reset to $0.00")
    else:
        for acc in daily_pnl:
            daily_pnl[acc] = 0.0
        log.info("[BOT] All daily P&L reset to $0.00")

def reset_consecutive_losses(account: str = "APEX"):
    """Reset consecutive loss counter. Called by main.py at midnight."""
    if account in consecutive_losses:
        consecutive_losses[account] = 0
        log.info(f"[{account}] Consecutive loss counter reset to 0")

def get_consecutive_losses(account: str = "APEX") -> int:
    return consecutive_losses.get(account, 0)

# -----------------------------------------------------------------------------
# POSITION SIZING
# -----------------------------------------------------------------------------

def calculate_apex_contracts(
    symbol: str,
    entry: float,
    stop_loss: float,
) -> Tuple[int, float]:
    """
    Calculate number of contracts for Apex futures trade.
    Risk per trade: APEX_RISK_PER_TRADE_PCT (0.25%) of account balance.
    """
    stop_distance = abs(entry - stop_loss)

    tick_values = {
        "ES":   50.0,
        "NQ":   20.0,
        "MES":   5.0,
        "MNQ":   2.0,
        "CL": 1000.0,
        "MCL":  100.0,
        "NG": 10000.0,
        "GC":  100.0,
        "MGC":  10.0,
        "SI": 5000.0,
    }

    base_symbol = ''.join(filter(str.isalpha, symbol.upper()))
    tick_value = tick_values.get(base_symbol, 20.0)

    dollar_risk_allowed = config.APEX_ACCOUNT_BALANCE * (config.APEX_RISK_PER_TRADE_PCT / 100)

    if stop_distance == 0 or tick_value == 0:
        log.warning(f"[APEX] Cannot calculate contracts — zero stop distance or tick value")
        return 0, 0.0

    raw_contracts = dollar_risk_allowed / (stop_distance * tick_value)
    contracts = max(1, int(raw_contracts))
    contracts = min(contracts, config.APEX_MAX_CONTRACTS)

    actual_dollar_risk = contracts * stop_distance * tick_value

    log.info(
        f"[APEX] Sizing: {contracts} contract(s) | "
        f"Stop: {stop_distance} pts | Tick: ${tick_value} | "
        f"Risk: ${actual_dollar_risk:.2f} / ${dollar_risk_allowed:.2f} allowed"
    )

    return contracts, actual_dollar_risk


def calculate_ftmo_lots(
    symbol: str,
    entry: float,
    stop_loss: float,
) -> Tuple[float, float]:
    """
    Calculate lot size for FTMO forex/metals/indices trade.
    Risk per trade: FTMO_RISK_PER_TRADE_PCT (0.5%) of account balance.
    """
    stop_distance = abs(entry - stop_loss)

    pip_values = {
        "EURUSD": 10.0,
        "GBPUSD": 10.0,
        "USDJPY": 9.0,
        "EURNZD": 10.0,
        "XAUUSD": 10.0,
        "USOIL":  10.0,
        "US100":   1.0,
    }

    pip_sizes = {
        "EURUSD": 0.0001,
        "GBPUSD": 0.0001,
        "USDJPY": 0.01,
        "EURNZD": 0.0001,
        "XAUUSD": 0.10,
        "USOIL":  0.01,
        "US100":  1.0,
    }

    base_symbol = symbol.upper().replace("/", "")
    pip_value = pip_values.get(base_symbol, 10.0)
    pip_size  = pip_sizes.get(base_symbol, 0.0001)

    stop_pips = stop_distance / pip_size

    dollar_risk_allowed = config.FTMO_ACCOUNT_BALANCE * (config.FTMO_RISK_PER_TRADE_PCT / 100)

    if stop_pips == 0:
        log.warning(f"[FTMO] Cannot calculate lots — zero stop distance")
        return 0.0, 0.0

    raw_lots = dollar_risk_allowed / (stop_pips * pip_value)
    lots = round(raw_lots, 2)
    lots = max(0.01, lots)

    actual_dollar_risk = lots * stop_pips * pip_value

    log.info(
        f"[FTMO] Sizing: {lots} lot(s) | "
        f"Stop: {stop_pips:.1f} pips | "
        f"Risk: ${actual_dollar_risk:.2f} / ${dollar_risk_allowed:.2f} allowed"
    )

    return lots, actual_dollar_risk


# -----------------------------------------------------------------------------
# APEX RISK CHECKS
# -----------------------------------------------------------------------------

def check_apex_session(now_ct: datetime) -> Tuple[bool, str]:
    """Check if current time is within an allowed Apex trading session."""
    current_time = now_ct.time()
    for session in config.APEX_SESSIONS:
        start = time(*map(int, session["start"].split(":")))
        end   = time(*map(int, session["end"].split(":")))
        if start <= current_time <= end:
            return True, session["name"]
    return False, "outside session"


def check_apex_no_trade_zone(now_ct: datetime) -> bool:
    """Returns True if in the Apex no-trade zone."""
    current_time   = now_ct.time()
    no_trade_start = time(*map(int, config.APEX_NO_TRADE_START.split(":")))
    no_trade_end   = time(*map(int, config.APEX_NO_TRADE_END.split(":")))
    return no_trade_start <= current_time <= no_trade_end


def check_apex_force_close_approaching(now_ct: datetime) -> bool:
    """Returns True if within 15 minutes of the Apex force-close time."""
    current_time = now_ct.time()
    force_h, force_m = map(int, config.APEX_FORCE_CLOSE_TIME.split(":"))
    block_from = time(force_h, max(0, force_m - 15))
    force_close = time(force_h, force_m)
    return block_from <= current_time <= force_close


def check_apex_daily_loss() -> Tuple[bool, str]:
    """Check if Apex daily loss limit has been breached."""
    current_loss = daily_pnl["APEX"]
    limit = -abs(config.APEX_DAILY_LOSS_LIMIT_USD)
    if current_loss <= limit:
        msg = f"Daily loss limit breached: ${current_loss:.2f} (limit: ${limit:.2f})"
        log.warning(f"[APEX] RISK BLOCK — {msg}")
        return False, msg
    remaining = abs(limit) - abs(current_loss)
    return True, f"Daily loss OK — ${remaining:.2f} remaining buffer"


def check_apex_consecutive_losses() -> Tuple[bool, str]:
    """
    Hard stop after 2 consecutive losses.
    Returns (False, reason) if limit reached.
    """
    losses = consecutive_losses["APEX"]
    if losses >= APEX_MAX_CONSECUTIVE_LOSSES:
        msg = (
            f"Hard stop — {losses} consecutive losses today. "
            f"No new Apex trades until tomorrow."
        )
        log.warning(f"[APEX] RISK BLOCK — {msg}")
        return False, msg
    remaining = APEX_MAX_CONSECUTIVE_LOSSES - losses
    return True, f"Consecutive losses: {losses}/{APEX_MAX_CONSECUTIVE_LOSSES} — {remaining} remaining"


def run_apex_risk_checks(signal, test_mode: bool = False) -> Tuple[bool, str, dict]:
    """
    Run all Apex risk checks in order.
    Returns: (passed, reason, sizing_info)
    """
    now_ct = datetime.now(CT)
    log.info(f"[APEX] Running risk checks at {now_ct.strftime('%H:%M:%S CT')}")

    # 1 — Consecutive loss hard stop
    consec_ok, consec_msg = check_apex_consecutive_losses()
    if not consec_ok:
        return False, consec_msg, {}
    log.info(f"[APEX] {consec_msg}")

    # 2 — No-trade zone
    if check_apex_no_trade_zone(now_ct):
        return False, f"No-trade zone active ({config.APEX_NO_TRADE_START}-{config.APEX_NO_TRADE_END} CT)", {}

    # 3 — Force close approaching
    if check_apex_force_close_approaching(now_ct):
        return False, f"Too close to force-close time ({config.APEX_FORCE_CLOSE_TIME} CT) — no new entries", {}

    # 4 — Session check
    if test_mode:
        session_name = "TestMode"
        log.warning(f"[APEX] TEST MODE — session check bypassed")
    else:
        in_session, session_name = check_apex_session(now_ct)
        if not in_session:
            return False, f"Outside allowed sessions — {now_ct.strftime('%H:%M CT')} not in London or NY window", {}

    log.info(f"[APEX] Session: {session_name} — OK")

    # 5 — Daily loss check
    loss_ok, loss_msg = check_apex_daily_loss()
    if not loss_ok:
        return False, loss_msg, {}
    log.info(f"[APEX] {loss_msg}")

    # 6 — Position sizing
    contracts, dollar_risk = calculate_apex_contracts(
        signal.symbol, signal.entry_price, signal.stop_loss
    )
    if contracts == 0:
        return False, "Position size calculated as 0 contracts — check stop distance", {}

    sizing = {
        "contracts":   contracts,
        "dollar_risk": dollar_risk,
        "session":     session_name,
        "consecutive_losses": consecutive_losses["APEX"],
    }

    log.info(f"[APEX] Risk checks PASSED — {contracts} contract(s) | ${dollar_risk:.2f} risk")
    return True, "All Apex risk checks passed", sizing


# -----------------------------------------------------------------------------
# FTMO RISK CHECKS
# -----------------------------------------------------------------------------

def get_upcoming_news(currencies: list) -> list:
    """Fetch upcoming high-impact news from ForexFactory calendar."""
    try:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        response = requests.get(url, timeout=5)
        if response.status_code != 200:
            log.warning("[FTMO] Could not fetch news calendar — skipping news filter")
            return []

        events = response.json()
        now_ct = datetime.now(CT)
        upcoming = []

        for event in events:
            if event.get("impact") not in ["High", "Medium"]:
                continue
            if event.get("currency") not in currencies:
                continue
            try:
                event_time_str = event.get("date", "")
                if not event_time_str:
                    continue
                event_time = datetime.fromisoformat(event_time_str.replace("Z", "+00:00"))
                event_time_ct = event_time.astimezone(CT)
                minutes_until = (event_time_ct - now_ct).total_seconds() / 60
                if -5 <= minutes_until <= config.FTMO_CLOSE_BEFORE_NEWS_MINS:
                    upcoming.append({
                        "title":         event.get("title", "Unknown"),
                        "currency":      event.get("currency"),
                        "impact":        event.get("impact"),
                        "minutes_until": round(minutes_until, 1),
                        "time_ct":       event_time_ct.strftime("%H:%M CT"),
                    })
            except Exception:
                continue

        return upcoming

    except Exception as e:
        log.warning(f"[FTMO] News calendar error: {e} — skipping news filter")
        return []


def get_symbol_currencies(symbol: str) -> list:
    currency_map = {
        "EURUSD": ["EUR", "USD"],
        "GBPUSD": ["GBP", "USD"],
        "USDJPY": ["USD", "JPY"],
        "EURNZD": ["EUR", "NZD"],
        "XAUUSD": ["XAU", "USD"],
        "USOIL":  ["USD"],
        "US100":  ["USD"],
    }
    return currency_map.get(symbol.upper(), ["USD"])


def check_ftmo_daily_loss() -> Tuple[bool, str]:
    current_loss = daily_pnl["FTMO"]
    limit_usd = -(config.FTMO_ACCOUNT_BALANCE * config.FTMO_DAILY_LOSS_LIMIT_PCT / 100)
    if current_loss <= limit_usd:
        msg = f"Daily loss limit breached: ${current_loss:.2f} (limit: ${limit_usd:.2f})"
        log.warning(f"[FTMO] RISK BLOCK — {msg}")
        return False, msg
    remaining = abs(limit_usd) - abs(current_loss)
    return True, f"Daily loss OK — ${remaining:.2f} remaining buffer"


def run_ftmo_risk_checks(signal) -> Tuple[bool, str, dict]:
    """Run all FTMO risk checks. Returns: (passed, reason, sizing_info)"""
    now_ct = datetime.now(CT)
    log.info(f"[FTMO] Running risk checks at {now_ct.strftime('%H:%M:%S CT')}")

    # 1 — Daily loss check
    loss_ok, loss_msg = check_ftmo_daily_loss()
    if not loss_ok:
        return False, loss_msg, {}
    log.info(f"[FTMO] {loss_msg}")

    # 2 — News filter
    currencies = get_symbol_currencies(signal.symbol)
    upcoming_news = get_upcoming_news(currencies)
    if upcoming_news:
        news_list = ", ".join([f"{e['title']} ({e['time_ct']})" for e in upcoming_news])
        return False, f"High-impact news approaching — blocking trade. Events: {news_list}", {}
    log.info(f"[FTMO] News filter — clear for next {config.FTMO_CLOSE_BEFORE_NEWS_MINS} mins")

    # 3 — Position sizing
    lots, dollar_risk = calculate_ftmo_lots(
        signal.symbol, signal.entry_price, signal.stop_loss
    )
    if lots == 0:
        return False, "Position size calculated as 0 lots — check stop distance", {}

    sizing = {
        "lots":        lots,
        "dollar_risk": dollar_risk,
    }

    log.info(f"[FTMO] Risk checks PASSED — {lots} lot(s) | ${dollar_risk:.2f} risk")
    return True, "All FTMO risk checks passed", sizing


# -----------------------------------------------------------------------------
# MAIN ENTRY POINT
# -----------------------------------------------------------------------------

def run_risk_checks(signal) -> Tuple[bool, str, dict]:
    """
    Route to the correct risk engine based on account.
    Called from server.py after Claude validation passes.
    """
    test_mode = getattr(signal, "test_mode", False)
    if signal.account == "APEX":
        return run_apex_risk_checks(signal, test_mode=test_mode)
    else:
        return run_ftmo_risk_checks(signal)
