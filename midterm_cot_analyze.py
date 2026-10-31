"""
midterm_cot_analyze.py -- reads data/midterm/ (from midterm_cot_fetch.py) and prints:
COT reads + forward returns at every midterm since 2002, positioning by presidency,
current Legacy 70-week reads, and NQ/silver sharp-drop analogs. Writes results.json.
The Legacy 70-week index is context only; the dashboard (cot_fetch.py) decides the lean.
"""
import os, json, bisect
from datetime import date, timedelta
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "midterm")
os.makedirs(OUT, exist_ok=True)
cot = json.load(open(os.path.join(OUT, "cot_legacy.json")))
px = json.load(open(os.path.join(OUT, "px.json")))
PXMAP = {"NQ": "NAS100_USD", "ES": "SPX500_USD", "GC": "XAU_USD", "SI": "XAG_USD", "BTC": "BTC_USD", "NG": "NATGAS_USD"}
N = 70  # weeks, same scale as the dashboard

# ---------- COT series ----------
def series(sym):
    out = []
    for r in cot[sym]:
        f = lambda k: float(r.get(k) or 0)
        oi = f("open_interest_all") or 1
        out.append(dict(d=r["report_date_as_yyyy_mm_dd"][:10], oi=oi,
                        comm=f("comm_positions_long_all") - f("comm_positions_short_all"),
                        spec=f("noncomm_positions_long_all") - f("noncomm_positions_short_all"),
                        retail=f("nonrept_positions_long_all") - f("nonrept_positions_short_all")))
    for i, x in enumerate(out):
        w = out[max(0, i - N + 1): i + 1]
        for k in ("comm", "spec", "retail"):
            lo, hi = min(y[k] for y in w), max(y[k] for y in w)
            x[k + "_idx"] = round((x[k] - lo) / (hi - lo) * 100) if hi > lo and len(w) >= 26 else None
        x["spec_pct_oi"] = round(x["spec"] / x["oi"] * 100, 1)
    return out
S = {s: series(s) for s in cot}

def cot_on(sym, d):
    """Last report dated strictly before d (what traders knew on election day)."""
    ds = [x["d"] for x in S[sym]]
    i = bisect.bisect_left(ds, d) - 1
    return S[sym][i] if i >= 0 else None

def verdict(x):
    c, s, r = x["comm_idx"], x["spec_idx"], x["retail_idx"]
    if c is None: return "n/a"
    if c >= 80 and ((s is not None and s <= 20) or (r is not None and r <= 20)): return "Bullish setup"
    if c <= 20 and ((s is not None and s >= 80) or (r is not None and r >= 80)): return "Bearish setup"
    return "No edge"

# ---------- prices ----------
P = {k: (list(v), [v[d][3] for d in v], [v[d][1] for d in v], [v[d][2] for d in v]) for k, v in px.items()}
def idx_on(inst, d):
    ds = P[inst][0]
    return bisect.bisect_right(ds, d) - 1
def fwd(inst, i, n):
    ds, c, h, l = P[inst]
    if i < 0 or i + n >= len(c): return None
    return round((c[i + n] / c[i] - 1) * 100, 1)
def maxdd_fwd(inst, i, n):
    ds, c, h, l = P[inst]
    if i + 1 >= len(c): return None
    lo = min(l[i + 1: i + 1 + n])
    return round((lo / c[i] - 1) * 100, 1)
def dd_from_high(inst, i, look=None):
    ds, c, h, l = P[inst]
    a = 0 if look is None else max(0, i - look)
    return round((c[i] / max(h[a:i + 1]) - 1) * 100, 1)

res = {}
# ---------- midterms ----------
MID = [("2002-11-05", "Bush (R)"), ("2006-11-07", "Bush (R)"), ("2010-11-02", "Obama (D)"),
       ("2014-11-04", "Obama (D)"), ("2018-11-06", "Trump (R), 1st term"), ("2022-11-08", "Biden (D)")]
mt = []
for d, pres in MID:
    row = {"date": d, "pres": pres, "assets": {}}
    for sym in ["NQ", "ES", "GC", "SI", "BTC", "NG"]:
        x = cot_on(sym, d)
        a = {}
        if x and x["comm_idx"] is not None:
            a.update(cot_date=x["d"], comm=x["comm_idx"], spec=x["spec_idx"], retail=x["retail_idx"],
                     spec_pct_oi=x["spec_pct_oi"], verdict=verdict(x))
        inst = PXMAP[sym]
        if inst in P and P[inst][0][0] <= d:
            i = idx_on(inst, d)
            a.update(pre20=round((P[inst][1][i] / P[inst][1][i - 20] - 1) * 100, 1),
                     dd_52w=dd_from_high(inst, i, 252),
                     f5=fwd(inst, i, 5), f20=fwd(inst, i, 20), f60=fwd(inst, i, 60), f120=fwd(inst, i, 120), f250=fwd(inst, i, 250),
                     mdd20=maxdd_fwd(inst, i, 20))
        if a: row["assets"][sym] = a
    mt.append(row)
res["midterms"] = mt

# ---------- presidencies: average positioning ----------
TERMS = [("Bush (R)", "2001-01-20", "2009-01-20"), ("Obama (D)", "2009-01-20", "2017-01-20"),
         ("Trump 1st (R)", "2017-01-20", "2021-01-20"), ("Biden (D)", "2021-01-20", "2025-01-20"),
         ("Trump 2nd (R)", "2025-01-20", "2026-12-31")]
pres = []
for name, a, b in TERMS:
    row = {"term": name, "assets": {}}
    for sym in ["NQ", "ES", "GC", "SI"]:
        w = [x for x in S[sym] if a <= x["d"] < b and x["comm_idx"] is not None]
        if not w: continue
        bull = sum(verdict(x) == "Bullish setup" for x in w); bear = sum(verdict(x) == "Bearish setup" for x in w)
        inst = PXMAP[sym]; ret = None
        if inst in P and P[inst][0][0] <= a:
            i0, i1 = idx_on(inst, a), idx_on(inst, b)
            ret = round((P[inst][1][i1] / P[inst][1][i0] - 1) * 100, 1)
        row["assets"][sym] = dict(weeks=len(w), avg_spec_pct_oi=round(sum(x["spec_pct_oi"] for x in w) / len(w), 1),
                                  bull_wks=bull, bear_wks=bear, px_ret=ret)
    pres.append(row)
res["presidencies"] = pres

# ---------- current ----------
cur = {}
for sym in ["NQ", "ES", "GC", "SI", "BTC", "NG"]:
    x = S[sym][-1]; p = S[sym][-2]
    cur[sym] = dict(d=x["d"], comm=x["comm_idx"], spec=x["spec_idx"], retail=x["retail_idx"], spec_pct_oi=x["spec_pct_oi"],
                    verdict=verdict(x), spec_chg=round(x["spec"] - p["spec"]), comm_chg=round(x["comm"] - p["comm"]),
                    last8=[(y["d"], y["comm_idx"], y["spec_idx"], y["retail_idx"]) for y in S[sym][-8:]])
res["current"] = cur

# ---------- shock analogs ----------
def events(inst, cond, gap=20):
    ds, c, h, l = P[inst]; ev = []; last = -999
    for i in range(260, len(c)):
        if i - last < gap: continue
        if cond(i, c, h):
            last = i
            ev.append(dict(d=ds[i], dd_ath=dd_from_high(inst, i), r2=round((c[i] / c[i - 2] - 1) * 100, 1),
                           r1=round((c[i] / c[i - 1] - 1) * 100, 1),
                           f5=fwd(inst, i, 5), f20=fwd(inst, i, 20), f60=fwd(inst, i, 60), mdd20=maxdd_fwd(inst, i, 20)))
    return ev
def ath(i, h): return max(h[: i + 1])
# NQ: 2-day drop of 4%+ while 7-15% off the all-time high
res["nq_shock"] = events("NAS100_USD", lambda i, c, h: c[i] / c[i - 2] - 1 <= -0.04 and -0.15 <= c[i] / ath(i, h) - 1 <= -0.07)
# Silver: 1-day drop of 5%+ while 40%+ off its all-time high
res["si_shock"] = events("XAG_USD", lambda i, c, h: c[i] / c[i - 1] - 1 <= -0.05 and c[i] / ath(i, h) - 1 <= -0.40)
# Silver: any 1-day drop of 5%+ (broader set)
res["si_shock_all"] = events("XAG_USD", lambda i, c, h: c[i] / c[i - 1] - 1 <= -0.05)

# ---------- how COT moved after each midterm (0/4/8/13/26 weeks) ----------
traj = {}
for sym in ["NQ", "ES", "GC", "SI", "BTC", "NG"]:
    ds = [x["d"] for x in S[sym]]; traj[sym] = []
    for d, _ in MID:
        i = bisect.bisect_left(ds, d) - 1
        if i < 0 or S[sym][i]["comm_idx"] is None: continue
        row = {"date": d, "cot": {w: (S[sym][i + w]["comm_idx"], S[sym][i + w]["spec_idx"], S[sym][i + w]["retail_idx"])
                                  for w in (0, 4, 8, 13, 26, 52) if i + w < len(S[sym])}}
        inst = PXMAP[sym]
        if inst in P and P[inst][0][0] <= d:
            k = idx_on(inst, d); row["px"] = {w: fwd(inst, k, w * 5) for w in (4, 8, 13, 26, 52)}
        traj[sym].append(row)
res["post_midterm_cot"] = traj

json.dump(res, open(os.path.join(OUT, "results.json"), "w"), indent=1)

def summ(ev, k):
    v = [e[k] for e in ev if e[k] is not None]
    if not v: return "n/a"
    v2 = sorted(v); med = v2[len(v2) // 2]
    return f"n={len(v)} med {med:+.1f} avg {sum(v)/len(v):+.1f} up {sum(x>0 for x in v)}/{len(v)}"
print("CURRENT"); [print(s, {k: v for k, v in cur[s].items() if k != 'last8'}) for s in cur]
print("\nMIDTERMS")
for r in mt:
    print(r["date"], r["pres"])
    for s, a in r["assets"].items(): print("  ", s, a)
print("\nPRESIDENCIES")
for r in pres: print(r["term"], r["assets"])
for k in ("nq_shock", "si_shock", "si_shock_all"):
    print("\n", k)
    for e in res[k]: print("  ", e)
    for f in ("f5", "f20", "f60", "mdd20"): print("   ", f, summ(res[k], f))
print("\nPOST-MIDTERM COT (C/Spec/Retail at +0/4/8/13/26/52 wks; price % at +4/8/13/26/52 wks)")
for sym, rows in traj.items():
    for r in rows: print(sym, r["date"], r["cot"], r.get("px", ""))
