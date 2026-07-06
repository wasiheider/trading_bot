"""
pickmytrade.py -- relay entry signals to PickMyTrade's webhook API for the
Apex Trader Funding evaluation account (via Tradovate). Completely separate
real-money account from OANDA/FTMO -- no shared state with risk.py/paper_state,
no daily/weekly limit coupling.

Order type (revised 2026-07-06): MARKET, not LIMIT/STOP. Entry/SL/TP for the
mirrored micro instruments (MNQ/MES/MYM/MGC/MCL) were originally computed by
copying the parent CFD instrument's (US100/US500/US30/XAUUSD/USOIL) absolute
price levels unchanged -- but the CFD price feed and the actual futures
price on Tradovate are two different, independently-priced markets. They can
diverge by a meaningful amount at any given moment (confirmed live 2026-07-06:
a mirrored MNQ signal carried US100's price of 29603.3 while MNQ was actually
trading ~245 points higher), which made a limit order at the copied price
invalid or wildly mispriced relative to MNQ's real market. A market order
sidesteps this entirely -- no assumed absolute entry price needed, fills at
whatever MNQ's real current price is.

SL/TP are now `percentage_sl`/`percentage_tp` (relative distance from the
parent's entry price, as a percentage) rather than absolute `sl`/`tp` prices,
for the same reason -- a percentage anchors correctly to the real fill price
regardless of any gap between the parent's price and the actual instrument's
price. `trail`/`trail_stop`/`trail_trigger`/`trail_freq`/`breakeven` stay
points-based (unchanged) -- these represent a relative step size, not an
absolute price anchor, so they remain valid even if the absolute price
differs, since MNQ/MES/etc. track the same underlying index and a given
point-move in the parent should correspond to a similar-magnitude move in
the mirrored instrument.

NOT YET EMPIRICALLY VERIFIED: whether PickMyTrade correctly computes
`quantity` from `risk_percentage` when using `percentage_sl` instead of an
absolute `sl` -- all prior testing used absolute price-based sl/tp. Test
this combination with a real (small/controlled) call before trusting it on
a live signal, same as every other assumption in this module has been
verified empirically before going live.

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

WIRED into the live signal path (server.py) as of 2026-07-05 -- fires
alongside the MICRO_MIRROR_MAP dispatch, wrapped in try/except with a
Telegram alert on failure.
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
    Relay an entry signal to PickMyTrade/Apex as a market order. `price`/`sl`/
    `tp2` come from the parent CFD instrument's signal -- used here only to
    compute relative (percentage/points) distances, never sent as an assumed
    absolute price for the mirrored instrument (see module docstring for why).
    `tp2` is the strategy's 1:3 RR take-profit level -- the single TP target
    sent (see module docstring for why TP2 rather than TP1). Raises
    RuntimeError/ValueError on failure -- caller decides how to handle/log/notify.
    """
    symbol = PICKMYTRADE_SYMBOL_MAP.get(instrument.upper())
    if not symbol:
        raise ValueError(f"No PickMyTrade symbol mapping for: {instrument}")

    r_distance = abs(price - sl)
    trail_step = r_distance * TRAIL_STEP_R
    percent_sl = (r_distance / price) * 100
    percent_tp = (abs(tp2 - price) / price) * 100

    body = {
        "strategy_name": "",
        "symbol": symbol,
        "date": datetime.now(timezone.utc).isoformat(),
        "data": "buy" if direction.upper() == "LONG" else "sell",
        "quantity": 0,
        "risk_percentage": RISK_PERCENTAGE,
        "price": price,
        "stp_limit_stp_price": 0,
        "tp": 0,
        "percentage_tp": percent_tp,
        "dollar_tp": 0,
        "sl": 0,
        "percentage_sl": percent_sl,
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
        "order_type": "MKT",
        "multiple_accounts": [
            {
                "token": PICKMYTRADE_TOKEN,
                "account_id": PICKMYTRADE_ACCOUNT_ID,
                "risk_percentage": RISK_PERCENTAGE,
                "quantity_multiplier": 0,
            }
        ],
    }

    response = _request(body)
    print(f"[pickmytrade] sent {body['data']} {symbol} MKT percent_sl={percent_sl:.4f}% "
          f"percent_tp={percent_tp:.4f}% -- response: {response}", flush=True)
    return response
