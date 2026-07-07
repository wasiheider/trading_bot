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
PAPER_WEBHOOK_TOKEN as an environment variable (same shared secret the
Pine Script webhook and /admin/reset use) -- authenticates the POST to
/admin/cot-update. A weekly scheduled cloud routine handles this
automatically -- see project memory for the routine ID.

Field names/shape match server.py's /cot route exactly (dashboard.html's
consumer needs zero changes): {"updated": "YYYY-MM-DD", "instruments": [...]}
"""
import concurrent.futures
import json
import os
import urllib.error
import urllib.request

SERVER_URL = "https://tradingbot-production-1e5a.up.railway.app"
PAPER_WEBHOOK_TOKEN = os.environ["PAPER_WEBHOOK_TOKEN"]

_COT_TFF = {
    "EUR": "099741", "GBP": "096742", "JPY": "097741", "NZD": "112741",
    "USD": "098662", "ES":  "13874%2B", "NQ": "209742", "US30": "12460%2B",
    "BTC": "133741", "VIX": "1170E1",
}
_COT_DISAGG = {"GOLD": "088691", "SILVER": "084691", "CRUDE": "067651"}
_COT_INVERT = {"VIX"}


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

        results.append({
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
        })

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

        results.append({
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
        })

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
