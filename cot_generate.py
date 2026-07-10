#!/usr/bin/env python3
"""
COT Weekly Breakdown Generator
Run after CFTC publishes (typically Friday afternoon ET).
Sources: CFTC TFF / Disaggregated + Tradingster Legacy Non-Commercial,
plus (added 2026-07-10) the Commercial/Non-Reportable (Retail) breakdown
from the same Tradingster data, and genuine third-source independent
checks where a free one exists: OANDA position book (forex), Crypto
Fear & Greed Index (BTC), EIA weekly crude stocks (Oil). See
cot_fetch.py's docstring for the full reasoning on why Source 1/Source 2
aren't truly independent of each other, and why NQ/ES/US30/Gold/Silver
have no viable free third source.

Produces a dated PDF in the same directory.

Requires environment variables:
  OANDA_API_TOKEN -- same OANDA demo token oanda.py uses
  EIA_API_KEY     -- free key from eia.gov/opendata/register.php

Usage:
    python cot_generate.py
"""

import urllib.request
import json
import math
import os
import sys
from datetime import datetime, timedelta
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                Table, TableStyle, HRFlowable)
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

# ── Config ────────────────────────────────────────────────────────────────────

TFF_CODES = {
    "EUR": "099741", "GBP": "096742", "JPY": "097741", "NZD": "112741",
    "USD": "098662", "ES":  "13874+",  "NQ":  "209742", "US30": "12460+",
    "BTC": "133741", "VIX": "1170E1",
}
DISAGG_CODES = {
    "GOLD": "088691", "SILVER": "084691", "CRUDE": "067651",
}
TRADINGSTER_CODES = {
    "EUR": "099741", "GBP": "096742", "JPY": "097741", "NZD": "112741",
    "USD": "098662", "ES":  "13874%2B","NQ":  "209742", "US30": "12460%2B",
    "BTC": "133741", "VIX": "1170E1",
    "GOLD": "088691","SILVER": "084691","CRUDE": "067651",
}
INVERT = {"VIX"}
ORDER  = ["EUR","GBP","JPY","NZD","USD","ES","NQ","US30","BTC","GOLD","SILVER","CRUDE","VIX"]
SECTION_MAP = {
    "EUR":"FOREX","GBP":"FOREX","JPY":"FOREX","NZD":"FOREX","USD":"FOREX",
    "ES":"INDICES","NQ":"INDICES","US30":"INDICES","BTC":"INDICES",
    "GOLD":"COMMODITIES","SILVER":"COMMODITIES","CRUDE":"COMMODITIES",
    "VIX":"VOLATILITY (VIX)",
}
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

OANDA_API_TOKEN = os.environ["OANDA_API_TOKEN"]
EIA_API_KEY = os.environ["EIA_API_KEY"]

# Bot COT symbol -> OANDA instrument, and whether the pair's "long" side
# means long the *quote* currency (JPY) rather than the base (need to invert).
OANDA_FOREX_PAIRS = {
    "EUR": ("EUR_USD", False),
    "GBP": ("GBP_USD", False),
    "JPY": ("USD_JPY", True),   # long USD_JPY = long USD / short JPY
    "NZD": ("NZD_USD", False),
}

# ── Fetch ─────────────────────────────────────────────────────────────────────

def fetch(url):
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def fetch_tff():
    codes = "','".join(c.replace("+", "%2B") for c in TFF_CODES.values())
    return fetch(f"https://publicreporting.cftc.gov/resource/gpe5-46if.json"
                 f"?$where=cftc_contract_market_code%20in('{codes}')"
                 f"&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=700")

def fetch_disagg():
    codes = "','".join(DISAGG_CODES.values())
    return fetch(f"https://publicreporting.cftc.gov/resource/kh3c-gbw2.json"
                 f"?$where=cftc_contract_market_code%20in('{codes}')"
                 f"&$order=report_date_as_yyyy_mm_dd%20DESC&$limit=200")

def fetch_tradingster(sym):
    try:
        return fetch(f"https://www.tradingster.com/api/cot/legacy-futures/"
                     f"{TRADINGSTER_CODES[sym]}")
    except Exception:
        return []

def fetch_oanda_position_book(pair):
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

def fetch_oanda_all():
    """Returns {sym: (long_pct, short_pct)} for EUR/GBP/JPY/NZD, plus a
    synthesized USD (average of the "USD side" of the four pairs)."""
    out = {}
    for sym, (pair, invert) in OANDA_FOREX_PAIRS.items():
        try:
            long_pct, short_pct = fetch_oanda_position_book(pair)
            if invert:
                long_pct, short_pct = short_pct, long_pct
            out[sym] = (long_pct, short_pct)
        except Exception as e:
            print(f"  OANDA position book failed {sym} ({pair}): {e}")

    usd_sides = []
    for sym, (pair, invert) in OANDA_FOREX_PAIRS.items():
        if sym not in out:
            continue
        long_pct, short_pct = out[sym]
        usd_sides.append(long_pct if invert else short_pct)
    if usd_sides:
        out["USD"] = (sum(usd_sides) / len(usd_sides), 100 - (sum(usd_sides) / len(usd_sides)))
    return out

def fetch_fear_greed():
    data = fetch("https://api.alternative.me/fng/?limit=1")
    row = data["data"][0]
    return int(row["value"]), row["value_classification"]

def fetch_eia_crude_stocks():
    url = (
        "https://api.eia.gov/v2/petroleum/stoc/wstk/data/"
        f"?api_key={EIA_API_KEY}&frequency=weekly&data[0]=value"
        "&facets[product][]=EPC0&facets[process][]=SAX&facets[duoarea][]=NUS"
        "&sort[0][column]=period&sort[0][direction]=desc&length=3"
    )
    data = fetch(url)
    rows = data["response"]["data"]
    return int(rows[0]["value"]), int(rows[1]["value"])

# ── Compute ───────────────────────────────────────────────────────────────────

def cot_index(nets):
    mn, mx = min(nets), max(nets)
    r = mx - mn or 1
    return [round((n - mn) / r * 100) for n in nets]

def _mean(xs): return sum(xs) / len(xs)
def _std(xs):
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))

def lev_sig(deltas):
    if len(deltas) < 2:
        return "--"
    if deltas[-2] < 0 and deltas[-1] > 0:
        return "flip-UP"
    if deltas[-2] > 0 and deltas[-1] < 0:
        return "flip-DN"
    if len(deltas) < 4:
        return "--"
    w = deltas[-4:]
    avg, s = _mean(w), _std(w) or 1
    if deltas[-1] > avg + s * 0.75:
        return "covering"
    if deltas[-1] < avg - s * 0.75:
        return "pressing"
    return "--"

def mm_sig(changes):
    if len(changes) < 4:
        return "--"
    w = changes[-4:]
    avg, s = _mean(w), _std(w) or 1
    if w[-1] < avg - s * 0.75:
        return "closing"
    if w[-1] > avg + s * 0.75:
        return "adding"
    return "--"

def bias_label(idx):
    return "Bullish" if idx >= 60 else "Bearish" if idx <= 40 else "Neutral"

def alignment(b1, b2):
    if "?" in (b1, b2):
        return "?"
    v = {"Bullish": 1, "Neutral": 0, "Bearish": -1}
    if v[b1] == v[b2] and b1 != "Neutral":
        return "CONFIRMED"
    return "DIVERGE" if v[b1] != v[b2] else "NEUTRAL"

def comm_retail_breakdown(leg_rows, is_inv):
    """Commercial (hedgers) vs Non-Reportable (retail) -- CFTC Legacy 3-way
    split, from the same Tradingster rows already fetched for Source 2."""
    if len(leg_rows) < 5:
        return {"comm_idx": "?", "comm_bias": "?", "retail_idx": "?", "retail_bias": "?"}
    comm_nets = [int(r["Commercial_Positions_Long_All"]) - int(r["Commercial_Positions_Short_All"]) for r in leg_rows]
    retail_nets = [int(r["Nonreportable_Positions_Long_All"]) - int(r["Nonreportable_Positions_Short_All"]) for r in leg_rows]
    comm_idxs = cot_index(comm_nets)
    retail_idxs = cot_index(retail_nets)
    if is_inv:
        comm_idxs = [100 - i for i in comm_idxs]
        retail_idxs = [100 - i for i in retail_idxs]
    return {
        "comm_idx": comm_idxs[-1], "comm_bias": bias_label(comm_idxs[-1]),
        "retail_idx": retail_idxs[-1], "retail_bias": bias_label(retail_idxs[-1]),
    }

# ── Narratives ────────────────────────────────────────────────────────────────

def _net_str(n): return f"{'net long' if n > 0 else 'net short'} {abs(n):,}"

def trend_text(sym, d):
    nets  = d["nets5"]
    s2n   = d.get("s2_nets5", [])
    has2  = s2n and s2n[0] != "?"
    n0, n4, chg = nets[0], nets[-1], nets[-1] - nets[0]
    flipped = (n0 > 0) != (n4 > 0)

    parts = []
    if flipped:
        parts.append(f"S1 flipped from {_net_str(n0)} (4 wks ago) to {_net_str(n4)} now. "
                     f"COT Index {d['s1_idx']} ({d['s1_bias']}).")
    else:
        parts.append(f"S1 net {n0:+,} -> {n4:+,} ({chg:+,} over 4 wks). "
                     f"COT Index {d['s1_idx']} ({d['s1_bias']}).")

    lev_desc = {"flip-UP": "Lev Fund delta turned positive (short-covering or new longs).",
                "flip-DN": "Lev Fund delta turned negative (new shorts or long liquidation).",
                "covering": "Lev Funds covering above-average pace.",
                "pressing": "Lev Funds pressing at above-average pace."}
    if d["lev_sig"] in lev_desc:
        parts.append(lev_desc[d["lev_sig"]])

    mm_desc = {"adding": "Asset Managers adding to longs.", "closing": "Asset Managers closing longs."}
    if d["mm_sig"] in mm_desc:
        parts.append(mm_desc[d["mm_sig"]])

    if has2:
        s2_chg = s2n[-1] - s2n[0]
        s2_flipped = (s2n[0] > 0) != (s2n[-1] > 0)
        if s2_flipped:
            parts.append(f"S2 NonComm flipped from {_net_str(s2n[0])} to {_net_str(s2n[-1])}. "
                         f"COT Index {d['s2_idx']} ({d['s2_bias']}).")
        else:
            parts.append(f"S2 NonComm net {s2n[0]:+,} -> {s2n[-1]:+,} ({s2_chg:+,}). "
                         f"COT Index {d['s2_idx']} ({d['s2_bias']}).")

    if d.get("comm_bias", "?") != "?":
        parts.append(f"Commercial (hedgers) IDX {d['comm_idx']} ({d['comm_bias']}), "
                     f"Retail (non-reportable) IDX {d['retail_idx']} ({d['retail_bias']}).")

    return " ".join(parts)


def third_source_text(sym, d):
    """Genuinely independent, non-CFTC third check -- only present for
    forex (OANDA), BTC (Fear & Greed), and CRUDE (EIA). None for
    indices or Gold/Silver -- no viable free source exists, see
    cot_fetch.py docstring."""
    if d.get("oanda_bias"):
        return (f"OANDA clients: {d['oanda_long_pct']:.1f}% long / {d['oanda_short_pct']:.1f}% short "
                f"({d['oanda_bias']}) -- independent broker order book, not CFTC data.")
    if d.get("fng_value") is not None:
        return (f"Crypto Fear & Greed Index: {d['fng_value']}/100 ({d['fng_classification']}) -- "
                f"independent retail/market sentiment gauge, not CFTC data.")
    if d.get("eia_bias"):
        chg = d["eia_now"] - d["eia_prev"]
        return (f"EIA weekly crude stocks: {chg:+,} thousand barrels ({d['eia_bias']}) -- "
                f"real supply/demand fundamentals, not positioning data.")
    return None


def analysis_text(sym, d):
    align, s1b, s2b = d.get("alignment","?"), d["s1_bias"], d.get("s2_bias","?")
    s1i, s2i = d["s1_idx"], d.get("s2_idx", 50)
    lev  = d["lev_sig"]
    nets = d["nets5"]
    s2n  = d.get("s2_nets5", [])
    has2 = s2n and s2n[0] != "?"

    parts = []

    if align == "CONFIRMED":
        d_word = s1b.lower() if s1b != "Neutral" else "neutral"
        parts.append(f"Both CFTC and Tradingster confirm {d_word} institutional positioning -- "
                     f"directional read is reliable.")
    elif align == "DIVERGE":
        parts.append(f"Source divergence: CFTC {s1b} (IDX {s1i}) vs Tradingster {s2b} (IDX {s2i}). "
                     f"Monitor for one to confirm the other before full conviction.")
    elif align == "NEUTRAL":
        parts.append("Both sources neutral -- no COT directional edge.")

    if isinstance(s1i, int) and s1i <= 5:
        parts.append(f"CFTC IDX={s1i} near historical bearish extreme -- crowd very one-sided short; "
                     f"contrarian squeeze risk is elevated.")
    elif isinstance(s1i, int) and s1i >= 95:
        parts.append(f"CFTC IDX={s1i} near historical bullish extreme -- crowd fully positioned long; "
                     f"exhaustion or reversal risk elevated.")

    if isinstance(s2i, int) and has2 and s2i <= 5:
        parts.append(f"NonComm IDX={s2i} also at bearish extreme -- broad spec group maximally short.")
    elif isinstance(s2i, int) and has2 and s2i >= 95:
        parts.append(f"NonComm IDX={s2i} also at bullish extreme -- broad spec group maximally long.")

    flip_ctx = {
        "flip-UP": ("Lev Funds crossed from net short to net long -- significant momentum shift."
                    if len(nets) >= 2 and nets[-2] < 0 and nets[-1] > 0
                    else "Lev Fund weekly delta crossed positive -- covering or fresh long entry."),
        "flip-DN": ("Lev Funds crossed from net long to net short -- significant momentum shift."
                    if len(nets) >= 2 and nets[-2] > 0 and nets[-1] < 0
                    else "Lev Fund weekly delta crossed negative -- new shorts or long liquidation."),
    }
    if lev in flip_ctx:
        parts.append(flip_ctx[lev])

    if has2 and len(s2n) >= 2 and (s2n[-2] > 0) != (s2n[-1] > 0):
        direction = "net long" if s2n[-1] > 0 else "net short"
        parts.append(f"NonComm crossed to {direction} last week -- notable shift in broader spec group.")

    # Commercial/Retail divergence from the crowd (e.g. retail one-sided while commercials aren't)
    comm_b, retail_b = d.get("comm_bias", "?"), d.get("retail_bias", "?")
    if comm_b != "?" and retail_b != "?" and comm_b != retail_b:
        parts.append(f"Commercials ({comm_b}) and Retail ({retail_b}) disagree -- worth noting who's "
                     f"on which side of this move.")

    # Third-source vs. Source 1 divergence (institutional vs. independent retail/fundamental read)
    ts_bias = d.get("oanda_bias") or (
        ("Bearish" if isinstance(d.get("fng_value"), int) and d["fng_value"] <= 25
         else "Bullish" if isinstance(d.get("fng_value"), int) and d["fng_value"] >= 75
         else None) if d.get("fng_value") is not None else None
    ) or d.get("eia_bias")
    if ts_bias and s1b != "Neutral" and ts_bias != "?" and ts_bias != s1b:
        parts.append(f"Third-source check ({ts_bias}) diverges from Source 1 ({s1b}) -- "
                     f"institutional COT positioning and the independent check disagree.")

    return " ".join(parts) or "Insufficient data for clear analysis."


def this_week_text(sym, d):
    align, s1b = d.get("alignment","?"), d["s1_bias"]
    s2b = d.get("s2_bias","?")
    s1i, lev = d["s1_idx"], d["lev_sig"]
    extreme = isinstance(s1i, int) and (s1i <= 5 or s1i >= 95)

    if align == "CONFIRMED" and s1b == "Bullish":
        base = "Bullish. Both sources aligned."
        if extreme and s1i >= 95:
            return base + f" IDX={s1i} extreme -- watch for exhaustion; favor pullback entries."
        if lev == "flip-UP":
            return base + " Flip-UP confirms fresh momentum. Buy dips."
        return base + " COT supports upside."

    if align == "CONFIRMED" and s1b == "Bearish":
        base = "Bearish. Both sources aligned."
        if extreme and s1i <= 5:
            return (base + f" IDX={s1i} near extreme -- crowd very one-sided; "
                    f"sell-side momentum intact but do not add fresh shorts at these levels.")
        if lev == "flip-DN":
            return base + " Flip-DN confirms fresh shorts. Sell rallies."
        if lev == "pressing":
            return base + " Pressing signal -- sellers accelerating. Sell rallies."
        return base + " COT supports downside."

    if align == "CONFIRMED" and s1b == "Neutral":
        return "Neutral. Both sources agree -- no COT directional edge this week."

    if align == "DIVERGE":
        if s1b == "Bearish" and extreme and s1i <= 5:
            return (f"Bearish S1 at IDX={s1i} near extreme. Sources diverge -- "
                    f"avoid conviction shorts; contrarian risk elevated.")
        if s1b == "Bullish" and extreme and s1i >= 95:
            return (f"Bullish S1 at IDX={s1i} near extreme. Sources diverge -- "
                    f"avoid chasing longs; confirmation needed from S2.")
        if s1b != "Neutral":
            return (f"{s1b} lean from CFTC (IDX {s1i}) but Tradingster diverges ({s2b}). "
                    f"Trade S1 direction with reduced size until NonComm confirms.")
        return "Sources diverge around neutral. No clear edge -- wait for convergence."

    return "Neutral. No COT edge this week. Use price structure alone for entries."


def next_week_text(sym, d, next_date_str):
    align, s1b = d.get("alignment","?"), d["s1_bias"]
    s1i, lev, mm = d["s1_idx"], d["lev_sig"], d["mm_sig"]
    extreme = isinstance(s1i, int) and (s1i <= 5 or s1i >= 95)
    nxt = next_date_str  # e.g. "Jun 30"

    if align == "CONFIRMED":
        if s1b == "Bullish":
            base = f"Bullish continuation expected through {nxt}."
            if mm == "adding":
                base += " Asset Manager accumulation supports medium-term upside."
            return base
        if s1b == "Bearish":
            base = f"Bearish continuation expected through {nxt}."
            if extreme:
                base += (f" IDX={s1i} -- watch next CFTC release for short-covering "
                         f"acceleration which would signal a potential squeeze.")
            if mm == "closing":
                base += " Asset Manager selling confirms distribution."
            return base
        return f"Neutral through {nxt}. Watch for a source to break out of neutral zone."

    if align == "DIVERGE":
        watch = ("S2 NonComm follow S1 into " + s1b if s1b != "Neutral"
                 else "sources converge directionally")
        base = (f"Key watch for {nxt}: will {watch}? "
                f"Next CFTC release will clarify.")
        if extreme:
            base += (f" With IDX={s1i} at extremes, a reversal would first show "
                     f"up as short-covering in next week's data.")
        return base

    return (f"Neutral through {nxt}. "
            f"Wait for one source to establish directional bias before committing.")

# ── PDF styles ────────────────────────────────────────────────────────────────

def make_styles():
    def s(name, **kw):
        st = ParagraphStyle(name, fontName="Helvetica", fontSize=9, leading=13)
        for k, v in kw.items(): setattr(st, k, v)
        return st
    return {
        "H1":   s("H1",  fontSize=16, fontName="Helvetica-Bold", spaceBefore=4,
                  spaceAfter=4,  textColor=colors.HexColor("#1a1a2e")),
        "H2":   s("H2",  fontSize=11, fontName="Helvetica-Bold", spaceBefore=8,
                  spaceAfter=3,  textColor=colors.HexColor("#16213e")),
        "META": s("META",fontSize=8,  textColor=colors.HexColor("#555555"), spaceAfter=5),
        "BODY": s("BODY",fontSize=8.5,leading=13, spaceBefore=2, spaceAfter=2, leftIndent=5),
        "LBL":  s("LBL", fontSize=8.5,fontName="Helvetica-Bold", leftIndent=5,
                  textColor=colors.HexColor("#333333"), spaceBefore=3, spaceAfter=0),
        "SSEC": s("SSEC",fontSize=9, fontName="Helvetica-BoldOblique",
                  textColor=colors.HexColor("#666666"), spaceBefore=6, spaceAfter=2),
    }

C_BULL = colors.HexColor("#e8f5e9")
C_BEAR = colors.HexColor("#fce8e8")
C_NEUT = colors.HexColor("#fffde7")
C_HDR  = colors.HexColor("#16213e")
C_ALT  = colors.HexColor("#f5f5f5")
C_GRID = colors.HexColor("#cccccc")
C_SYM  = colors.HexColor("#eef2f7")

def bc(bias): return C_BULL if "Bull" in str(bias) else C_BEAR if "Bear" in str(bias) else C_NEUT
def ac(aln):  return C_BULL if "CONF" in str(aln) else C_BEAR if "DIV"  in str(aln) else C_NEUT

TH = ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=8, textColor=colors.white,
                    alignment=TA_CENTER, leading=10)
TD = ParagraphStyle("TD", fontName="Helvetica", fontSize=8, alignment=TA_RIGHT, leading=10)
TS = ParagraphStyle("TS", fontName="Helvetica-Bold", fontSize=8, alignment=TA_LEFT,  leading=10)
TL = ParagraphStyle("TL", fontName="Helvetica", fontSize=8, alignment=TA_LEFT,  leading=10)

ph = lambda t: Paragraph(str(t), TH)
pd_ = lambda t: Paragraph(str(t), TD)
ps_ = lambda t: Paragraph(str(t), TS)
pl_ = lambda t: Paragraph(str(t), TL)

BASE_TS = [
    ("BACKGROUND", (0,0),(-1,0), C_HDR),
    ("TEXTCOLOR",  (0,0),(-1,0), colors.white),
    ("FONTSIZE",   (0,0),(-1,-1), 8),
    ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, C_ALT]),
    ("GRID",       (0,0),(-1,-1), 0.4, C_GRID),
    ("LEFTPADDING",(0,0),(-1,-1), 3), ("RIGHTPADDING",(0,0),(-1,-1),3),
    ("TOPPADDING", (0,0),(-1,-1), 2), ("BOTTOMPADDING",(0,0),(-1,-1),2),
    ("VALIGN",     (0,0),(-1,-1),"MIDDLE"),
]

# ── PDF builder ───────────────────────────────────────────────────────────────

def build_pdf(result, today, next_date_str, latest_report_date):
    W = A4[0] - 30*mm
    fname = f"COT_Breakdown_{latest_report_date}.pdf"
    out   = os.path.join(OUTPUT_DIR, fname)
    doc   = SimpleDocTemplate(out, pagesize=A4,
                leftMargin=15*mm, rightMargin=15*mm,
                topMargin=15*mm, bottomMargin=15*mm)

    sty = make_styles()
    story = []

    today_str = today.strftime("%B %d, %Y").replace(" 0", " ")
    report_str   = datetime.strptime(latest_report_date, "%Y-%m-%d").strftime("%B %d, %Y").replace(" 0"," ")

    story.append(Paragraph("COT DUAL-SOURCE + COMMERCIAL/RETAIL BREAKDOWN", sty["H1"]))
    story.append(Paragraph(
        f"Data: Week Ending {report_str}  |  Analysis: {today_str}", sty["META"]))
    story.append(Paragraph(
        "<b>Source 1:</b> CFTC TFF -- Leveraged Funds (hedge funds/CTAs) and Asset Managers; "
        "Disaggregated Managed Money for GOLD/SILVER/CRUDE.  "
        "<b>Source 2:</b> Tradingster Legacy Non-Commercial (broader spec group = Lev Fund + Asset Mgr combined).  "
        "<b>Commercial/Retail:</b> CFTC Legacy report's classic 3-way split (hedgers vs. large specs vs. small/retail "
        "traders), same underlying Tradingster data as Source 2.  "
        "<b>Third Source:</b> a genuinely independent, non-CFTC check where a free one exists -- OANDA position "
        "book (forex), Crypto Fear &amp; Greed Index (BTC), EIA weekly crude stocks (Oil). No free independent source "
        "exists for indices or Gold/Silver.  "
        "<b>COT Index:</b> Larry Williams percentile over full history -- Bullish >=60 | Bearish <=40 | Neutral 40-60.",
        sty["META"]))
    story.append(HRFlowable(width=W, thickness=1.5, color=colors.HexColor("#1a1a2e")))
    story.append(Spacer(1, 4))

    # ─ Source 1 table
    story.append(Paragraph("Source 1 -- CFTC Lev Fund / Managed Money  (net contracts)", sty["H2"]))
    ref = result[ORDER[0]]
    col_dates = ref["dates"]  # e.g. ["05-19","05-26","06-02","06-09","06-16"]
    hdr = [ph(h) for h in ["SYM"] + col_dates + ["IDX","BIAS","LEV SIG","AM SIG"]]
    cw  = [12*mm,20*mm,20*mm,20*mm,20*mm,20*mm,11*mm,18*mm,20*mm,19*mm]
    ts1 = list(BASE_TS)
    rows1 = [hdr]
    for i, sym in enumerate(ORDER):
        if sym not in result: continue
        d = result[sym]
        rows1.append([ps_(sym)] + [pd_(n) for n in d["nets5"]] +
                     [pd_(d["s1_idx"]), pd_(d["s1_bias"]), pd_(d["lev_sig"]), pd_(d["mm_sig"])])
        ts1.append(("BACKGROUND",(7,i+1),(7,i+1), bc(d["s1_bias"])))
    t1 = Table(rows1, colWidths=cw, repeatRows=1)
    t1.setStyle(TableStyle(ts1))
    story.append(t1)
    story.append(Spacer(1, 6))

    # ─ Source 2 table
    story.append(Paragraph(
        "Source 2 -- Tradingster Non-Commercial  (Lev Fund + Asset Mgr combined)", sty["H2"]))
    hdr2 = [ph(h) for h in ["SYM"] + col_dates + ["IDX","BIAS","ALIGNMENT"]]
    cw2  = [12*mm,22*mm,22*mm,22*mm,22*mm,22*mm,11*mm,18*mm,29*mm]
    ts2  = list(BASE_TS)
    rows2 = [hdr2]
    for i, sym in enumerate(ORDER):
        if sym not in result: continue
        d = result[sym]
        s2n = d.get("s2_nets5", ["?"]*5)
        rows2.append([ps_(sym)] + [pd_(n) for n in s2n] +
                     [pd_(d.get("s2_idx","?")), pd_(d.get("s2_bias","?")), pl_(d.get("alignment","?"))])
        ts2.append(("BACKGROUND",(7,i+1),(7,i+1), bc(d.get("s2_bias","?"))))
        ts2.append(("BACKGROUND",(8,i+1),(8,i+1), ac(d.get("alignment","?"))))
    t2 = Table(rows2, colWidths=cw2, repeatRows=1)
    t2.setStyle(TableStyle(ts2))
    story.append(t2)
    story.append(Spacer(1, 8))

    # ─ Commercial / Retail breakdown table (new 2026-07-10)
    story.append(Paragraph(
        "Commercial (Hedgers) vs. Non-Reportable (Retail) -- CFTC Legacy 3-Way Split", sty["H2"]))
    hdr3 = [ph(h) for h in ["SYM","COMM IDX","COMM BIAS","RETAIL IDX","RETAIL BIAS","COMM vs RETAIL"]]
    cw3  = [16*mm,20*mm,25*mm,20*mm,25*mm,50*mm]
    ts3  = list(BASE_TS)
    rows3 = [hdr3]
    for i, sym in enumerate(ORDER):
        if sym not in result: continue
        d = result[sym]
        comm_b, retail_b = d.get("comm_bias","?"), d.get("retail_bias","?")
        agree = ("Agree" if comm_b == retail_b and comm_b != "?"
                 else "Diverge" if comm_b != "?" and retail_b != "?" and comm_b != retail_b
                 else "?")
        rows3.append([ps_(sym), pd_(d.get("comm_idx","?")), pd_(comm_b),
                     pd_(d.get("retail_idx","?")), pd_(retail_b), pl_(agree)])
        ts3.append(("BACKGROUND",(2,i+1),(2,i+1), bc(comm_b)))
        ts3.append(("BACKGROUND",(4,i+1),(4,i+1), bc(retail_b)))
    t3 = Table(rows3, colWidths=cw3, repeatRows=1)
    t3.setStyle(TableStyle(ts3))
    story.append(t3)
    story.append(Spacer(1, 8))

    # ─ Third-source independent check table (new 2026-07-10)
    story.append(Paragraph(
        "Third-Source Independent Check (non-CFTC -- forex/BTC/Oil only, see note above)", sty["H2"]))
    hdr4 = [ph(h) for h in ["SYM","SOURCE","READING","BIAS"]]
    cw4  = [16*mm,35*mm,70*mm,35*mm]
    ts4  = list(BASE_TS)
    rows4 = [hdr4]
    has_third = False
    for i, sym in enumerate(ORDER):
        if sym not in result: continue
        d = result[sym]
        if d.get("oanda_bias"):
            has_third = True
            rows4.append([ps_(sym), pl_("OANDA Position Book"),
                         pl_(f"{d['oanda_long_pct']:.1f}% Long / {d['oanda_short_pct']:.1f}% Short"),
                         pd_(d["oanda_bias"])])
            ts4.append(("BACKGROUND",(3,len(rows4)-1),(3,len(rows4)-1), bc(d["oanda_bias"])))
        elif d.get("fng_value") is not None:
            has_third = True
            fbias = "Bearish" if d["fng_value"] <= 25 else "Bullish" if d["fng_value"] >= 75 else "Neutral"
            rows4.append([ps_(sym), pl_("Crypto Fear & Greed"),
                         pl_(f"{d['fng_value']}/100 ({d['fng_classification']})"),
                         pd_(fbias)])
            ts4.append(("BACKGROUND",(3,len(rows4)-1),(3,len(rows4)-1), bc(fbias)))
        elif d.get("eia_bias"):
            has_third = True
            chg = d["eia_now"] - d["eia_prev"]
            rows4.append([ps_(sym), pl_("EIA Weekly Crude Stocks"),
                         pl_(f"{chg:+,} thousand bbl"),
                         pd_(d["eia_bias"])])
            ts4.append(("BACKGROUND",(3,len(rows4)-1),(3,len(rows4)-1), bc(d["eia_bias"])))
    if has_third:
        t4 = Table(rows4, colWidths=cw4, repeatRows=1)
        t4.setStyle(TableStyle(ts4))
        story.append(t4)
    else:
        story.append(Paragraph("No third-source data available this run.", sty["META"]))
    story.append(Spacer(1, 8))

    # ─ Narratives
    story.append(HRFlowable(width=W, thickness=1, color=colors.HexColor("#aaaaaa")))
    story.append(Spacer(1, 3))
    story.append(Paragraph("Instrument-by-Instrument Analysis", sty["H2"]))

    last_sec = None
    for sym in ORDER:
        if sym not in result: continue
        d = result[sym]
        sec = SECTION_MAP.get(sym,"")
        if sec != last_sec:
            story.append(Spacer(1,2))
            story.append(Paragraph(f"-- {sec} --", sty["SSEC"]))
            last_sec = sec

        align = d.get("alignment","?")
        s1b   = d["s1_bias"]
        s2b   = d.get("s2_bias","?")
        s1i   = d["s1_idx"]
        s2i   = d.get("s2_idx","?")

        def col(bias):
            return (colors.HexColor("#1a7a4a") if "Bull" in str(bias) else
                    colors.HexColor("#b22222") if "Bear" in str(bias) else
                    colors.HexColor("#7a5c00"))
        def acol(aln):
            return (colors.HexColor("#1a7a4a") if "CONF" in str(aln) else
                    colors.HexColor("#b22222") if "DIV"  in str(aln) else
                    colors.HexColor("#7a5c00"))

        hrow = [[
            Paragraph(sym, ParagraphStyle(f"s_{sym}", fontSize=12, fontName="Helvetica-Bold",
                                          textColor=colors.HexColor("#0f3460"))),
            Paragraph(f"S1: <b>{s1b}</b>  IDX {s1i}",
                      ParagraphStyle(f"b1_{sym}", fontSize=8.5, fontName="Helvetica-Bold",
                                     textColor=col(s1b))),
            Paragraph(f"S2: <b>{s2b}</b>  IDX {s2i}",
                      ParagraphStyle(f"b2_{sym}", fontSize=8.5, fontName="Helvetica-Bold",
                                     textColor=col(s2b))),
            Paragraph(align,
                      ParagraphStyle(f"al_{sym}", fontSize=8.5, fontName="Helvetica-Bold",
                                     textColor=acol(align))),
        ]]
        ht = Table(hrow, colWidths=[18*mm, 52*mm, 52*mm, 58*mm])
        ht.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0), C_SYM),
            ("TOPPADDING",(0,0),(-1,0),3),("BOTTOMPADDING",(0,0),(-1,0),3),
            ("LEFTPADDING",(0,0),(-1,0),5),("VALIGN",(0,0),(-1,0),"MIDDLE"),
            ("LINEBELOW",(0,0),(-1,0),0.6,C_GRID),
            ("BACKGROUND",(1,0),(1,0), bc(s1b)),
            ("BACKGROUND",(2,0),(2,0), bc(s2b)),
            ("BACKGROUND",(3,0),(3,0), ac(align)),
        ]))
        story.append(ht)
        story.append(Paragraph("<b>4-Week Trend:</b>  " + trend_text(sym, d), sty["BODY"]))
        story.append(Paragraph("<b>Analysis:</b>  " + analysis_text(sym, d), sty["BODY"]))
        ts_text = third_source_text(sym, d)
        if ts_text:
            story.append(Paragraph("<b>Third-Source Check:</b>  " + ts_text, sty["BODY"]))
        story.append(Paragraph(f"This week ({today.strftime('%b %d').replace(' 0', ' ')}):",
                               sty["LBL"]))
        story.append(Paragraph(this_week_text(sym, d), sty["BODY"]))
        story.append(Paragraph(f"Next week ({next_date_str}):", sty["LBL"]))
        story.append(Paragraph(next_week_text(sym, d, next_date_str), sty["BODY"]))
        story.append(Spacer(1, 4))

    # ─ Summary table
    story.append(HRFlowable(width=W, thickness=1.5, color=colors.HexColor("#1a1a2e")))
    story.append(Spacer(1, 3))
    tw_label = today.strftime("%b %d").replace(" 0"," ")
    nw_label = next_date_str
    story.append(Paragraph(f"Bias Summary -- {tw_label} -> {nw_label}", sty["H2"]))

    shdr = [ph(h) for h in ["SYM","S1 IDX","S2 IDX","ALIGNMENT",
                             f"THIS WEEK ({tw_label})", f"NEXT WEEK ({nw_label})"]]
    scw  = [14*mm,14*mm,14*mm,24*mm,54*mm,60*mm]
    sts  = list(BASE_TS)
    srows = [shdr]
    for i, sym in enumerate(ORDER):
        if sym not in result: continue
        d = result[sym]
        al = d.get("alignment","?")
        tw = this_week_text(sym, d)
        nw = next_week_text(sym, d, next_date_str)
        srows.append([ps_(sym), pd_(d["s1_idx"]), pd_(d.get("s2_idx","?")),
                      pl_(al), pl_(tw), pl_(nw)])
        sts.append(("BACKGROUND",(3,i+1),(3,i+1), ac(al)))
    st = Table(srows, colWidths=scw, repeatRows=1)
    st.setStyle(TableStyle(sts))
    story.append(st)

    story.append(Spacer(1, 6))

    # Key themes
    extremes = [sym for sym in ORDER
                if sym in result and isinstance(result[sym]["s1_idx"], int)
                and result[sym]["s1_idx"] <= 5]
    tops     = [sym for sym in ORDER
                if sym in result and isinstance(result[sym]["s1_idx"], int)
                and result[sym]["s1_idx"] >= 95]
    confirmed_bull = [sym for sym in ORDER
                      if sym in result and result[sym].get("alignment")=="CONFIRMED"
                      and result[sym]["s1_bias"]=="Bullish"]
    confirmed_bear = [sym for sym in ORDER
                      if sym in result and result[sym].get("alignment")=="CONFIRMED"
                      and result[sym]["s1_bias"]=="Bearish"]
    diverge = [sym for sym in ORDER
               if sym in result and result[sym].get("alignment")=="DIVERGE"]
    ts_diverge = [sym for sym in ORDER
                  if sym in result and result[sym].get("s1_bias") != "Neutral"
                  and (result[sym].get("oanda_bias") or result[sym].get("eia_bias")
                       or result[sym].get("fng_value") is not None)
                  and third_source_text(sym, result[sym])
                  and "diverge" in analysis_text(sym, result[sym]).lower()]

    themes = []
    if extremes:
        themes.append(f"<b>Extreme bearish positioning (IDX <=5):</b> {', '.join(extremes)} -- "
                      f"crowd very one-sided; contrarian squeeze risk elevated.")
    if tops:
        themes.append(f"<b>Extreme bullish positioning (IDX >=95):</b> {', '.join(tops)} -- "
                      f"crowd fully long; exhaustion risk elevated.")
    if confirmed_bull:
        themes.append(f"<b>CONFIRMED Bullish:</b> {', '.join(confirmed_bull)}.")
    if confirmed_bear:
        themes.append(f"<b>CONFIRMED Bearish:</b> {', '.join(confirmed_bear)}.")
    if diverge:
        themes.append(f"<b>Diverging sources (watch closely):</b> {', '.join(diverge)}.")
    if ts_diverge:
        themes.append(f"<b>Institutional vs. independent third-source divergence:</b> {', '.join(ts_diverge)} -- "
                      f"COT positioning disagrees with the non-CFTC check.")

    if themes:
        story.append(Paragraph(
            "<b>Key themes this week:</b>  " + "  ".join(themes), sty["BODY"]))

    story.append(Spacer(1, 6))
    story.append(Paragraph(
        f"Report generated: {today.strftime('%B %d, %Y').replace(' 0',' ')}  |  "
        f"CFTC data: positions as of {report_str}  |  "
        f"Next CFTC release: Friday after {next_date_str} (positions as of Tue {next_date_str})",
        sty["META"]))

    doc.build(story)
    return out

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Fetching CFTC TFF data...")
    tff_raw = fetch_tff()
    print(f"  {len(tff_raw)} rows")

    print("Fetching CFTC Disaggregated data...")
    disagg_raw = fetch_disagg()
    print(f"  {len(disagg_raw)} rows")

    print("Fetching Tradingster Non-Commercial data...")
    leg_raw = {}
    for sym in ORDER:
        r = fetch_tradingster(sym)
        leg_raw[sym] = r if isinstance(r, list) else []
        print(f"  {sym}: {len(leg_raw[sym])} rows")

    print("Fetching OANDA position book (forex)...")
    oanda_pos = fetch_oanda_all()
    print(f"  {len(oanda_pos)} pairs")

    print("Fetching Crypto Fear & Greed Index (BTC)...")
    try:
        fng_value, fng_class = fetch_fear_greed()
        print(f"  {fng_value}/100 ({fng_class})")
    except Exception as e:
        print(f"  failed: {e}")
        fng_value, fng_class = None, None

    print("Fetching EIA weekly crude stocks (Oil)...")
    try:
        eia_now, eia_prev = fetch_eia_crude_stocks()
        print(f"  now={eia_now} prev={eia_prev}")
    except Exception as e:
        print(f"  failed: {e}")
        eia_now, eia_prev = None, None

    # ── Process Source 1 (CFTC) ───────────────────────────────────────────────
    result = {}

    for sym, code in TFF_CODES.items():
        is_inv = sym in INVERT
        rows = sorted(
            [r for r in tff_raw if r.get("cftc_contract_market_code") == code],
            key=lambda r: r["report_date_as_yyyy_mm_dd"])
        if len(rows) < 5: continue

        nets  = [int(r["lev_money_positions_long"]) - int(r["lev_money_positions_short"]) for r in rows]
        idxs  = cot_index(nets)
        if is_inv: idxs = [100 - i for i in idxs]

        l5     = rows[-5:]
        nets5  = [int(r["lev_money_positions_long"]) - int(r["lev_money_positions_short"]) for r in l5]
        dates5 = [r["report_date_as_yyyy_mm_dd"][5:10] for r in l5]

        deltas = [int(r["change_in_lev_money_long"]) - int(r["change_in_lev_money_short"]) for r in rows]
        if is_inv: deltas = [-d for d in deltas]

        am_ch = [] if is_inv else [int(r.get("change_in_asset_mgr_long", 0)) for r in rows]

        result[sym] = {
            "dates":   dates5,
            "nets5":   nets5,
            "s1_idx":  idxs[-1],
            "s1_bias": bias_label(idxs[-1]),
            "lev_sig": lev_sig(deltas),
            "mm_sig":  mm_sig(am_ch) if am_ch else "--",
        }

    for sym, code in DISAGG_CODES.items():
        rows = sorted(
            [r for r in disagg_raw if r.get("cftc_contract_market_code") == code],
            key=lambda r: r["report_date_as_yyyy_mm_dd"])
        if len(rows) < 5: continue

        nets  = [int(r["m_money_positions_long_all"]) - int(r["m_money_positions_short_all"]) for r in rows]
        idxs  = cot_index(nets)
        l5     = rows[-5:]
        nets5  = [int(r["m_money_positions_long_all"]) - int(r["m_money_positions_short_all"]) for r in l5]
        dates5 = [r["report_date_as_yyyy_mm_dd"][5:10] for r in l5]
        deltas = [int(r["change_in_m_money_long_all"]) - int(r["change_in_m_money_short_all"]) for r in rows]

        result[sym] = {
            "dates":   dates5,
            "nets5":   nets5,
            "s1_idx":  idxs[-1],
            "s1_bias": bias_label(idxs[-1]),
            "lev_sig": lev_sig(deltas),
            "mm_sig":  "--",
        }

    # ── Process Source 2 (Tradingster) + Commercial/Retail ───────────────────
    for sym in ORDER:
        if sym not in result: continue
        is_inv = sym in INVERT
        lr = leg_raw.get(sym, [])
        if len(lr) < 5:
            result[sym].update({"s2_nets5":["?"]*5,"s2_idx":"?","s2_bias":"?","alignment":"?"})
            result[sym].update(comm_retail_breakdown(lr, is_inv))
            continue

        all_nets = [int(r["Noncommercial_Positions_Long_All"]) - int(r["Noncommercial_Positions_Short_All"])
                    for r in lr]
        idxs = cot_index(all_nets)
        if is_inv: idxs = [100 - i for i in idxs]

        result[sym]["s2_nets5"] = all_nets[-5:]
        result[sym]["s2_idx"]   = idxs[-1]
        result[sym]["s2_bias"]  = bias_label(idxs[-1])
        result[sym]["alignment"]= alignment(result[sym]["s1_bias"], result[sym]["s2_bias"])
        result[sym].update(comm_retail_breakdown(lr, is_inv))

    # ── Third-source checks ───────────────────────────────────────────────────
    for sym, (long_pct, short_pct) in oanda_pos.items():
        if sym in result:
            result[sym]["oanda_long_pct"] = long_pct
            result[sym]["oanda_short_pct"] = short_pct
            result[sym]["oanda_bias"] = bias_label(long_pct)

    if "BTC" in result and fng_value is not None:
        result["BTC"]["fng_value"] = fng_value
        result["BTC"]["fng_classification"] = fng_class

    if "CRUDE" in result and eia_now is not None:
        change = eia_now - eia_prev
        result["CRUDE"]["eia_now"] = eia_now
        result["CRUDE"]["eia_prev"] = eia_prev
        result["CRUDE"]["eia_bias"] = "Bullish" if change < -2000 else "Bearish" if change > 2000 else "Neutral"

    # ── Dates ─────────────────────────────────────────────────────────────────
    latest_report_date = result[ORDER[0]]["dates"][-1]  # "06-16"
    # Reconstruct full date from CFTC row
    ref_sym = ORDER[0]
    ref_rows = sorted(
        [r for r in tff_raw if r.get("cftc_contract_market_code") == TFF_CODES[ref_sym]],
        key=lambda r: r["report_date_as_yyyy_mm_dd"])
    full_latest = ref_rows[-1]["report_date_as_yyyy_mm_dd"][:10]  # "2026-06-16"

    today     = datetime.now()
    next_date = today + timedelta(days=7)
    next_date_str = next_date.strftime("%b %d").replace(" 0", " ")

    # ── Build PDF ─────────────────────────────────────────────────────────────
    print("Generating PDF...")
    out = build_pdf(result, today, next_date_str, full_latest)
    print(f"\nDone. PDF saved to:\n  {out}")

    # Open automatically
    import subprocess
    subprocess.Popen(["start", "", out], shell=True)

if __name__ == "__main__":
    main()
