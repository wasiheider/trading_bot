#!/usr/bin/env python3
"""
earnings_fetch.py -- fetches upcoming earnings-report dates (Alpha Vantage
EARNINGS_CALENDAR), filters to only S&P 500 / Nasdaq-100 constituents
(slickcharts.com), and POSTs the result to /admin/earnings-update, which
stores it in Postgres (earnings_cache table). Dashboard's /earnings route
serves straight from that cache.

Why filtered like this (2026-07-20): the trading bot only trades US500
(S&P 500) and US100 (Nasdaq-100) among the equity-index instruments --
Russell 2000 (RTY) was requested too but dropped after research found no
reliable free live source for its ~2000 constituents (the only free full
list, iShares' IWM holdings CSV, is Akamai-protected -- same block class
as CFTC's Socrata API, already confirmed blocked on Railway's outbound IP,
see project memory 2026-07-07).

Constituent source: originally planned as Wikipedia (like COT's general
"prefer free, no-key, no-scraping-risk sources" bias), but the current
"Nasdaq-100" Wikipedia article does not have a usable per-ticker table --
its constituent list only exists as a navbox of company names (no ticker
symbols) that would need a second name->ticker resolution step. Verified
directly (see project memory) that slickcharts.com/sp500 and
slickcharts.com/nasdaq100 both have clean, consistently-structured tables
with a Symbol column and no signs of datacenter-IP blocking -- used for
both indices instead, for consistency.

Unlike COT, none of these sources (Alpha Vantage, slickcharts) are known
to block datacenter/cloud IPs, so this runs directly on Railway's schedule
and does NOT need the "fetch out-of-band, POST from elsewhere" workaround
COT needs for CFTC. It's still structured as a POST-to-cache script
(rather than a live per-request fetch in server.py) purely for dashboard
load speed -- Alpha Vantage's bulk call is slow (single call covering the
whole US market), way too slow to run on every page load -- and this
keeps well within its 25-calls/day free-tier limit (only needs 1/day).

Run daily. Requires these environment variables:
  PAPER_WEBHOOK_TOKEN   -- same shared secret /admin/cot-update and
                           /admin/reset use, authenticates the POST to
                           /admin/earnings-update.
  ALPHA_VANTAGE_API_KEY -- free key, instant email signup at
                           alphavantage.co/support/#api-key.
A daily scheduled cloud routine handles this automatically -- see
project memory for the routine ID.

Needs beautifulsoup4 for slickcharts table parsing (not in
requirements.txt, local-only dependency -- same pattern as reportlab
for cot_generate.py).

Payload shape: {"updated": "YYYY-MM-DD", "earnings": [{"symbol", "name",
"report_date", "report_time", "indices": [...]}]}. report_time is one of
"pre-market" / "post-market" / "" (Alpha Vantage leaves it blank when
unknown -- passed through as-is, not guessed).
"""
import csv
import io
import json
import os
import urllib.error
import urllib.request

from bs4 import BeautifulSoup

SERVER_URL = "https://tradingbot-production-1e5a.up.railway.app"
PAPER_WEBHOOK_TOKEN = os.environ["PAPER_WEBHOOK_TOKEN"]
ALPHA_VANTAGE_API_KEY = os.environ["ALPHA_VANTAGE_API_KEY"]

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def fetch_index_symbols(url: str) -> dict:
    """Returns {symbol: company_name} from a slickcharts.com index page's
    Symbol/Company columns (index 2 and 1 respectively)."""
    soup = BeautifulSoup(_fetch(url), "html.parser")
    table = soup.find("table")
    out = {}
    for row in table.find_all("tr")[1:]:
        cells = row.find_all("td")
        if len(cells) < 3:
            continue
        symbol = cells[2].get_text(strip=True)
        name = cells[1].get_text(strip=True)
        if symbol:
            out[symbol] = name
    return out


def fetch_earnings_calendar() -> list:
    """Bulk fetch -- omitting &symbol= returns every US company reporting
    within the horizon window, one call for the whole market."""
    url = (f"https://www.alphavantage.co/query?function=EARNINGS_CALENDAR"
           f"&horizon=3month&apikey={ALPHA_VANTAGE_API_KEY}")
    text = _fetch(url).decode()
    return list(csv.DictReader(io.StringIO(text)))


def build_payload() -> dict:
    sp500 = fetch_index_symbols("https://www.slickcharts.com/sp500")
    nasdaq100 = fetch_index_symbols("https://www.slickcharts.com/nasdaq100")
    calendar = fetch_earnings_calendar()

    results = []
    for row in calendar:
        symbol = (row.get("symbol") or "").strip()
        in_sp500 = symbol in sp500
        in_nasdaq100 = symbol in nasdaq100
        if not (in_sp500 or in_nasdaq100):
            continue
        indices = []
        if in_sp500:
            indices.append("S&P500")
        if in_nasdaq100:
            indices.append("Nasdaq100")
        results.append({
            "symbol":      symbol,
            "name":        sp500.get(symbol) or nasdaq100.get(symbol) or row.get("name", ""),
            "report_date": row.get("reportDate", ""),
            "report_time": row.get("timeOfTheDay", ""),
            "indices":     indices,
        })

    results.sort(key=lambda r: (r["report_date"], r["symbol"]))
    today = results[0]["report_date"] if results else ""
    return {
        "updated": today,
        "universe_size": len(set(sp500) | set(nasdaq100)),
        "earnings": results,
    }


def main():
    payload = build_payload()
    body = json.dumps({"token": PAPER_WEBHOOK_TOKEN, "payload": payload}).encode()
    req = urllib.request.Request(
        f"{SERVER_URL}/admin/earnings-update",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"POST /admin/earnings-update failed => HTTP {e.code}: {e.read().decode()}")
    print(f"[earnings_fetch] universe={payload['universe_size']} tickers, "
          f"{len(payload['earnings'])} matched earnings rows -- server response: {resp}")


if __name__ == "__main__":
    main()
