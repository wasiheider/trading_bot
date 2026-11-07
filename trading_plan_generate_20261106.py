#!/usr/bin/env python3
"""
Trading_Plan_2026-11-09_2026-11-10_NQ_Gold_Silver_BTC_GBPUSD.pdf generator.
Same visual style as trading_plan_generate_20261030.py, extended from NQ/Gold to
Silver, BTC and GBPUSD (user request 2026-11-06). Data pulled 2026-11-06
after cot_fetch.py refreshed the dashboard (report week ending 2026-11-03):
COT indexes, cohort nets and the cot_rules.py setup flag from the live /cot
endpoint; 5-week Leveraged Funds / Managed Money nets from
COT_Breakdown_2026-11-03.pdf. Prices: OANDA practice daily candles;
derived levels cached in data/trading_plan_20261106_prices.json (gitignored).
Only GBPUSD has a COT setup this week (Bullish, Commercials 80 vs
Non-Commercial 19). NQ Commercials 22 (rule needs <=20); BTC's 10/27 Bearish
setup ended (Commercials 12 -> 26).
"""
import os, json
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

def trend_table5(title, dates, vals, prev=None):
    """5-week net series; first delta vs the week before the window when known."""
    deltas = [(vals[0] - prev) if prev is not None else None] + [vals[i] - vals[i-1] for i in range(1, len(vals))]
    hdr = [ph("")] + [ph(d) for d in dates]
    row1 = [ps_(title)] + [pd_(f"{v:,}") for v in vals]
    row2 = [ps_("Weekly Δ")] + [pd_("–" if d is None else f"{d:+,}") for d in deltas]
    cw = [42*mm] + [27.6*mm]*5
    ts = list(BASE_TS)
    ts.append(("BACKGROUND",(5,1),(5,2), colors.HexColor("#dbeeff")))
    ts.append(("FONTNAME",(5,1),(5,2),"Helvetica-Bold"))
    t = Table([hdr, row1, row2], colWidths=cw)
    t.setStyle(TableStyle(ts))
    return t

def cohort_table(rows):
    """rows: (cohort, net_prev, net_now, idx_prev, idx_now)"""
    hdr = [ph("Cohort"), ph("Net 10/27"), ph("Net 11/03"), ph("Change"), ph("IDX 10/27"), ph("IDX 11/03")]
    body = [[ps_(c), pd_(f"{a:,}"), pd_(f"{b:,}"), pd_(f"{b-a:+,}"), pd_(i0), pd_(i1)] for c, a, b, i0, i1 in rows]
    t = Table([hdr] + body, colWidths=[44*mm, 27*mm, 27*mm, 25*mm, 28*mm, 29*mm])
    t.setStyle(TableStyle(BASE_TS))
    return t

def price_table(p, fmt):
    hdr = [ph("Fri close"), ph("Week H / L"), ph("Prior wk H / L"), ph("20d / 50d MA"), ph("ATR(14)"), ph("vs 300d high")]
    f = lambda v: fmt.format(v)
    row = [pd_(f(p["close"])), pd_(f"{f(p['wk_hi'])} / {f(p['wk_lo'])}"), pd_(f"{f(p['pwk_hi'])} / {f(p['pwk_lo'])}"),
           pd_(f"{f(p['ma20'])} / {f(p['ma50'])}"), pd_(f(p["atr"])), pd_(f"{p['off_hi']:+.1f}%")]
    t = Table([hdr, row], colWidths=[24*mm, 36*mm, 36*mm, 36*mm, 22*mm, 26*mm])
    t.setStyle(TableStyle(BASE_TS))
    return t

def plan_box(text, W, bias):
    t = Table([[Paragraph(text, make_styles()["PLAN"])]], colWidths=[W])
    t.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), C_BULL if bias == "Bullish" else C_PLAN),
        ("BOX",(0,0),(-1,-1),0.6,colors.HexColor("#9fc3e8")),
        ("TOPPADDING",(0,0),(-1,-1),8),("BOTTOMPADDING",(0,0),(-1,-1),8),
        ("LEFTPADDING",(0,0),(-1,-1),8),("RIGHTPADDING",(0,0),(-1,-1),8),
    ]))
    return t

STATS = {}
_stats = os.path.join(OUTPUT_DIR, "data", "trading_plan_20261106_prices.json")
if os.path.exists(_stats):
    STATS = json.load(open(_stats))

DATES5 = ["10/06","10/13","10/20","10/27","11/03"]

SECTIONS = [
  dict(sym="NQ", fmt="{:,.0f}", bias="None",
    badge=("Lev Funds","BULLISH (IDX 93)","Bullish","Broad NonComm","BULLISH (IDX 71)","Bullish","CONFIRMED"),
    trend=("Lev Fund Net", [-19212,-11593,950,2301,10073], -24723),
    cohorts=[("Commercials", -62092, -63270, 22, 22), ("Leveraged Funds", 2301, 10073, 86, 93),
             ("Non-Commercial (S2)", 48839, 42873, "–", 71), ("Asset Managers", 65381, 73137, 36, 54),
             ("Retail", 13253, 20397, 75, 88)],
    bullets=[
      "<b>Price broke hard after the COT snapshot.</b> NQ fell 7.0% on Thursday (28,538 to 26,531), then bounced 1.8% "
      "Friday to close at 27,012. The week ended down 4.9%, with a Friday low of 26,273, the lowest in 60 days. NQ "
      "is now 14% below its early-October high (31,401) and below its 20-day (29,104), 50-day (29,661) and 200-day (28,077) "
      "averages. Daily ATR is about 800 points.",
      "<b>The COT data is from Tuesday Nov 3, before Thursday's drop.</b> As of Tuesday, Leveraged Funds had bought "
      "for six straight weeks: from –24,723 net short on 09/29 to +10,073 net long (+7,772 this week). Their COT Index "
      "rose 86 to 93. Source 2 (Non-Commercial) is Bullish at IDX 71, so the alignment is <b>CONFIRMED</b>, though its "
      "net long slipped from 48,839 to 42,873. Asset Managers added back 7,756 contracts (IDX 36 to 54).",
      "Commercials are net short –63,270 (–62,092 last week). Their COT Index is <b>22</b> on this week's 70-week "
      "window, unchanged from last week on the same window (last week's report, on the older window, showed 28). "
      "The rule needs 20 or lower.",
      "<b>Retail jumped in.</b> Net long rose from +13,253 to +20,397 and the Retail index went 75 to 88. Retail "
      "and hedge funds were both near their long extremes going into Thursday's selloff.",
    ],
    read="NQ is the closest it has been to a Bearish COT setup, and it still does not qualify. The speculator side is "
      "fully stretched (hedge funds 93, Retail 88). The Commercial side is at 22, two points short of the 20 the rule "
      "needs. Two cautions. First, this snapshot predates Thursday's 7% drop, and a move that size usually forces "
      "speculators to cut longs, so next week's numbers may look different. Second, a bearish setup that only "
      "completes after a 14% drop from the high would arrive late in the move. Under the rule, a stretched crowd "
      "is not a lean by itself.",
    plan="<b>TRADING PLAN — Mon/Tue</b><br/><br/><b>Bias: No COT edge — trade v7 signals on their own at standard "
      "size.</b> No COT setup: Commercials at IDX 22, the rule needs 20 or lower. Take Range Reversal and Box Break "
      "signals in either direction at the normal 0.5% risk, and Mid Continuation at 0.25%. Do not press shorts "
      "because the crowd is long, and do not buy the dip because it fell hard. Volatility is high (ATR ~800), so stops "
      "are wider and position size is smaller at the same risk. Next Friday's release (data as of Nov 10) will show how "
      "speculators reacted to Thursday; if Commercials are at 20 or below with hedge funds or Retail still at 80+, NQ "
      "shorts get COT support."),
  dict(sym="GOLD", fmt="{:,.1f}", bias="None",
    badge=("Managed Money","BEARISH (IDX 16)","Bearish","Broad NonComm","NEUTRAL (IDX 56)","Neutral","DIVERGENT"),
    trend=("MM Net Long", [114820,111079,108685,107404,104297], 124418),
    cohorts=[("Commercials", -215624, -204902, 41, 44), ("Managed Money", 107404, 104297, 20, 16),
             ("Non-Commercial (S2)", 186451, 179548, 57, 56), ("Retail", 29173, 25354, 54, 45)],
    bullets=[
      "<b>Price bounced 5% this week.</b> Gold closed at 4,071 after the Oct 30 low of 3,870, the lowest in 60 days. "
      "This week's range was 3,891 to 4,147. Gold sits right on its 20-day average (4,067), still below the 50-day "
      "(4,198) and 200-day (4,462), and 27% below the late-January high of 5,602.",
      "<b>Managed Money cut longs for a tenth straight week</b>, from 107,404 to 104,297 (–3,107). The COT Index went "
      "20 to 16, the least long in the 66-week window. Source 2 (Non-Commercial) is Neutral at IDX 56 (net long "
      "186,451 to 179,548), so the alignment is <b>DIVERGENT</b>.",
      "Commercials covered for a second week: net short –215,624 to –204,902 (+10,722), IDX 41 to 44. Still mid-range.",
      "Retail sold too: net long +29,173 to +25,354, IDX 54 to 45.",
    ],
    read="Same picture as last week, a little further along. Hedge funds are washed out (MM 16), and Commercials are "
      "buying back shorts, but Commercials at 44 are nowhere near the 80 the rule needs. The price bounce came with "
      "speculators still selling, which fits short covering by the hedgers rather than new buying by funds. "
      "No lean either way.",
    plan="<b>TRADING PLAN — Mon/Tue</b><br/><br/><b>Bias: No COT edge — trade v7 signals on their own at standard "
      "size.</b> No COT setup: Commercials at IDX 44, a Bullish setup needs 80 or higher. Range Reversal stays off on "
      "Gold, so take Box Break (0.5%) and Mid Continuation (0.25%) signals in either direction. Do not buy early "
      "because funds look washed out. Gold longs get COT support only if Commercials reach 80+ while Managed Money, "
      "Non-Commercial or Retail are at 20 or below."),
  dict(sym="SILVER", fmt="{:,.2f}", bias="None",
    badge=("Managed Money","BEARISH (IDX 0)","Bearish","Broad NonComm","BEARISH (IDX 37)","Bearish","CONFIRMED"),
    trend=("MM Net Long", [7608,5735,3746,2902,2665], None),
    cohorts=[("Commercials", -35687, -34756, 62, 62), ("Managed Money", 2902, 2665, 1, 0),
             ("Non-Commercial (S2)", 19703, 21251, "–", 37), ("Retail", 15984, 13505, 48, 37)],
    bullets=[
      "<b>Price bounced 5.7% this week</b> to 51.94, after a Nov 3 low of 48.62 (the lowest in 60 days). Range: 48.62 "
      "to 53.48. Silver is still below its 20-day (54.56), 50-day (60.02) and 200-day (68.89) averages and 57% below "
      "the late-January high of 121.67. Daily ATR is about 2.16 (4% of price).",
      "<b>Managed Money is all but flat.</b> Net long fell from 7,608 (10/06) to 2,665, and the COT Index is <b>0</b>, "
      "the bottom of its 66-week range. Source 2 (Non-Commercial) is Bearish at IDX 37, though it added 1,548 "
      "contracts this week. Both sources Bearish, so the alignment is <b>CONFIRMED</b>.",
      "Commercials barely moved: net short –35,687 to –34,756, IDX 62 both weeks.",
      "Retail cut longs: +15,984 to +13,505, IDX 48 to 37.",
    ],
    read="The hedge-fund half of a Bullish COT setup is fully in place: Managed Money has no long left to sell "
      "(IDX 0). The missing half is Commercials, at 62 against the 80 the rule needs. That gap is smaller than "
      "Gold's (44), so Silver is the metal to watch for a setup, but it is not one today. A washed-out crowd alone "
      "is not a buy signal.",
    plan="<b>TRADING PLAN — Mon/Tue</b><br/><br/><b>Bias: No COT edge — trade v7 signals on their own at standard "
      "size.</b> No COT setup: Commercials at IDX 62, a Bullish setup needs 80 or higher. Range Reversal stays off on "
      "Silver, so take Box Break (0.5%) and Mid Continuation (0.25%) in either direction. Silver longs get COT support "
      "when Commercials reach 80+ with Managed Money still at 20 or below."),
  dict(sym="BTC", fmt="{:,.0f}", bias="None",
    badge=("Lev Funds","BULLISH (IDX 100)","Bullish","Broad NonComm","BULLISH (IDX 80)","Bullish","CONFIRMED"),
    trend=("Lev Fund Net", [-6852,-5844,-4942,-3979,-2591], None),
    cohorts=[("Commercials", -2880, -2123, 12, 26), ("Leveraged Funds", -3979, -2591, 91, 100),
             ("Non-Commercial (S2)", 2720, 2230, "–", 80), ("Asset Managers", 2754, 2403, 11, 6),
             ("Retail", 160, -107, 19, 13)],
    bullets=[
      "<b>Price failed at 81k.</b> BTC rallied to 80,189 on Wednesday, then fell 5.5% Thursday to 75,805 and closed the "
      "week at 75,814 (–1.3%). Range: 74,772 to 81,115. It is below its 20-day (80,343) and 50-day (80,906) averages, "
      "above the 200-day (72,339), and 40% below the October 2025 high of 126,307.",
      "Leveraged Funds kept covering: net short –3,979 to –2,591, the fifth straight smaller short. Their COT Index "
      "hit <b>100</b>, the top of the range (they are least short). Source 2 (Non-Commercial) is Bullish at IDX 80. "
      "Alignment <b>CONFIRMED</b>. Asset Managers trimmed again (IDX 11 to 6).",
      "<b>Commercials bought, and that ended last week's setup.</b> Their net short shrank from –2,880 to –2,123 and "
      "the index rose from 12 to <b>26</b>. Last week, Commercials at 12 against hedge funds at 91+ was a Bearish COT "
      "setup. At 26 it no longer qualifies.",
      "Retail flipped to a small net short (+160 to –107, IDX 19 to 13). The Crypto Fear &amp; Greed Index is 25 "
      "(Extreme Fear).",
    ],
    read="Last week's Bearish setup is gone, so do not carry a short lean into Monday. Commercials moved off their "
      "extreme while hedge funds kept covering. Retail is now at its short extreme (13), but a Bullish setup would "
      "need Commercials at 80+, and they are at 26. Nothing in the rule supports a lean either way.",
    plan="<b>TRADING PLAN — Mon/Tue</b><br/><br/><b>Bias: No COT edge — trade v7 signals on their own at standard "
      "size.</b> No COT setup: Commercials at IDX 26 (last week's Bearish setup at 12 has dissolved). Take signals in "
      "either direction at normal risk. Daily ATR is about 2,700 (3.6% of price), so stops are wide. BTC shorts would "
      "get COT support again only if Commercials drop back to 20 or below with hedge funds or Retail at 80+."),
  dict(sym="GBPUSD", fmt="{:,.4f}", bias="Bullish",
    badge=("Lev Funds","NEUTRAL (IDX 53)","Neutral","Broad NonComm","BEARISH (IDX 19)","Bearish","DIVERGENT"),
    trend=("Lev Fund Net", [12432,10313,24104,17195,26663], None),
    cohorts=[("Commercials", 89898, 68999, 88, 80), ("Leveraged Funds", 17195, 26663, 35, 53),
             ("Non-Commercial (S2)", -79414, -59821, "–", 19), ("Asset Managers", -128893, -121163, 19, 25),
             ("Retail", -10484, -9178, 37, 40)],
    bullets=[
      "<b>Price gave back the week's gain on Friday.</b> GBPUSD fell 0.7% Friday to 1.3353, near the week's low "
      "(range 1.3342 to 1.3503), and finished the week down 0.3%. It sits on its 20-day (1.3348) and 50-day (1.3362) "
      "averages and below the 200-day (1.3421). The 60-day low is 1.3137 (mid-October). Daily ATR is about 68 pips.",
      "<b>Commercials are net long and at the threshold.</b> Net long fell from 89,898 to 68,999 (–20,899) and the "
      "COT Index dropped from 88 to <b>80</b>, exactly the rule's minimum.",
      "Non-Commercial (Source 2) is net short –59,821 (from –79,414), IDX <b>19</b>, one point inside the 20 "
      "extreme. Leveraged Funds (Source 1) went the other way: net long 17,195 to 26,663, IDX 35 to 53. The two "
      "speculator sources disagree, so the alignment is <b>DIVERGENT</b>. Asset Managers covered some shorts (IDX 19 "
      "to 25).",
      "Retail is net short –9,178 (IDX 40). OANDA clients are 59% long, a neutral reading.",
    ],
    read="By the rule this is a <b>Bullish COT setup</b>: Commercials at an extreme long (IDX 80) against Non-Commercial "
      "at the opposite extreme (IDX 19). It is the thinnest possible version, though. Commercials cut 21k contracts "
      "and sit exactly on 80, and Non-Commercial is one point inside 20 and covering. Both sides moved toward the "
      "middle this week, so the setup could disappear next Friday. Price closed Friday near the week's low, so the lean "
      "has no price confirmation yet.",
    plan="<b>TRADING PLAN — Mon/Tue</b><br/><br/><b>Bias: Bullish COT setup — lean with Commercials.</b> Commercials "
      "at IDX 80 vs. Non-Commercial at IDX 19. Favor long v7 signals; short signals get no COT support. Keep standard "
      "size (0.5% Range Reversal/Box Break, 0.25% Mid Continuation) because the reading is right at the threshold. "
      "Do not anticipate a long before v7 signals one. Recheck Friday Nov 13: if Commercials drop below 80 or "
      "Non-Commercial rises above 20, the setup ends."),
]

def build():
    W = A4[0] - 30*mm
    fname = "Trading_Plan_2026-11-09_2026-11-10_NQ_Gold_Silver_BTC_GBPUSD.pdf"
    out = os.path.join(OUTPUT_DIR, fname)
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=15*mm, rightMargin=15*mm, topMargin=15*mm, bottomMargin=15*mm)
    sty = make_styles()
    sty["H1"].fontSize = 15
    sty["H1"].leading = 19
    story = []
    story.append(Paragraph("NQ, GOLD, SILVER, BTC &amp; GBPUSD — COT-BASED TRADING PLAN", sty["H1"]))
    story.append(Paragraph(
        "Trading Window: Monday Nov 9 – Tuesday Nov 10, 2026  |  COT Data: CFTC week ending Tue Nov 3, 2026 (released "
        "Fri Nov 6)  |  Prices: OANDA daily closes through Fri Nov 6  |  Prepared Nov 6, 2026",
        sty["META"]))
    story.append(HRFlowable(width=W, thickness=1.5, color=colors.HexColor("#1a1a2e")))
    story.append(Spacer(1, 6))

    story.append(Paragraph("Summary", sty["LBL"]))
    hdr = [ph("Instrument"), ph("Fri close"), ph("Week"), ph("Comm IDX"), ph("Hedge funds IDX"), ph("Retail IDX"), ph("COT verdict")]
    rows = [
      ["NQ", "27,012", "–4.9%", "22", "93", "88", "No COT edge (2 pts from Bearish setup)"],
      ["Gold", "4,070.6", "+5.0%", "44", "16", "45", "No COT edge"],
      ["Silver", "51.94", "+5.7%", "62", "0", "37", "No COT edge"],
      ["BTC", "75,814", "–1.3%", "26", "100", "13", "No COT edge (last week's Bearish setup ended)"],
      ["GBPUSD", "1.3353", "–0.3%", "80", "19 (NonComm)", "40", "Bullish COT setup (at threshold)"],
    ]
    body = [[ps_(r[0])] + [pd_(x) for x in r[1:6]] + [Paragraph(r[6], ParagraphStyle("v", fontName="Helvetica", fontSize=8.5, leading=10))] for r in rows]
    t = Table([hdr] + body, colWidths=[20*mm, 20*mm, 15*mm, 17*mm, 25*mm, 17*mm, 66*mm])
    ts = list(BASE_TS)
    ts.append(("BACKGROUND",(6,5),(6,5), C_BULL))
    t.setStyle(TableStyle(ts))
    story.append(t)
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "<b>Timing caveat:</b> COT positions are as of Tuesday Nov 3. NQ fell 7% on Thursday Nov 5, after "
        "the snapshot, so this week's numbers do not show how traders reacted to that drop. Hedge funds = Leveraged "
        "Funds (TFF) for NQ/BTC/GBP, Managed Money (Disaggregated) for Gold/Silver; GBPUSD's setup uses the "
        "Non-Commercial reading per the shared rule.", sty["META"]))

    for s in SECTIONS:
        story.append(HRFlowable(width=W, thickness=1, color=colors.HexColor("#aaaaaa")))
        story.append(Paragraph(s["sym"], sty["H2sym"]))
        story.append(badge_row(*s["badge"]))
        story.append(Spacer(1, 4))
        if s["sym"] in STATS:
            story.append(Paragraph("Price Context (daily, through Fri Nov 6)", sty["TABHDR"]))
            story.append(price_table(STATS[s["sym"]], s["fmt"]))
        story.append(Paragraph(f"5-Week Positioning Trend — {s['trend'][0].replace(' Net','').replace(' Long','')} (net contracts)", sty["TABHDR"]))
        story.append(trend_table5(s["trend"][0], DATES5, s["trend"][1], s["trend"][2]))
        story.append(Paragraph("Commercial / Speculator / Retail This Week", sty["TABHDR"]))
        story.append(cohort_table(s["cohorts"]))
        story.append(Paragraph("Positioning Breakdown", sty["LBL"]))
        for b in s["bullets"]:
            story.append(Paragraph("●&nbsp;&nbsp;" + b, sty["BULLET"]))
        story.append(Paragraph("Read", sty["LBL"]))
        story.append(Paragraph(s["read"], sty["BODY"]))
        story.append(Spacer(1, 4))
        story.append(plan_box(s["plan"], W, s["bias"]))
        story.append(Spacer(1, 8))

    story.append(Spacer(1, 10))
    story.append(HRFlowable(width=W, thickness=1, color=colors.HexColor("#aaaaaa")))
    story.append(Spacer(1, 4))
    story.append(Paragraph(
        "Sources: live /cot dashboard (report week ending 2026-11-03, refreshed by cot_fetch.py on Nov 6): COT indexes, "
        "Commercial/Retail/Non-Commercial/Asset Manager nets and the setup flag from cot_rules.py. 5-week net series "
        "from COT_Breakdown_2026-11-03.pdf (CFTC TFF Leveraged Funds for NQ/BTC/GBP, Disaggregated Managed Money for "
        "Gold/Silver; 09/29 values carried from last week's plan for NQ and Gold). Prices: OANDA practice daily mid "
        "candles (NAS100_USD, XAU_USD, XAG_USD, BTC_USD, GBP_USD). COT Index = "
        "Larry Williams percentile over the dashboard's ~70/66-week window. COT setup rule: Commercial IDX ≥80 with "
        "hedge funds and/or Retail ≤20 = Bullish setup; Commercial ≤20 with hedge funds and/or Retail ≥80 = Bearish "
        "setup; anything else = no COT edge. Risk sizes are the v7 defaults (Range Reversal/Box Break 0.5%, Mid "
        "Continuation 0.25%); Range Reversal is off on Gold and Silver.",
        sty["META"]))
    doc.build(story)
    return out

if __name__ == "__main__":
    out = build()
    print(f"Done. PDF saved to:\n  {out}")
