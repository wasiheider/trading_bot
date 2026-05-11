# =============================================================================
# RISK.PY — PROP FIRM RISK ENGINE
# =============================================================================
# Hard guardrails for both Apex and FTMO accounts.
# This runs AFTER Claude validation and BEFORE any order execution.
# These rules are hardcoded and cannot be overridden by Claude or Pine Script.
#
# Apex rules:  daily loss limit, max drawdown, session gate, force-close time,
#              no-trade zone, max contracts, position sizing
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
# DAILY P&L TRACKER (in-memory — resets when server restarts)
# -----------------------------------------------------------------------------
# In Phase 6 this will be replaced with SQLite-backed tracking
# For now tracks P&L since server start

daily_pnl = {
    "APEX": 0.0,    # Running daily P&L in USD
    "FTMO": 0.0,
}

def update_daily_pnl(account: str, pnl: float):
    """Call this when a trade closes to update running P&L."""
    daily_pnl[account] += pnl
    log.info(f"[{account}] Daily P&L updated: ${daily_pnl[account]:.2f}")

def reset_daily_pnl(account: str):
    """Call this at session open to reset daily tracking."""
    daily_pnl[account] = 0.0
    log.info(f"[{account}] Daily P&L reset to $0.00")

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
    Based on: account balance × risk % / (stop distance × tick value)

    Returns: (contracts, dollar_risk)
    """
    stop_distance = abs(entry - stop_loss)

    # Tick values per contract (USD per point)
    tick_values = {
        "ES":  50.0,   # $50 per point
        "NQ":  20.0,   # $20 per point
        "MES":  5.0,   # $5 per point (micro)
        "MNQ":  2.0,   # $2 per point (micro)
        "CL": 1000.0,  # $1000 per point (crude oil)
        "NG":  10000.0,# $10000 per point (nat gas) — per MMBtu
        "GC":  100.0,  # $100 per point (gold)
        "SI":  5000.0, # $5000 per point (silver)
    }

    # Match symbol to tick value (strip contract month if present e.g. NQ1! -> NQ)
    base_symbol = ''.join(filter(str.isalpha, symbol.upper()))
    tick_value = tick_values.get(base_symbol, 20.0)  # default to NQ if unknown

    # Dollar risk allowed per trade
    dollar_risk_allowed = config.APEX_ACCOUNT_BALANCE * (config.APEX_RISK_PER_TRADE_PCT / 100)

    # Contracts = dollar risk / (stop distance × tick value)
    if stop_distance == 0 or tick_value == 0:
        log.warning(f"[APEX] Cannot calculate contracts — zero stop distance or tick value")
        return 0, 0.0

    raw_contracts = dollar_risk_allowed / (stop_distance * tick_value)
    contracts = max(1, int(raw_contracts))  # minimum 1, round down
    contracts = min(contracts, config.APEX_MAX_CONTRACTS)  # cap at max

    actual_dollar_risk = contracts * stop_distance * tick_value

    log.info(
        f"[APEX] Position sizing: {contracts} contract(s) | "
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
    Calculate lot size for FTMO forex/metals trade.
    Based on: account balance × risk % / (stop pips × pip value)

    Returns: (lots, dollar_risk)
    """
    stop_distance = abs(entry - stop_loss)

    # Pip values per standard lot (USD)
    pip_values = {
        "EURUSD": 10.0,
        "GBPUSD": 10.0,
        "XAUUSD": 1.0,   # Gold: $1 per 0.01 move per lot = $10 per pip
        "XAGUSD": 50.0,  # Silver: $50 per pip per lot
    }

    # Pip sizes (how much price moves = 1 pip)
    pip_sizes = {
        "EURUSD": 0.0001,
        "GBPUSD": 0.0001,
        "XAUUSD": 0.10,   # Gold pip = $0.10
        "XAGUSD": 0.01,
    }

    base_symbol = symbol.upper().replace("/", "")
    pip_value = pip_values.get(base_symbol, 10.0)
    pip_size  = pip_sizes.get(base_symbol, 0.0001)

    # Convert stop distance to pips
    stop_pips = stop_distance / pip_size

    # Dollar risk allowed
    dollar_risk_allowed = config.FTMO_ACCOUNT_BALANCE * (config.FTMO_RISK_PER_TRADE_PCT / 100)

    # Lots = dollar risk / (stop pips × pip value per lot)
    if stop_pips == 0:
        log.warning(f"[FTMO] Cannot calculate lots — zero stop distance")
        return 0.0, 0.0

    raw_lots = dollar_risk_allowed / (stop_pips * pip_value)
    lots = round(raw_lots, 2)
    lots = max(0.01, lots)  # minimum 0.01 lots

    actual_dollar_risk = lots * stop_pips * pip_value

    log.info(
        f"[FTMO] Position sizing: {lots} lot(s) | "
        f"Stop: {stop_pips:.1f} pips | "
        f"Risk: ${actual_dollar_risk:.2f} / ${dollar_risk_allowed:.2f} allowed"
    )

    return lots, actual_dollar_risk


# -----------------------------------------------------------------------------
# APEX RISK CHECKS
# -----------------------------------------------------------------------------

def check_apex_session(now_ct: datetime) -> Tuple[bool, str]:
    """Check if current time falls within an allowed Apex trading session."""
    current_time = now_ct.time()

    for session in config.APEX_SESSIONS:
        start = time(*map(int, session["start"].split(":")))
        end   = time(*map(int, session["end"].split(":")))
        if start <= current_time <= end:
            return True, session["name"]

    return False, "outside session"


def check_apex_no_trade_zone(now_ct: datetime) -> bool:
    """Returns True if we are in the Apex no-trade zone (3pm-5pm CT)."""
    current_time = now_ct.time()
    no_trade_start = time(*map(int, config.APEX_NO_TRADE_START.split(":")))
    no_trade_end   = time(*map(int, config.APEX_NO_TRADE_END.split(":")))
    return no_trade_start <= current_time <= no_trade_end


def check_apex_force_close_approaching(now_ct: datetime) -> bool:
    """Returns True if within 15 minutes of the Apex force-close time."""
    current_time = now_ct.time()
    force_h, force_m = map(int, config.APEX_FORCE_CLOSE_TIME.split(":"))
    force_close = time(force_h, force_m)

    # Block new entries if within 15 min of force close
    block_from = time(force_h, max(0, force_m - 15))
    return block_from <= current_time <= force_close


def check_apex_daily_loss(now_ct: datetime) -> Tuple[bool, str]:
    """Check if Apex daily loss limit has been breached."""
    current_loss = daily_pnl["APEX"]
    limit = -abs(config.APEX_DAILY_LOSS_LIMIT_USD)

    if current_loss <= limit:
        msg = f"Daily loss limit breached: ${current_loss:.2f} (limit: ${limit:.2f})"
        log.warning(f"[APEX] RISK BLOCK — {msg}")
        return False, msg

    remaining = abs(limit) - abs(current_loss)
    return True, f"Daily loss OK — ${remaining:.2f} remaining buffer"


def run_apex_risk_checks(signal, test_mode: bool = False) -> Tuple[bool, str, dict]:
    """
    Run all Apex risk checks in order.
    Returns: (passed, reason, sizing_info)
    """
    now_ct = datetime.now(CT)
    log.info(f"[APEX] Running risk checks at {now_ct.strftime('%H:%M:%S CT')}")

    # 1 — No-trade zone check
    if check_apex_no_trade_zone(now_ct):
        return False, f"No-trade zone active ({config.APEX_NO_TRADE_START}-{config.APEX_NO_TRADE_END} CT)", {}

    # 2 — Force close approaching
    if check_apex_force_close_approaching(now_ct):
        return False, f"Too close to force-close time ({config.APEX_FORCE_CLOSE_TIME} CT) — no new entries", {}

    # 3 — Session check
    if test_mode:
        session_name = "TestMode"
        log.warning(f"[APEX] TEST MODE — session check bypassed")
    else:
        in_session, session_name = check_apex_session(now_ct)
        if not in_session:
            return False, f"Outside allowed sessions — current time {now_ct.strftime('%H:%M CT')} not in London or NY window", {}

    log.info(f"[APEX] Session: {session_name} — OK")

    # 4 — Daily loss check
    loss_ok, loss_msg = check_apex_daily_loss(now_ct)
    if not loss_ok:
        return False, loss_msg, {}

    log.info(f"[APEX] {loss_msg}")

    # 5 — Position sizing
    contracts, dollar_risk = calculate_apex_contracts(
        signal.symbol, signal.entry_price, signal.stop_loss
    )

    if contracts == 0:
        return False, "Position size calculated as 0 contracts — check account balance and stop distance", {}

    sizing = {
        "contracts": contracts,
        "dollar_risk": dollar_risk,
        "session": session_name,
    }

    log.info(f"[APEX] Risk checks PASSED — {contracts} contract(s) | ${dollar_risk:.2f} risk")
    return True, "All Apex risk checks passed", sizing


# -----------------------------------------------------------------------------
# FTMO RISK CHECKS
# -----------------------------------------------------------------------------

def get_upcoming_news(currencies: list) -> list:
    """
    Fetch upcoming high-impact news events from ForexFactory calendar.
    Returns list of events within the next 30 minutes.
    Simple implementation — returns empty list if API unavailable.
    """
    try:
        # ForexFactory JSON feed
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

            # Parse event time
            try:
                event_time_str = event.get("date", "")
                if not event_time_str:
                    continue

                event_time = datetime.fromisoformat(event_time_str.replace("Z", "+00:00"))
                event_time_ct = event_time.astimezone(CT)

                # Check if event is within next 30 minutes
                minutes_until = (event_time_ct - now_ct).total_seconds() / 60

                if -5 <= minutes_until <= config.FTMO_CLOSE_BEFORE_NEWS_MINS:
                    upcoming.append({
                        "title": event.get("title", "Unknown"),
                        "currency": event.get("currency"),
                        "impact": event.get("impact"),
                        "minutes_until": round(minutes_until, 1),
                        "time_ct": event_time_ct.strftime("%H:%M CT"),
                    })
            except Exception:
                continue

        return upcoming

    except Exception as e:
        log.warning(f"[FTMO] News calendar error: {e} — skipping news filter")
        return []


def get_symbol_currencies(symbol: str) -> list:
    """Get the currencies associated with a symbol for news filtering."""
    currency_map = {
        "EURUSD": ["EUR", "USD"],
        "GBPUSD": ["GBP", "USD"],
        "XAUUSD": ["XAU", "USD"],
        "XAGUSD": ["XAG", "USD"],
    }
    return currency_map.get(symbol.upper(), ["USD"])


def check_ftmo_daily_loss() -> Tuple[bool, str]:
    """Check if FTMO daily loss limit has been breached."""
    current_loss = daily_pnl["FTMO"]
    limit_usd = -(config.FTMO_ACCOUNT_BALANCE * config.FTMO_DAILY_LOSS_LIMIT_PCT / 100)

    if current_loss <= limit_usd:
        msg = f"Daily loss limit breached: ${current_loss:.2f} (limit: ${limit_usd:.2f})"
        log.warning(f"[FTMO] RISK BLOCK — {msg}")
        return False, msg

    remaining = abs(limit_usd) - abs(current_loss)
    return True, f"Daily loss OK — ${remaining:.2f} remaining buffer"


def run_ftmo_risk_checks(signal) -> Tuple[bool, str, dict]:
    """
    Run all FTMO risk checks in order.
    Returns: (passed, reason, sizing_info)
    """
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
        msg = f"High-impact news approaching — blocking trade. Events: {news_list}"
        log.warning(f"[FTMO] RISK BLOCK — {msg}")
        return False, msg, {}

    log.info(f"[FTMO] News filter — no high-impact events in next {config.FTMO_CLOSE_BEFORE_NEWS_MINS} mins")

    # 3 — Position sizing
    lots, dollar_risk = calculate_ftmo_lots(
        signal.symbol, signal.entry_price, signal.stop_loss
    )

    if lots == 0:
        return False, "Position size calculated as 0 lots — check account balance and stop distance", {}

    sizing = {
        "lots": lots,
        "dollar_risk": dollar_risk,
    }

    log.info(f"[FTMO] Risk checks PASSED — {lots} lot(s) | ${dollar_risk:.2f} risk")
    return True, "All FTMO risk checks passed", sizing


# -----------------------------------------------------------------------------
# MAIN ENTRY POINT
# -----------------------------------------------------------------------------

def run_risk_checks(signal) -> Tuple[bool, str, dict]:
    """
    Run the appropriate risk checks for the signal's account.
    Called from server.py after Claude validation passes.

    Returns:
        passed  (bool)  — True if all checks pass
        reason  (str)   — Description of result or failure reason
        sizing  (dict)  — Position sizing info (contracts or lots)
    """
    test_mode = getattr(signal, "test_mode", False)
    if signal.account == "APEX":
        return run_apex_risk_checks(signal, test_mode=test_mode)
    else:
        return run_ftmo_risk_checks(signal)
