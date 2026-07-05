"""
pickmytrade.py -- relay entry signals to PickMyTrade's webhook API for the
Apex Trader Funding evaluation account (via Tradovate). Completely separate
real-money account from OANDA/FTMO -- no shared state with risk.py/paper_state,
no daily/weekly limit coupling.

Exit management (revised 2026-07-05): single TP target set at the strategy's
1:3 RR level (tp2, not the closer tp1) -- PickMyTrade only supports one TP
field, not the paper bot's TP1(50% close)+TP2(remainder) split. Using the
farther TP2 target is deliberately compensated for with an early breakeven
(0.5R) and continuous trailing every 0.5R afterward, rather than waiting
for a full 1R like the FTMO EA does -- this manages floating exposure along
the way to the farther target, which matters given Apex's Intraday trailing
drawdown (trails the highest equity ever reached, including floating gains,
and never resets -- punishes floating exposure time hardest of anything in
this project).

Risk: 0.25% per trade ($625 on the $250K account) -- reduced from an initial
0.5%/$1,250 plan once the daily-loss guardrail was added; see project memory
for the full reasoning on why aggressive-but-guardrailed was chosen for this
account specifically (different risk profile than FTMO -- multiple cheap
Apex accounts run in parallel rather than one nursed conservatively).

NOT YET WIRED into the live signal path (server.py) as of 2026-07-05. Two
things remain to verify empirically with a real (deliberately unfillable)
test order before trusting this with live signals:
  1. Whether `tp`/`sl` are read as absolute price (this module's assumption,
     supported by PickMyTrade's own documented example showing tp populated
     with a literal price value) or something else -- the Generate Alert
     wizard's UI-level "mode" selector appears to be a wizard-only construct
     for TradingView-triggered alerts specifically, not a constraint on raw
     API calls, per PickMyTrade's own docs -- but not yet empirically proven
     against this specific account/token.
  2. Whether `breakeven_offset` should be 0 (move SL to exactly entry) or
     mirror `breakeven` (the wizard populated both fields identically from a
     single UI input, so it's unclear if they're independently meaningful).
     Currently set to 0 here.
Test send_entry() manually, confirm the resulting order's price/SL/TP/
breakeven/trailing behavior in Tradovate matches expectations, THEN wire it
into server.py's signal handling.
"""
import json
import urllib.request
import urllib.error
from datetime import datetime, timezone

from config import PICKMYTRADE_TOKEN, PICKMYTRADE_ACCOUNT_ID

PICKMYTRADE_URL = "https://api.pickmytrade.trade/v2/add-trade-data-latest?t=20255"

# Bot instrument -> TradingView continuous-contract symbol (confirmed format: MNQ1!)
PICKMYTRADE_SYMBOL_MAP = {
    "MNQ": "MNQ1!",
    "MES": "MES1!",
    "MYM": "MYM1!",
    "MGC": "MGC1!",
    "MCL": "MCL1!",
}

RISK_PERCENTAGE = 0.25  # 0.25% of account balance per trade ($625 on $250K)
TRAIL_STEP_R = 0.5      # breakeven trigger + trailing distance/trigger/frequency, all in units of R


def _request(body: dict) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        PICKMYTRADE_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"PickMyTrade POST failed => HTTP {e.code}: {e.read().decode()}")


def send_entry(instrument: str, direction: str, setup: str, price: float, sl: float, tp2: float) -> dict:
    """
    Relay an entry signal to PickMyTrade/Apex. `tp2` should be the strategy's
    1:3 RR take-profit level -- this is the single TP target sent (see module
    docstring for why TP2 rather than TP1). Raises RuntimeError/ValueError on
    failure -- caller decides how to handle/log/notify.
    """
    symbol = PICKMYTRADE_SYMBOL_MAP.get(instrument.upper())
    if not symbol:
        raise ValueError(f"No PickMyTrade symbol mapping for: {instrument}")

    order_type = "STP" if setup == "box_break" else "LMT"

    r_distance = abs(price - sl)
    trail_step = r_distance * TRAIL_STEP_R

    body = {
        "strategy_name": "",
        "symbol": symbol,
        "date": datetime.now(timezone.utc).isoformat(),
        "data": "buy" if direction.upper() == "LONG" else "sell",
        "quantity": 0,
        "risk_percentage": RISK_PERCENTAGE,
        "price": price,
        "stp_limit_stp_price": 0,
        "tp": tp2,
        "percentage_tp": 0,
        "dollar_tp": 0,
        "sl": sl,
        "percentage_sl": 0,
        "dollar_sl": 0,
        "trail": 1,
        "trail_stop": trail_step,
        "trail_trigger": trail_step,
        "trail_freq": trail_step,
        "update_tp": False,
        "update_sl": False,
        "breakeven": trail_step,
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
