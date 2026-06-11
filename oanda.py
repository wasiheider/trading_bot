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
    "NZDUSD": "NZD_USD",
}

# OANDA units per 1 standard lot from risk.py (forex: 1 lot = 100,000 units)
_UNITS_PER_LOT = {
    "EUR_USD": 100000,
    "GBP_USD": 100000,
    "USD_JPY": 100000,
    "EUR_NZD": 100000,
    "NZD_USD": 100000,
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
            "timeInForce":  "GFD",
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
    """Close a specific trade by its OANDA trade ID. Returns realizedPL."""
    resp = _request("PUT", f"/v3/accounts/{OANDA_ACCOUNT_ID}/trades/{trade_id}/close")
    fill = resp.get("orderFillTransaction", {})
    try:
        pl = float(fill.get("pl", "0") or "0")
    except (ValueError, TypeError):
        pl = 0.0
    return {"realizedPL": pl}


def get_open_trade(instrument: str, direction: str) -> dict | None:
    """Query OANDA for an open trade matching instrument and direction. Returns trade dict or None."""
    oanda_instrument = INSTRUMENT_MAP.get(instrument.upper())
    if not oanda_instrument:
        return None
    resp = _request("GET", f"/v3/accounts/{OANDA_ACCOUNT_ID}/openTrades")
    for trade in resp.get("trades", []):
        if trade.get("instrument") != oanda_instrument:
            continue
        units = float(trade.get("currentUnits", 0))
        trade_dir = "LONG" if units > 0 else "SHORT"
        if trade_dir == direction.upper():
            return trade
    return None


def get_all_open_trades() -> list:
    """Return all open trades on the OANDA account."""
    resp = _request("GET", f"/v3/accounts/{OANDA_ACCOUNT_ID}/openTrades")
    return resp.get("trades", [])


def get_account_summary() -> dict:
    """Return OANDA account balance (realized), NAV, and unrealizedPL."""
    resp = _request("GET", f"/v3/accounts/{OANDA_ACCOUNT_ID}/summary")
    acct = resp.get("account", {})
    return {
        "balance":      float(acct.get("balance",      0) or 0),
        "nav":          float(acct.get("NAV",           0) or 0),
        "unrealizedPL": float(acct.get("unrealizedPL", 0) or 0),
    }
