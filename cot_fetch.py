#!/usr/bin/env python3
"""
cot_fetch.py -- fetches + computes the dashboard's /cot JSON payload and
POSTs it to the live server's /admin/cot-update endpoint, which stores it
in Postgres (cot_cache table).

Why this exists (2026-07-07): Railway's outbound IP is blocked (HTTP 403)
by CFTC's Socrata-hosted API -- confirmed not a header/User-Agent issue
(curl from an unrelated network succeeds instantly with zero custom
headers). Since CFTC only publishes new COT data weekly (Friday
afternoons ET), server.py's old live-fetch-per-request design was already
more real-time than the data needs. This script runs from an environment
with an unblocked IP, computes the exact same payload shape server.py's
/cot route used to compute live, and POSTs it to the server -- no live
external call to CFTC from Railway at all. (An earlier version of this
fix wrote a local cot_data.json file for Railway to serve directly, but
that file is deliberately gitignored -- a past cleanup commit removed
flat-file persistence project-wide in favor of "PostgreSQL is sole data
layer." Postgres + an authenticated POST endpoint keeps that principle
intact and avoids a redeploy just to refresh data.)

Run this after each week's CFTC release (Friday afternoon ET). Requires
these environment variables:
  PAPER_WEBHOOK_TOKEN -- same shared secret the Pine Script webhook and
                         /admin/reset use, authenticates the POST to
                         /admin/cot-update.
  OANDA_API_TOKEN      -- same OANDA demo token oanda.py uses, for the
                          position book third-source check on forex.
  EIA_API_KEY          -- free key from eia.gov/opendata/register.php,
                          for the crude oil inventory third-source check.
A weekly scheduled cloud routine handles this automatically -- see
project memory for the routine ID.

Commercial/Non-Commercial/Retail breakdown (added 2026-07-10): the
Tradingster Legacy endpoint we already call for Source 2 (NonComm)
also returns Commercial_Positions_Long/Short_All and
Nonreportable_Positions_Long/Short_All in the same response -- these
are the CFTC Legacy report's classic three-way split (Commercial =
hedgers/producers/banks, Non-Commercial = large speculators/hedge
funds, Non-Reportable = small/retail traders below CFTC's reporting
threshold). No new API call needed, just extracting fields that were
already being fetched and ignored.

Third-source checks (added 2026-07-10): CFTC TFF/Disaggregated (Source
1) and Tradingster Legacy (Source 2) are NOT independent measurements --
they're two different CFTC report formats slicing the same underlying
weekly survey of the same reporting traders. A genuinely independent
third check needs a non-CFTC data source:
  - Forex (EUR/GBP/JPY/NZD/USD): OANDA's own position book -- aggregate
    client long/short % by instrument, a real broker order book, not
    CFTC survey data.
  - BTC: Crypto Fear & Greed Index (alternative.me) -- aggregates
    volatility, momentum, social sentiment, exchange dominance.
  - CRUDE: EIA weekly U.S. crude oil ending stocks (excl. SPR, series
    WCESTUS1) -- real supply/demand fundamentals, not positioning.
  - NQ/ES/US30 and GOLD/SILVER have no viable free independent source
    (researched and ruled out 2026-07-10 -- CBOE put/call ratio has no
    live free feed, only stale archives; GLD/SLV ETF holdings have no
    working free API; CME warehouse stocks explicitly blocks automated
    access via their Terms of Use). These stay 2-source (COT only).

Field names/shape match server.py's /cot route exactly (dashboard.html's
consumer needs zero changes to existing fields): {"updated": "YYYY-MM-DD",
"instruments": [...]}. New optional fields are additive.
"""
import concurrent.futures
import json
import os
import urllib.error
import urllib.request

SERVER_URL = "https://tradingbot-production-1e5a.up.railway.app"
PAPER_WEBHOOK_TOKEN = os.environ["PAPER_WEBHOOK_TOKEN"]
OANDA_API_TOKEN = os.environ["OANDA_API_TOKEN"]
EIA_API_KEY = os.environ["EIA_API_KEY"]

_COT_TFF = {
    "EUR": "099741", "GBP": "096742", "JPY": "097741", "NZD": "112741",
    "USD": "098662", "ES":  "13874%2B", "NQ": "209742", "US30": "12460%2B",
    "BTC": "133741", "VIX": "1170E1",
}
_COT_DISAGG = {"GOLD": "088691", "SILVER": "084691", "CRUDE": "067651"}
_COT_INVERT = {"VIX"}

# Bot COT symbol -> OANDA instrument, and whether the pair's "long" side
# means long the *quote* currency (JPY) rather than the base (need to invert).
_OANDA_FOREX_PAIRS = {
    "EUR": ("EUR_USD", False),
    "GBP": ("GBP_USD", False),
    "JPY": ("USD_JPY", True),   # long USD_JPY = long USD / short JPY
    "NZD": ("NZD_USD", False),
}


def _cot_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def _cot_index(nets):
    mn, mx = min(nets), max(nets)
    rng = mx - mn or 1
    return [round(((n - mn) / rng) * 100) for n in nets]


def _lev_signal(deltas):
    if len(deltas) < 2:
        return None
    if deltas[-2] < 0 and deltas[-1] > 0:
        return "flipped"
    if len(deltas) < 4:
        return None
    w = deltas[-4:]
    avg = sum(w) / 4
    std = (sum((x - avg) ** 2 for x in w) / 4) ** 0.5 or 1
    if deltas[-1] > avg + std * 0.75:
        return "covering"
    if deltas[-1] < avg - std * 0.75:
        return "pressing"
    return None


def _mm_signal(changes):
    if len(changes) < 4:
        return None
    w = changes[-4:]
    avg = sum(w) / 4
    std = (sum((x - avg) ** 2 for x in w) / 4) ** 0.5 or 1
    if w[-1] < avg - std * 0.75:
        return "closing"
    if w[-1] > avg + std * 0.75:
        return "adding"
    return None


def _near_term(bias, delta, lev):
    d_pos = delta > 500
    d_neg = delta < -500
    if lev == "flipped":
        return "↑ REVERSAL — Shorts Covering" if bias != "Bullish" else "↓ REVERSAL — Longs Rolling"
    if lev == "covering":
        if bias == "Bearish": return "↑ Shorts Fading"
        if bias == "Bullish": return "↑ Bulls Adding"
        if d_pos: return "↑ Accumulation"
    if lev == "pressing":
        if bias == "Bearish": return "↓ Bears Pressing"
        if bias == "Bullish": return "↓ Longs Fading — Caution"
        if d_neg: return "↓ Distribution"
    if d_pos and bias == "Bearish": return "↑ Delta Improving vs Bias"
    if d_neg and bias == "Bullish": return "↓ Delta Weakening vs Bias"
    if d_pos and bias == "Bullish": return "↑ Bias + Delta Aligned"
    if d_neg and bias == "Bearish": return "↓ Bias + Delta Aligned"
    return None


def _bias(idx):
    return "Bullish" if idx >= 60 else "Bearish" if idx <= 40 else "Neutral"


def _alignment(bias1, bias2):
    if not bias1 or not bias2:
        return "unknown"
    d = {"Bullish": 1, "Neutral": 0, "Bearish": -1}
    if d[bias1] == d[bias2] and bias1 != "Neutral":
        return "confirmed"
    if d[bias1] != d[bias2]:
        return "diverge"
    return "neutral"


def _fetch_tradingster_legacy(sym_code):
    sym, code = sym_code
    try:
        rows = _cot_get(f"https://www.tradingster.com/api/cot/legacy-futures/{code}")
        rows.sort(key=lambda r: r.get("As_of_Date", ""))
        return sym, rows
    except Exception as e:
        print(f"[cot_fetch] Tradingster legacy failed {sym}: {e}", flush=True)
        return sym, []


def _comm_retail_breakdown(leg_rows, invert):
    """Commercial (hedgers) vs Non-Reportable (retail) bias, from the same
    Tradingster Legacy rows already fetched for Source 2 -- CFTC Legacy
    report's classic three-way split, just extracting fields we weren't
    using before."""
    if len(leg_rows) < 4:
        return {"comm_net_now": None, "comm_net_1wk": None, "comm_bias": None,
                "retail_net_now": None, "retail_net_1wk": None, "retail_bias": None}

    comm_nets = [
        int(r.get("Commercial_Positions_Long_All") or 0) - int(r.get("Commercial_Positions_Short_All") or 0)
        for r in leg_rows
    ]
    retail_nets = [
        int(r.get("Nonreportable_Positions_Long_All") or 0) - int(r.get("Nonreportable_Positions_Short_All") or 0)
        for r in leg_rows
    ]
    comm_idxs = _cot_index(comm_nets)
    retail_idxs = _cot_index(retail_nets)
    if invert:
        comm_idxs = [100 - i for i in comm_idxs]
        retail_idxs = [100 - i for i in retail_idxs]

    return {
        "comm_net_now": comm_nets[-1], "comm_net_1wk": comm_nets[-2], "comm_bias": _bias(comm_idxs[-1]),
        "retail_net_now": retail_nets[-1], "retail_net_1wk": retail_nets[-2], "retail_bias": _bias(retail_idxs[-1]),
    }


def _fetch_oanda_position_book(pair):
    req = urllib.request.Request(
        f"https://api-fxpractice.oanda.com/v3/instruments/{pair}/positionBook",
        headers={"Authorization": f"Bearer {OANDA_API_TOKEN}"},
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read())
    buckets = data["positionBook"]["buckets"]
    long_pct = sum(float(b["longCountPercent"]) for b in buckets)
    short_pct = sum(float(b["shortCountPercent"]) for b in buckets)
    return long_pct, short_pct


def _fetch_oanda_all():
    """Returns {sym: (long_pct, short_pct)} for EUR/GBP/JPY/NZD, plus a
    synthesized USD (average of the "USD side" of the four pairs)."""
    out = {}
    for sym, (pair, invert) in _OANDA_FOREX_PAIRS.items():
        try:
            long_pct, short_pct = _fetch_oanda_position_book(pair)
            if invert:
                long_pct, short_pct = short_pct, long_pct
            out[sym] = (long_pct, short_pct)
        except Exception as e:
            print(f"[cot_fetch] OANDA position book failed {sym} ({pair}): {e}", flush=True)

    usd_sides = []
    for sym, (pair, invert) in _OANDA_FOREX_PAIRS.items():
        if sym not in out:
            continue
        long_pct, short_pct = out[sym]
        # "USD side" of each pair: JPY's pair already has USD as the base once un-inverted,
        # the rest have USD as the quote currency, so USD-long = the pair's short side.
        usd_sides.append(long_pct if invert else short_pct)
    if usd_sides:
        out["USD"] = (sum(usd_sides) / len(usd_sides), 100 - (sum(usd_sides) / len(usd_sides)))

    return out


def _fetch_fear_greed():
    data = _cot_get("https://api.alternative.me/fng/?limit=1")
    row = data["data"][0]
    return int(row["value"]), row["value_classification"]


def _fetch_eia_crude_stocks():
    url = (
        "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"
        f"?api_key={EIA_API_KEY}&frequency=weekly&data[0]=value"
        "&facets[product][]=EPC0&facets[process][]=SAX&facets[duoarea][]=NUS"
        "&sort[0][column]=period&sort[0][direction]=desc&length=3"
    )
    data = _cot_get(url)
    rows = data["response"]["data"]
    now = int(rows[0]["value"])
    prev = int(rows[1]["value"])
    return now, prev


def build_payload():
    results = []

    codes_q = ",".join(f"'{c}'" for c in _COT_TFF.values())
    tff_rows = _cot_get(
        f"https://publicreporting.cftc.gov/resource/gpe5-46if.json"
        f"?$where=cftc_contract_market_code%20in({codes_q})"
        f"&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=700"
    )

    disagg_q = ",".join(f"'{c}'" for c in _COT_DISAGG.values())
    disagg_rows = _cot_get(
        f"https://publicreporting.cftc.gov/resource/kh3c-gbw2.json"
        f"?$where=cftc_contract_market_code%20in({disagg_q})"
        f"&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=200"
    )

    all_codes = {**_COT_TFF, **_COT_DISAGG}
    leg2 = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=13) as ex:
        for sym, rows in ex.map(_fetch_tradingster_legacy, all_codes.items()):
            leg2[sym] = rows

    try:
        oanda_pos = _fetch_oanda_all()
    except Exception as e:
        print(f"[cot_fetch] OANDA position book fetch failed entirely: {e}", flush=True)
        oanda_pos = {}

    try:
        fng_value, fng_class = _fetch_fear_greed()
    except Exception as e:
        print(f"[cot_fetch] Fear & Greed fetch failed: {e}", flush=True)
        fng_value, fng_class = None, None

    try:
        eia_now, eia_prev = _fetch_eia_crude_stocks()
    except Exception as e:
        print(f"[cot_fetch] EIA crude stocks fetch failed: {e}", flush=True)
        eia_now, eia_prev = None, None

    for sym, code in _COT_TFF.items():
        raw_code = code.replace("%2B", "+")
        rows = sorted(
            [r for r in tff_rows if r.get("cftc_contract_market_code") == raw_code],
            key=lambda r: r.get("report_date_as_yyyy_mm_dd", ""),
        )
        if len(rows) < 4:
            continue

        invert = sym in _COT_INVERT

        lev_nets = [
            int(r.get("lev_money_positions_long") or 0) - int(r.get("lev_money_positions_short") or 0)
            for r in rows
        ]
        idxs = _cot_index(lev_nets)
        if invert:
            idxs = [100 - i for i in idxs]
        idx = idxs[-1]
        bias1 = _bias(idx)

        lev_deltas = [
            int(r.get("change_in_lev_money_long") or 0) - int(r.get("change_in_lev_money_short") or 0)
            for r in rows
        ]
        if invert:
            lev_deltas = [-d for d in lev_deltas]
        weekly_delta = lev_deltas[-1]
        lev = _lev_signal(lev_deltas)

        am_changes = [int(r.get("change_in_asset_mgr_long") or 0) for r in rows]
        mm = None if invert else _mm_signal(am_changes)

        latest_date = rows[-1].get("report_date_as_yyyy_mm_dd", "")[:10]

        leg_idx = leg_bias1 = leg_lev = leg_net_now = leg_net_1wk = None
        leg_rows = leg2.get(sym, [])
        if len(leg_rows) >= 4:
            nc_nets = [
                int(r.get("Noncommercial_Positions_Long_All") or 0) - int(r.get("Noncommercial_Positions_Short_All") or 0)
                for r in leg_rows
            ]
            nc_idxs = _cot_index(nc_nets)
            if invert:
                nc_idxs = [100 - i for i in nc_idxs]
            leg_idx = nc_idxs[-1]
            leg_bias1 = _bias(leg_idx)
            nc_deltas = [nc_nets[i] - nc_nets[i - 1] for i in range(1, len(nc_nets))]
            if invert:
                nc_deltas = [-d for d in nc_deltas]
            leg_lev = _lev_signal(nc_deltas)
            leg_net_now = nc_nets[-1]
            leg_net_1wk = nc_nets[-2]

        item = {
            "sym": sym, "date": latest_date,
            "net_now": lev_nets[-1], "net_1wk": lev_nets[-2],
            "net_2wk": lev_nets[-3] if len(lev_nets) >= 3 else None,
            "weekly_delta": weekly_delta,
            "cot_idx": idx, "bias": bias1,
            "lev_signal": lev, "mm_signal": mm,
            "near_term": _near_term(bias1, weekly_delta, lev),
            "src1_label": "LevF (inv)" if invert else "Lev Funds",
            "leg_idx": leg_idx, "leg_bias": leg_bias1,
            "leg_lev": leg_lev,
            "leg_net_now": leg_net_now, "leg_net_1wk": leg_net_1wk,
            "alignment": _alignment(bias1, leg_bias1),
        }
        item.update(_comm_retail_breakdown(leg_rows, invert))

        if sym in oanda_pos:
            long_pct, short_pct = oanda_pos[sym]
            item["oanda_long_pct"] = round(long_pct, 1)
            item["oanda_short_pct"] = round(short_pct, 1)
            item["oanda_bias"] = _bias(long_pct)

        if sym == "BTC" and fng_value is not None:
            item["fng_value"] = fng_value
            item["fng_classification"] = fng_class

        results.append(item)

    for sym, code in _COT_DISAGG.items():
        rows = sorted(
            [r for r in disagg_rows if r.get("cftc_contract_market_code") == code],
            key=lambda r: r.get("report_date_as_yyyy_mm_dd", ""),
        )
        if len(rows) < 4:
            continue

        mm_nets = [
            int(r.get("m_money_positions_long_all") or 0) - int(r.get("m_money_positions_short_all") or 0)
            for r in rows
        ]
        idxs = _cot_index(mm_nets)
        idx = idxs[-1]
        bias1 = _bias(idx)

        mm_deltas = [
            int(r.get("change_in_m_money_long_all") or 0) - int(r.get("change_in_m_money_short_all") or 0)
            for r in rows
        ]
        weekly_delta = mm_deltas[-1]
        lev = _lev_signal(mm_deltas)
        mm_long_changes = [int(r.get("change_in_m_money_long_all") or 0) for r in rows]
        mm = _mm_signal(mm_long_changes)

        latest_date = rows[-1].get("report_date_as_yyyy_mm_dd", "")[:10]

        leg_idx = leg_bias1 = leg_lev = leg_net_now = leg_net_1wk = None
        leg_rows = leg2.get(sym, [])
        if len(leg_rows) >= 4:
            nc_nets = [
                int(r.get("Noncommercial_Positions_Long_All") or 0) - int(r.get("Noncommercial_Positions_Short_All") or 0)
                for r in leg_rows
            ]
            nc_idxs = _cot_index(nc_nets)
            leg_idx = nc_idxs[-1]
            leg_bias1 = _bias(leg_idx)
            nc_deltas = [nc_nets[i] - nc_nets[i - 1] for i in range(1, len(nc_nets))]
            leg_lev = _lev_signal(nc_deltas)
            leg_net_now = nc_nets[-1]
            leg_net_1wk = nc_nets[-2]

        item = {
            "sym": sym, "date": latest_date,
            "net_now": mm_nets[-1], "net_1wk": mm_nets[-2],
            "net_2wk": mm_nets[-3] if len(mm_nets) >= 3 else None,
            "weekly_delta": weekly_delta,
            "cot_idx": idx, "bias": bias1,
            "lev_signal": lev, "mm_signal": mm,
            "near_term": _near_term(bias1, weekly_delta, lev),
            "src1_label": "Mgd Money",
            "leg_idx": leg_idx, "leg_bias": leg_bias1,
            "leg_lev": leg_lev,
            "leg_net_now": leg_net_now, "leg_net_1wk": leg_net_1wk,
            "alignment": _alignment(bias1, leg_bias1),
        }
        item.update(_comm_retail_breakdown(leg_rows, invert=False))

        if sym == "CRUDE" and eia_now is not None:
            change = eia_now - eia_prev
            item["eia_stocks_now"] = eia_now
            item["eia_stocks_1wk"] = eia_prev
            item["eia_bias"] = "Bullish" if change < -2000 else "Bearish" if change > 2000 else "Neutral"

        results.append(item)

    latest = max((r["date"] for r in results if r.get("date")), default="")
    return {"updated": latest, "instruments": results}


def main():
    payload = build_payload()
    body = json.dumps({"token": PAPER_WEBHOOK_TOKEN, "payload": payload}).encode()
    req = urllib.request.Request(
        f"{SERVER_URL}/admin/cot-update",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"POST /admin/cot-update failed => HTTP {e.code}: {e.read().decode()}")
    print(f"[cot_fetch] computed {len(payload['instruments'])} instruments, updated={payload['updated']!r} "
          f"-- server response: {resp}")


if __name__ == "__main__":
    main()
