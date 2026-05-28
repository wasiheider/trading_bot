import json
import urllib.request
import urllib.error

from config import OANDA_API_TOKEN, OANDA_ACCOUNT_ID, OANDA_BASE_URL

_HEADERS = {
    "Authorization": f"Bearer {OANDA_API_TOKEN}",
    "Content-Type":  "application/json",
}

# Bot instrument name -> OANDA instrument name
# Only forex pairs are available on this OANDA demo account type.
# XAUUSD, NAS100, US30, USOIL etc. are CFD-only and not supported.
INSTRUMENT_MAP = {
    "EURUSD": "EUR_USD",
    "GBPUSD": "GBP_USD",
    "USDJPY": "USD_JPY",
    "EURNZD": "EUR_NZD",
}

# OANDA units per 1 standard lot from risk.py (forex: 1 lot = 100,000 units)
_UNITS_PER_LOT = {
    "EUR_USD": 100000,
    "GBP_USD": 100000,
    "USD_JPY": 100000,
    "EUR_NZD": 100000,
}


def _request(method, path, body=None):
    data = json.dumps(body).encode() if body else None
    req  = urllib.request.Request(
        f"{OANDA_BASE_URL}{path}",
        data=data,
        headers=_HEADERS,
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"OANDA {method} {path} => HTTP {e.code}: {e.read().decode()}")


def place_order(instrument: str, direction: str, lot_size: float) -> dict:
    """Place a market order. Returns dict with trade_id, price, units."""
    oanda_instrument = INSTRUMENT_MAP.get(instrument.upper())
    if not oanda_instrument:
        raise ValueError(f"No OANDA mapping for: {instrument}")

    multiplier = _UNITS_PER_LOT.get(oanda_instrument, 1000)
    units = max(1, round(lot_size * multiplier))
    if direction.upper() == "SHORT":
        units = -units

    resp = _request("POST", f"/v3/accounts/{OANDA_ACCOUNT_ID}/orders", {
        "order": {
            "type":         "MARKET",
            "instrument":   oanda_instrument,
            "units":        str(units),
            "timeInForce":  "FOK",
            "positionFill": "DEFAULT",
        }
    })

    if "orderFillTransaction" in resp:
        fill = resp["orderFillTransaction"]
        return {
            "trade_id": fill["tradeOpened"]["tradeID"],
            "price":    fill["price"],
            "units":    fill["units"],
        }

    if "orderCancelTransaction" in resp:
        reason = resp["orderCancelTransaction"].get("reason", "unknown")
        raise RuntimeError(f"Order cancelled: {reason}")

    raise RuntimeError(f"Unexpected response: {resp}")


def close_trade(trade_id: str) -> dict:
    """Close a specific trade by its OANDA trade ID."""
    return _request("PUT", f"/v3/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/close")
