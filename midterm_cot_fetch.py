"""
midterm_cot_fetch.py -- pulls full CFTC Legacy COT history (NQ, ES, gold, silver, BTC, natgas)
and OANDA daily prices since 2002 into data/midterm/ for midterm_cot_analyze.py.
Needs OANDA_API_TOKEN in the environment. Built for COT_Midterm_Context_2026-11-03.pdf.
"""
import os, json, urllib.request, urllib.parse
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "midterm")
os.makedirs(OUT, exist_ok=True)

def get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=90) as r:
        return json.loads(r.read())

# ---- CFTC legacy futures-only COT, full history ----
CODES = {"NQ": "209742", "ES": "13874A", "GC": "088691", "SI": "084691", "BTC": "133741", "NG": "023651"}
SEL = ("report_date_as_yyyy_mm_dd,open_interest_all,noncomm_positions_long_all,noncomm_positions_short_all,"
       "comm_positions_long_all,comm_positions_short_all,nonrept_positions_long_all,nonrept_positions_short_all")
cot = {}
for sym, code in CODES.items():
    rows, off = [], 0
    while True:
        q = urllib.parse.urlencode({"cftc_contract_market_code": code, "$order": "report_date_as_yyyy_mm_dd",
                                    "$limit": 5000, "$offset": off, "$select": SEL})
        j = get("https://publicreporting.cftc.gov/resource/6dca-aqww.json?" + q)
        rows += j
        if len(j) < 5000: break
        off += 5000
    cot[sym] = rows
    print(sym, len(rows), rows[0]["report_date_as_yyyy_mm_dd"][:10], rows[-1]["report_date_as_yyyy_mm_dd"][:10])
json.dump(cot, open(os.path.join(OUT, "cot_legacy.json"), "w"))

# ---- OANDA daily prices ----
H = {"Authorization": "Bearer " + os.environ["OANDA_API_TOKEN"]}
px = {}
for inst in ["NAS100_USD", "SPX500_USD", "XAU_USD", "XAG_USD", "BTC_USD", "NATGAS_USD"]:
    out, frm = {}, "2002-01-01T00:00:00Z"
    try:
        while True:
            q = urllib.parse.urlencode({"granularity": "D", "from": frm, "count": 5000, "price": "M"})
            c = get(f"https://api-fxpractice.oanda.com/v3/instruments/{inst}/candles?" + q, H)["candles"]
            for x in c:
                if x["complete"]:
                    m = x["mid"]
                    out[x["time"][:10]] = [float(m["o"]), float(m["h"]), float(m["l"]), float(m["c"])]
            if len(c) < 5000: break
            frm = c[-1]["time"]
    except Exception as e:
        print(inst, "ERR", e)
    if out:
        px[inst] = dict(sorted(out.items()))
        k = list(px[inst])
        print(inst, len(k), k[0], k[-1], px[inst][k[-1]])
json.dump(px, open(os.path.join(OUT, "px.json"), "w"))
