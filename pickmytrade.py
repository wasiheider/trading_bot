"""
pickmytrade.py -- relay entry signals to PickMyTrade's webhook API for the
Apex Trader Funding evaluation account (via Tradovate). Completely separate
real-money account from OANDA/FTMO -- no shared state with risk.py/paper_state,
no daily/weekly limit coupling.

Broker-managed exit only: single TP1 target + SL set at order placement time.
No partial-close/breakeven/trailing here, unlike the FTMO MQL5 EA -- PickMyTrade's
dashboard doesn't expose that granularity (one TP field, not a TP1+TP2 split).
TP1 was chosen deliberately over TP2 for this account: closer target, less
floating exposure time, safer given Apex's Intraday trailing drawdown (which
trails the highest equity ever reached, including floating gains, and never
resets).

NOT YET WIRED into the live signal path (server.py) as of 2026-07-05. Two
assumptions are unverified against the real API:
  1. Whether `tp`/`sl` are interpreted as absolute price or point-distance --
     the Generate Alert wizard's "mode" selector didn't appear as an explicit
     field in the generated JSON, so it may be stored server-side against the
     token rather than passed per-request. MUST verify with a real placed
     order before trusting this with live signals.
  2. The exact `date` field format expected outside of TradingView's own
     "{{timenow}}" placeholder substitution -- sending ISO 8601 UTC as a
     reasonable default, unverified.
Test manually once the Apex account activates, confirm the resulting order's
price/SL/TP in Tradovate matches expectations, THEN wire send_entry() into
server.py's signal handling.
"""
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

from config import PICKMYTRADE_TOKEN, PICKMYTRADE_ACCOUNT_ID

PICKMYTRADE_URL = "https://api.pickmytrade.trade/v2/add-trade-data"

# Bot instrument -> TradingView continuous-contract symbol (confirmed format: MNQ1!)
PICKMYTRADE_SYMBOL_MAP = {
    "MNQ": "MNQ1!",
    "MES": "MES1!",
    "MYM": "MYM1!",
    "MGC": "MGC1!",
    "MCL": "MCL1!",
}

RISK_PERCENTAGE = 0.5  # 0.5% of account balance per trade, matches the paper bot's convention


def _request(body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        PICKMYTRADE_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"PickMyTrade POST failed => HTTP {e.code}: {e.read().decode()}")


def send_entry(instrument: str, direction: str, setup: str, price: float, sl: float, tp1: float) -> dict:
    """
    Relay an entry signal to PickMyTrade/Apex. Uses TP1 as the single take-profit
    target (see module docstring for why). Raises RuntimeError/ValueError on
    failure -- caller decides how to handle/log/notify.
    """
    symbol = PICKMYTRADE_SYMBOL_MAP.get(instrument.upper())
    if not symbol:
        raise ValueError(f"No PickMyTrade symbol mapping for: {instrument}")

    order_type = "STP" if setup == "box_break" else "LMT"

    body = {
        "strategy_name": "",
        "symbol": symbol,
        "date": datetime.now(timezone.utc).isoformat(),
        "data": "buy" if direction.upper() == "LONG" else "sell",
        "quantity": 0,
        "risk_percentage": RISK_PERCENTAGE,
        "price": price,
        "stp_limit_stp_price": 0,
        "tp": tp1,
        "percentage_tp": 0,
        "dollar_tp": 0,
        "sl": sl,
        "percentage_sl": 0,
        "dollar_sl": 0,
        "trail": 0,
        "trail_stop": 0,
        "trail_trigger": 0,
        "trail_freq": 0,
        "update_tp": False,
        "update_sl": False,
        "breakeven": 0,
        "breakeven_offset": 0,
        "token": PICKMYTRADE_TOKEN,
        "pyramid": False,
        "same_direction_ignore": False,
        "reverse_order_close": True,
        "order_type": order_type,
        "multiple_accounts": [
            {
                "token": PICKMYTRADE_TOKEN,
                "account_id": PICKMYTRADE_ACCOUNT_ID,
                "risk_percentage": RISK_PERCENTAGE,
                "quantity_multiplier": 0,
            }
        ],
    }

    return _request(body)
