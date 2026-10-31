#!/usr/bin/env python3
"""
Trading_Plan_2026-11-02_2026-11-03_NQ_Gold.pdf generator.
Mirrors trading_plan_generate_20260911.py's visual style. Data pulled live
this session (2026-10-30): current-week COT Index / bias / alignment /
Commercial + Retail index and cot_setup from the live /cot dashboard
endpoint (report week ending 2026-10-27); Lev Funds (NQ, TFF 209742) and
Managed Money (Gold, Disaggregated 088691) 5-week net series fetched fresh
from publicreporting.cftc.gov with cot_fetch.py's exact queries (index
values recomputed over the same window match the dashboard: NQ 86, Gold
20); Tradingster legacy-futures fetched for both (Source 2 NonComm 69 /
57, Commercial 28 / 41, Retail 75 / 54 -- match the dashboard). Weekly
deltas are net-position differences vs the prior week (the 09/29 delta is
vs the 09/22 net: NQ -30,683, Gold 131,334). Streaks checked against the
full series: NQ Lev Funds covered 5 straight weeks (09/29-10/27); Gold MM
net long fell 9 straight weeks (09/01-10/27, from 151,315 on 08/25).
Bias per cot_rules.py: neither instrument has a COT setup this week.
"""
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                Table, TableStyle, HRFlowable)
from reportlab.lib.enums import TA_LEFT, TA_RIGHT, TA_CENTER

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))

C_BULL = colors.HexColor("#e8f5e9")
C_BEAR = colors.HexColor("#fce8e8")
C_NEUT = colors.HexColor("#fffde7")
C_HDR  = colors.HexColor("#16213e")
C_ALT  = colors.HexColor("#f5f5f5")
C_GRID = colors.HexColor("#cccccc")
C_SYM  = colors.HexColor("#eef2f7")
C_PLAN = colors.HexColor("#eaf2ff")

def bc(bias): return C_BULL if "Bull" in str(bias) else C_BEAR if "Bear" in str(bias) else C_NEUT

def make_styles():
    def s(name, **kw):
        st = ParagraphStyle(name, fontName="Helvetica", fontSize=9, leading=13)
        for k, v in kw.items(): setattr(st, k, v)
        return st
    return {
        "H1":   s("H1",  fontSize=17, fontName="Helvetica-Bold", spaceBefore=4,
                  spaceAfter=4,  textColor=colors.HexColor("#1a1a2e")),
        "H2sym":s("H2sym",fontSize=13, fontName="Helvetica-Bold", spaceBefore=10,
                  spaceAfter=2,  textColor=colors.HexColor("#0f3460")),
        "META": s("META",fontSize=8.5,textColor=colors.HexColor("#555555"), spaceAfter=6),
        "BODY": s("BODY",fontSize=9,leading=13.5, spaceBefore=2, spaceAfter=2, leftIndent=5),
        "BULLET":s("BULLET",fontSize=9,leading=13.5, spaceBefore=3, spaceAfter=3, leftIndent=10, bulletIndent=0),
        "LBL":  s("LBL", fontSize=9.5,fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=2,
                  textColor=colors.HexColor("#16213e")),
        "PLAN": s("PLAN",fontSize=9,leading=13.5, spaceBefore=3, spaceAfter=3, leftIndent=6, rightIndent=6),
        "TABHDR": s("TABHDR", fontSize=9, fontName="Helvetica-Bold", spaceBefore=6, spaceAfter=3,
                    textColor=colors.HexColor("#16213e")),
    }

TH = ParagraphStyle("TH", fontName="Helvetica-Bold", fontSize=8.5, textColor=colors.white, alignment=TA_CENTER, leading=10)
TD = ParagraphStyle("TD", fontName="Helvetica", fontSize=8.5, alignment=TA_RIGHT, leading=10)
TS = ParagraphStyle("TS", fontName="Helvetica-Bold", fontSize=8.5, alignment=TA_LEFT, leading=10)

ph = lambda t: Paragraph(str(t), TH)
pd_ = lambda t: Paragraph(str(t), TD)
ps_ = lambda t: Paragraph(str(t), TS)

BASE_TS = [
    ("BACKGROUND", (0,0),(-1,0), C_HDR),
    ("TEXTCOLOR",  (0,0),(-1,0), colors.white),
    ("ROWBACKGROUNDS",(0,1),(-1,-1),[colors.white, C_ALT]),
    ("GRID",       (0,0),(-1,-1), 0.4, C_GRID),
    ("LEFTPADDING",(0,0),(-1,-1), 4), ("RIGHTPADDING",(0,0),(-1,-1),4),
    ("TOPPADDING", (0,0),(-1,-1), 3), ("BOTTOMPADDING",(0,0),(-1,-1),3),
    ("VALIGN",     (0,0),(-1,-1),"MIDDLE"),
]

def badge_row(label1, val1, bias1, label2, val2, bias2, alignment):
    align_color = C_BULL if "CONF" in alignment else C_NEUT if "NEUT" in alignment else C_BEAR
    row = [[
        Paragraph(f"<b>{label1}:</b> {val1}",
                  ParagraphStyle("b1", fontSize=9, fontName="Helvetica-Bold",
                                 textColor=colors.HexColor("#1a7a4a") if "Bull" in bias1 else
                                           colors.HexColor("#b22222") if "Bear" in bias1 else
                                           colors.HexColor("#7a5c00"))),
        Paragraph(f"<b>{label2}:</b> {val2}",
                  ParagraphStyle("b2", fontSize=9, fontName="Helvetica-Bold",
                                 textColor=colors.HexColor("#1a7a4a") if "Bull" in bias2 else
                                           colors.HexColor("#b22222") if "Bear" in bias2 else
                                           colors.HexColor("#7a5c00"))),
        Paragraph(f"<b>{alignment}</b>",
                  ParagraphStyle("al", fontSize=9, fontName="Helvetica-Bold",
                                 textColor=colors.HexColor("#1a7a4a") if "CONF" in alignment else
                                           colors.HexColor("#7a5c00") if "NEUT" in alignment else
                                           colors.HexColor("#b22222"))),
    ]]
    t = Table(row, colWidths=[75*mm, 75*mm, 35*mm])
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(0,0), bc(bias1)),
        ("BACKGROUND",(1,0),(1,0), bc(bias2)),
        ("BACKGROUND",(2,0),(2,0), align_color),
        ("TOPPADDING",(0,0),(-1,0),5),("BOTTOMPADDING",(0,0),(-1,0),5),
        ("LEFTPADDING",(0,0),(-1,0),6),("VALIGN",(0,0),(-1,0),"MIDDLE"),
        ("BOX",(0,0),(-1,-1),0.5,C_GRID),
    ]))
    return t

def trend_table(title, dates, vals, deltas):
    hdr = [ph("")] + [ph(d) for d in dates]
    row1 = [ps_(title)] + [pd_(f"{v:,}") for v in vals]
    row2 = [ps_("Weekly Δ")] + [pd_(f"{d:+,}") for d in deltas]
    cw = [42*mm] + [27.6*mm]*5
    ts = list(BASE_TS)
    ts.append(("BACKGROUND",(5,1),(5,1), colors.HexColor("#dbeeff")))
    ts.append(("BACKGROUND",(5,2),(5,2), colors.HexColor("#dbeeff")))
    ts.append(("FONTNAME",(5,1),(5,2),"Helvetica-Bold"))
    t = Table([hdr, row1, row2], colWidths=cw)
    t.setStyle(TableStyle(ts))
    return t

def build():
    W = A4[0] - 30*mm
    fname = "Trading_Plan_2026-11-02_2026-11-03_NQ_Gold.pdf"
    out = os.path.join(OUTPUT_DIR, fname)
    doc = SimpleDocTemplate(out, pagesize=A4,
                             leftMargin=15*mm, rightMargin=15*mm,
                             topMargin=15*mm, bottomMargin=15*mm)
    sty = make_styles()
    story = []

    story.append(Paragraph("NQ &amp; GOLD — COT-BASED TRADING PLAN", sty["H1"]))
    story.append(Paragraph(
        "Trading Window: Monday Nov 2 – Tuesday Nov 3, 2026  |  "
        "COT Data: CFTC week ending Oct 27, 2026 (released Fri Oct 30)  |  "
        "Prepared Oct 30, 2026", sty["META"]))
    story.append(HRFlowable(width=W, thickness=1.5, color=colors.HexColor("#1a1a2e")))
    story.append(Spacer(1, 6))

    # ── NQ ──────────────────────────────────────────────────────────────
    story.append(Paragraph("NQ", sty["H2sym"]))
    story.append(badge_row("Lev Funds", "BULLISH (IDX 86)", "Bullish",
                            "Broad NonComm", "BULLISH (IDX 69)", "Bullish", "CONFIRMED"))
    story.append(Spacer(1, 4))
    story.append(Paragraph("4-Week Positioning Trend — Leveraged Funds (net contracts)", sty["TABHDR"]))
    story.append(trend_table("Lev Fund Net",
                              ["09/29","10/06","10/13","10/20","10/27"],
                              [-24723, -19212, -11593, 950, 2301],
                              [5960, 5511, 7619, 12543, 1351]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Positioning Breakdown", sty["LBL"]))
    nq_bullets = [
        "<b>The short-covering run is done, and it has stalled at the top of the range.</b> Leveraged Funds "
        "covered for five straight weeks, from –30,683 net short (09/22) to net long. The biggest week was "
        "10/20 (+12,543), which flipped them to +950. This week added only +1,351 (to +2,301). The COT "
        "Index went 60 → 65 → 73 → 84 → 86, so hedge funds now sit at the long extreme of their 70-week "
        "range.",
        "Source 2 (Tradingster Non-Commercial) is Bullish at IDX 69, so the two sources are "
        "<b>CONFIRMED</b> Bullish. But Source 2 has gone flat while Source 1 climbed: it read 70 → 72 → 69 → 70 "
        "→ 69 over the same five weeks, and Non-Commercial net long slipped from 50,726 to 48,839 this week. "
        "Asset Managers cut their net long from 77,790 to 65,381 (IDX 65 → 36).",
        "Commercials (the hedgers) are net short –62,092, about where they were last week (–63,576). Their "
        "COT Index is 28, which is low but not at the ≤20 extreme. It has held between 25 and 29 for five "
        "weeks.",
        "Retail stays long and keeps adding (+13,253 net vs +12,850, IDX 75 vs 74). Retail and hedge funds "
        "are long while Commercials lean short, so Commercials and the speculators are on opposite sides. "
        "But Commercials are not at an extreme, which is the condition that would turn this into a "
        "setup.",
    ]
    for b in nq_bullets:
        story.append(Paragraph("●&nbsp;&nbsp;" + b, sty["BULLET"]))

    story.append(Paragraph("Read", sty["LBL"]))
    story.append(Paragraph(
        "Positioning in NQ is lopsided but does not yet meet our rule. Hedge funds have gone from heavily short "
        "to their longest reading in the 70-week window, and retail is long and rising. That is the "
        "speculator half of a bearish setup. The other half is missing: Commercials sit at IDX 28, not at "
        "the ≤20 extreme that would show the hedgers pressing against the crowd. Under our rule, a stretched "
        "speculator long is never a lean by itself, in either direction. The covering also slowed sharply "
        "this week (+1,351 after +12,543), so the squeeze has stalled rather than accelerated. Watch "
        "Commercials. If their index drops to 20 or below while hedge funds or retail stay at 80+ (hedge "
        "funds are at 86 now), NQ becomes a Bearish COT setup.", sty["BODY"]))

    plan_tbl = Table([[Paragraph(
        "<b>TRADING PLAN — Mon/Tue</b><br/><br/>"
        "<b>Bias: No COT edge — trade v7 signals on their own at standard size.</b> No COT setup this week. "
        "Commercials are at IDX 28 and the rule needs 20 or lower. Hedge funds (Leveraged Funds IDX 86) are "
        "already stretched long, so only the Commercial side is missing. Take Range Reversal and Box Break "
        "signals in either direction at the normal 0.5% risk, and Mid Continuation at its normal 0.25%. Do "
        "not add size to longs because hedge funds are long, and do not fade them early. If next Friday's "
        "release shows Commercials at 20 or below with hedge funds or retail still at 80+, NQ shorts get "
        "COT support.",
        sty["PLAN"])]], colWidths=[W])
    plan_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), C_PLAN),
        ("BOX",(0,0),(-1,-1),0.6,colors.HexColor("#9fc3e8")),
        ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),
        ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),
    ]))
    story.append(Spacer(1, 4))
    story.append(plan_tbl)
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width=W, thickness=1, color=colors.HexColor("#aaaaaa")))

    # ── GOLD ────────────────────────────────────────────────────────────
    story.append(Paragraph("GOLD", sty["H2sym"]))
    story.append(badge_row("Managed Money", "BEARISH (IDX 20)", "Bearish",
                            "Broad NonComm", "NEUTRAL (IDX 57)", "Neutral", "DIVERGENT"))
    story.append(Spacer(1, 4))
    story.append(Paragraph("4-Week Positioning Trend — Managed Money (net contracts)", sty["TABHDR"]))
    story.append(trend_table("MM Net Long",
                              ["09/29","10/06","10/13","10/20","10/27"],
                              [124418, 114820, 111079, 108685, 107404],
                              [-6916, -9598, -3741, -2394, -1281]))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Positioning Breakdown", sty["LBL"]))
    gold_bullets = [
        "<b>Managed Money has cut its net long for nine straight weeks, but the selling is slowing.</b> Net long "
        "fell from 151,315 (08/25) to 107,404, and from 131,334 (09/22) over the five weeks shown. The weekly cuts shrank each week after the 10/06 peak: "
        "–9,598 → –3,741 → –2,394 → –1,281. The COT Index went 42 → 30 → 25 → 22 → 20, which is the low "
        "end of its 66-week range. Managed Money is still net long; it is just the least long it has been in "
        "that window.",
        "Source 2 (Tradingster Non-Commercial) disagrees. It is Neutral at IDX 57 (net long 186,451), so the "
        "alignment is <b>DIVERGENT</b>. Its drop picked up speed this week, though: –13,414 contracts, from "
        "IDX 61 to 57. Its net long has fallen every week shown (218,632 → 186,451; IDX 66 → 63 → 63 → 61 → 57).",
        "<b>Commercials (bullion banks) cut their net short sharply this week</b>, from –235,582 to –215,624 "
        "(+19,958), and their COT Index rose from 36 to 41. That is the hedger side buying back while "
        "speculators sell, but at 41 Commercials are mid-range, nowhere near the ≥80 extreme.",
        "Retail cut longs hard too (net +29,173 vs +35,717, IDX 70 → 54). That flipped Retail from Bullish "
        "to Neutral in one week and removed the stretched-crowd reading that had been building for a "
        "month.",
    ]
    for b in gold_bullets:
        story.append(Paragraph("●&nbsp;&nbsp;" + b, sty["BULLET"]))

    story.append(Paragraph("Read", sty["LBL"]))
    story.append(Paragraph(
        "Gold's positioning is moving in the direction a bullish setup needs, but it is far from complete. "
        "Managed Money has washed out to IDX 20, which is the hedge-fund half of a Bullish COT setup on its "
        "own. Commercials are covering and Retail is leaving. But the rule needs Commercials at the ≥80 "
        "extreme, and they are at 41, so the gap is wide. A 5-point move in a week is real, but at that pace "
        "the threshold is a long way off. Source 2 also has not reached an extreme (57), so the two "
        "speculator measures disagree on how washed-out funds really are. None of this supports a lean "
        "either way. A low Managed Money reading without Commercials at an extreme is not a buy signal, and "
        "nine weeks of liquidation is not a short signal under our rule.", sty["BODY"]))

    plan_tbl2 = Table([[Paragraph(
        "<b>TRADING PLAN — Mon/Tue</b><br/><br/>"
        "<b>Bias: No COT edge — trade v7 signals on their own at standard size.</b> No COT setup this week. "
        "Commercials are at IDX 41 and a Bullish setup needs 80 or higher. Managed Money (IDX 20) is already "
        "at the opposite extreme, so only the Commercial side is missing, but the gap is wide. Range Reversal "
        "is off on Gold by default, so take Box Break (0.5%) and Mid Continuation (0.25%) signals in either "
        "direction at normal size. Do not buy early because funds look washed out, and do not short because "
        "funds keep selling. Keep watching Commercial covering. If Commercials reach 80+ while Managed Money, "
        "Non-Commercial or Retail are at 20 or below, Gold longs get COT support.",
        sty["PLAN"])]], colWidths=[W])
    plan_tbl2.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), C_PLAN),
        ("BOX",(0,0),(-1,-1),0.6,colors.HexColor("#9fc3e8")),
        ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),
        ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),
    ]))
    story.append(Spacer(1, 4))
    story.append(plan_tbl2)
    story.append(Spacer(1, 10))
    story.append(HRFlowable(width=W, thickness=1, color=colors.HexColor("#aaaaaa")))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Sources: CFTC TFF Leveraged Funds (NQ) / Disaggregated Managed Money (Gold), Tradingster Legacy "
        "Non-Commercial + Commercial/Non-Reportable breakdown. COT Index = Larry Williams percentile (TFF/"
        "Disaggregated over the dashboard's ~70/66-week window; Tradingster over its full history). "
        "Bullish ≥60 | Bearish ≤40 | Neutral 40-60. COT setup rule: Commercial IDX ≥80 with hedge funds "
        "and/or Retail ≤20 = Bullish setup; Commercial ≤20 with hedge funds and/or Retail ≥80 = Bearish "
        "setup; anything else = no COT edge. No independent third-source check exists for NQ or Gold (no "
        "viable free data feed), so both instruments use two COT sources only.",
        sty["META"]))

    doc.build(story)
    return out

if __name__ == "__main__":
    out = build()
    print(f"Done. PDF saved to:\n  {out}")
