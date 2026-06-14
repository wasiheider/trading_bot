"""Generate COT Breakdown Summary PDF — CFTC Jun 9 2026 data (Mgd Money update after source switch)."""

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.enums import TA_CENTER
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "COT_Breakdown_Jun09_2026.pdf")

# ── Colours ───────────────────────────────────────────────────────────────────
BG    = colors.HexColor("#060810")
BG1   = colors.HexColor("#0b0f1a")
BG2   = colors.HexColor("#101520")
BG3   = colors.HexColor("#161d2e")
GREEN = colors.HexColor("#00e676")
RED   = colors.HexColor("#ff4444")
AMBER = colors.HexColor("#f5a623")
BLUE  = colors.HexColor("#4a9eff")
MUTED = colors.HexColor("#5d7490")
DIM   = colors.HexColor("#2e3d52")
WHITE = colors.HexColor("#e2eaf6")
PURP  = colors.HexColor("#a78bfa")

W, H = A4

doc = SimpleDocTemplate(
    OUT, pagesize=A4,
    leftMargin=14*mm, rightMargin=14*mm,
    topMargin=14*mm, bottomMargin=14*mm,
    title="COT Breakdown — Jun 9 2026",
)

# ── Styles ────────────────────────────────────────────────────────────────────
def S(name, **kw):
    base = dict(fontName="Helvetica", fontSize=9, textColor=WHITE, leading=13)
    base.update(kw)
    return ParagraphStyle(name, **base)

sTitle  = S("title",  fontName="Helvetica-Bold", fontSize=17, textColor=GREEN,  leading=21)
sSubtit = S("sub",    fontSize=8, textColor=MUTED, leading=12)
sSect   = S("sect",   fontName="Helvetica-Bold", fontSize=10, textColor=AMBER,  leading=14, spaceBefore=10, spaceAfter=4)
sInstr  = S("instr",  fontName="Helvetica-Bold", fontSize=9,  textColor=WHITE,  leading=13)
sBullet = S("bul",    fontSize=8, textColor=MUTED, leading=12, leftIndent=8)
sWhy    = S("why",    fontName="Helvetica-Oblique", fontSize=8, textColor=MUTED, leading=12, leftIndent=8)
sCap    = S("cap",    fontSize=7, textColor=DIM,   leading=10)
sFooter = S("foot",   fontSize=7, textColor=DIM,   leading=10, alignment=TA_CENTER)

def P(text, style=None):  return Paragraph(text, style or S("body"))
def HR():                 return HRFlowable(width="100%", thickness=0.5, color=DIM, spaceAfter=4, spaceBefore=4)
def SP(h=4):              return Spacer(1, h)

def bias_col(bias):
    b = bias.lower()
    if "bull" in b: return GREEN
    if "bear" in b: return RED
    if "neut" in b: return AMBER
    return MUTED

def delta_col(s):
    s = s.replace(",","").replace("k","000").replace("+","").replace("−","-").replace("▲","").replace("▼","").strip()
    try:
        return GREEN if float(s) > 0 else RED if float(s) < 0 else MUTED
    except:
        return MUTED

def tbl_base():
    return TableStyle([
        ("BACKGROUND",    (0,0),(-1,0),  BG2),
        ("TEXTCOLOR",     (0,0),(-1,0),  MUTED),
        ("FONTNAME",      (0,0),(-1,0),  "Helvetica-Bold"),
        ("FONTSIZE",      (0,0),(-1,-1), 7),
        ("LEADING",       (0,0),(-1,-1), 10),
        ("ROWBACKGROUND", (0,1),(-1,-1), [BG1, BG3]),
        ("TEXTCOLOR",     (0,1),(-1,-1), WHITE),
        ("GRID",          (0,0),(-1,-1), 0.3, DIM),
        ("LEFTPADDING",   (0,0),(-1,-1), 5),
        ("RIGHTPADDING",  (0,0),(-1,-1), 5),
        ("TOPPADDING",    (0,0),(-1,-1), 3),
        ("BOTTOMPADDING", (0,0),(-1,-1), 3),
        ("VALIGN",        (0,0),(-1,-1), "MIDDLE"),
    ])

story = []

# ── Header ────────────────────────────────────────────────────────────────────
story += [
    P("COT Bias Breakdown", sTitle),
    P("CFTC Commitments of Traders  ·  Report Date: <b>Jun 9, 2026</b>  ·  Generated: Jun 14, 2026", sSubtit),
    P("Strategy: Trading Bot V4  ·  Source: TFF dataset gpe5-46if (Lev Funds) + Disaggregated dataset kh3c-gbw2 (Mgd Money)", sSubtit),
    P("Bias verified against live dashboard — Larry Williams COT Index method (relative to ~54-week history)", sSubtit),
    P("<b>Data source update:</b>  GOLD / SILVER / CRUDE switched from Legacy NonComm (6dca-aqww) to "
      "Disaggregated Managed Money (kh3c-gbw2) — aligns with Tradingster and professional COT methodology. "
      "CRUDE bias changed: Neutral 46/100 → <b>Bullish 73/100</b>.", sCap),
    SP(6), HR(),
    P("<b>How to read:</b>  Bias = COT Index percentile vs history (≥60=Bullish, ≤40=Bearish, 40-60=Neutral). "
      "Net = Lev Fund long−short (TFF) / Mgd Money long−short (Disaggregated). "
      "Weekly Δ = net contracts added/removed this week. "
      "LEV FLIPPED = direction of lev fund delta reversed from last week. "
      "VIX inverted throughout (lev fund short VIX = low fear = market bullish).", sCap),
    SP(8),
]

# ── Summary table ─────────────────────────────────────────────────────────────
story.append(P("SUMMARY — ALL 14 INSTRUMENTS  (CFTC Jun 9 · Verified vs Live Dashboard)", sSect))

# Corrected from live dashboard verification
rows = [
    # sym, bias, index, net_jun9, net_jun2, weekly_delta, lev_signal, mm_signal, key_driver
    ("ES",     "Bearish",  "16/100",  "-459,690", "-503,509", "+43,819",  "LEV FLIPPED",          "MM Adding",   "Covered 35.6k shorts + added longs; delta flipped positive"),
    ("NQ",     "Bearish",  "27/100",  "-34,306",  "-53,650",  "+19,344",  "LEV FLIPPED",          "",            "Longs +11.1k, shorts covered 8.2k; strongest % improvement"),
    ("RTY",    "Bearish",  "40/100",  "-74,454",  "-73,340",  "-1,114",   "Lev Covering avg",     "MM Closing",  "No follow-through; MM cutting longs (-4.5k)"),
    ("US30",   "Bullish",  "61/100",  "-9,172",   "-13,898",  "+4,726",   "Lev Covering avg",     "MM Closing",  "Net short -9k is LOW vs history = Bullish COT signal"),
    ("BTC",    "Bullish",  "100/100", "-5,995",   "-6,558",   "+563",     "Lev Pressing avg",     "MM Adding",   "At top of historical range; rate of improvement slowing"),
    ("GOLD",   "Bearish",  "11/100",  "+105,863", "+113,563", "-7,700",   "Lev Pressing avg",     "",            "Mgd Money +106k is bottom 11% of history — funds were 200k+ at peaks"),
    ("SILVER", "Bearish",  "12/100",  "+10,403",  "+11,003",  "-600",     "",                     "",            "Mgd Money +10k near historical min; trimming lightly this week"),
    ("CRUDE",  "Bullish",  "73/100",  "+94,725",  "+95,825",  "-1,100",   "",                     "",            "Mgd Money at 73/100 = top third of history; mild trim but structurally bullish"),
    ("USD",    "Bearish",  "29/100",  "-13,656",  "-11,112",  "-2,544",   "",                     "",            "More shorts added; asset mgrs adding longs (divergence)"),
    ("EUR",    "Bearish",  "0/100",   "-17,388",  "+12,027",  "-29,415",  "Lev Pressing avg",     "",            "FLIPPED from net long to net short in one week; extreme 0/100"),
    ("GBP",    "Neutral",  "45/100",  "+22,312",  "+27,353",  "-5,041",   "",                     "",            "Dropped to neutral; both lev funds and MM cutting longs"),
    ("JPY",    "Bearish",  "0/100",   "-99,844",  "-88,063",  "-11,781",  "",                     "MM Adding",   "Crowded short at -100k; MM diverging (adding JPY longs)"),
    ("NZD",    "Bearish",  "16/100",  "-22,254",  "-27,725",  "+5,471",   "LEV FLIPPED",          "MM Adding",   "Covering NZD shorts; risk-on move mirrors NQ/ES"),
    ("VIX",    "Bearish",  "23/100",  "-35,290",  "-33,033",  "+2,257i",  "LEV FLIPPED",          "",            "Index 23/100 = historically fearful; delta improved but still bearish"),
]

hdr = ["Sym","Bias","Index","Net Jun 9","Net Jun 2","Wkly Δ","Lev Signal","MM Signal","Key Driver"]
cw  = [14,22,16,24,24,19,27,18,0]
cw[-1] = W - 28*mm - sum(x*mm for x in cw[:-1])
tbl_data = [hdr] + [[r[0],r[1],r[2],r[3],r[4],r[5],r[6],r[7],r[8]] for r in rows]

ts = tbl_base()
for i, r in enumerate(rows, 1):
    bc = bias_col(r[1])
    ts.add("TEXTCOLOR", (1,i),(1,i), bc)
    ts.add("FONTNAME",  (1,i),(1,i), "Helvetica-Bold")
    dc = delta_col(r[5])
    ts.add("TEXTCOLOR", (5,i),(5,i), dc)
    ts.add("FONTNAME",  (5,i),(5,i), "Helvetica-Bold")
    # Highlight LEV FLIPPED
    if "FLIPPED" in r[6]:
        ts.add("TEXTCOLOR", (6,i),(6,i), GREEN)
        ts.add("FONTNAME",  (6,i),(6,i), "Helvetica-Bold")
    elif "Pressing" in r[6]:
        ts.add("TEXTCOLOR", (6,i),(6,i), RED)
    elif "Covering" in r[6]:
        ts.add("TEXTCOLOR", (6,i),(6,i), GREEN)
    # MM signal colour
    if "Adding" in r[7]:   ts.add("TEXTCOLOR", (7,i),(7,i), GREEN)
    if "Closing" in r[7]:  ts.add("TEXTCOLOR", (7,i),(7,i), RED)
    # Warn rows
    if r[0] in ("EUR","JPY"):
        ts.add("BACKGROUND", (0,i),(-1,i), colors.HexColor("#1a0c00"))

t = Table(tbl_data, colWidths=cw, repeatRows=1)
t.setStyle(ts)
story += [t,
    P("i  VIX weekly delta inverted for market-direction reading. "
      "GOLD/SILVER show Bearish index despite positive net — index measures relative to 54-wk Mgd Money history "
      "(gold Mgd Money was 200k+ at prior peaks; current +106k is bottom 11%). "
      "CRUDE Bullish 73/100 using Mgd Money vs Neutral 46/100 under old NonComm method — source matters.", sCap),
    SP(10)]

# ── Helper: instrument block ──────────────────────────────────────────────────
def iblock(sym, bias, index, nc, np, wd, br, why, warn=False):
    bc_hex = {GREEN: "00e676", RED: "ff4444", AMBER: "f5a623", MUTED: "5d7490"}[bias_col(bias)]
    dc = "ff4444" if wd.startswith("-") or wd.startswith("▼") else "00e676"
    return [
        P(f"<b>{sym}</b>  —  <font color='#{bc_hex}'>{bias}</font>  "
          f"<font color='#5d7490'>Index {index}</font>  "
          f"<font color='#{dc}'>Δ {wd}</font>", sInstr),
        P(br, sBullet),
        P(f"<i>Why: {why}</i>", sWhy),
        SP(5),
    ]

# ── EQUITIES ──────────────────────────────────────────────────────────────────
story += [HR(), P("EQUITIES  (TFF — Lev Fund Net)", sSect)]

story += iblock("ES", "Bearish", "16/100", "-459,690", "-503,509", "+43,819",
    "LEV FLIPPED. Added 8,179 longs; covered 35,640 shorts = +43.8k net. MM adding longs.",
    "Lev funds staged the largest single-week covering in the dataset — ~9% reduction in net short. "
    "Both lev funds and asset mgrs leaning less bearish. Still structurally very short (index 16/100). "
    "LEV FLIPPED fires because prev week delta was negative and this week turned positive.")

story += iblock("NQ", "Bearish", "27/100", "-34,306", "-53,650", "+19,344",
    "LEV FLIPPED. Added 11,131 longs AND covered 8,213 shorts. Strongest % improvement of any index.",
    "NQ saw the most aggressive repositioning of any equity. Delta flipped from -1.9k to +19.3k. "
    "Index at 27/100 — still well below the 60 Bullish threshold. MM trimmed longs marginally.")

story += iblock("RTY", "Bearish", "40/100", "-74,454", "-73,340", "-1,114",
    "Lev Covering above avg. Delta -1.1k (tiny worsening) but mild vs recent pace. MM Closing longs (-4.5k).",
    "RTY lagged while ES/NQ covered. The LEV COVERING signal means this week's -1.1k is BETTER "
    "than the recent average rate of short-building — not that they actually covered. "
    "Asset mgr longs being cut is the most negative signal here. Index at 40 is exactly on the Neutral border.")

story += iblock("US30", "Bullish", "61/100", "-9,172", "-13,898", "+4,726",
    "Lev Covering above avg. Covered 2,922 shorts + added 1,804 longs. MM Closing longs (minor).",
    "US30 net short of -9k is historically SMALL — meaning lev funds are far less short than their norm. "
    "This puts the index at 61/100, just above the Bullish threshold. "
    "Follows ES covering theme but is supported by the relative bullish signal from the index. "
    "MM closing longs is a mild counter-signal.")

story += iblock("BTC", "Bullish", "100/100", "-5,995", "-6,558", "+563",
    "Lev Pressing below avg. Delta +563 (tiny). MM Adding longs.",
    "Index 100/100 = lev funds are at the LEAST SHORT position in BTC's tracked history. "
    "Bullish by COT index, even though net is still -6k. The LEV PRESSING signal means "
    "the rate of improvement this week (+563) is slower than recent weeks' pace — decelerating. "
    "MM adding is a confirmatory bullish signal. No conviction move but best relative position ever.")

# ── CURRENCIES ────────────────────────────────────────────────────────────────
story += [HR(), P("CURRENCIES  (TFF — Lev Fund Net)", sSect)]

story += iblock("EUR", "Bearish", "0/100", "-17,388", "+12,027", "-29,415",
    "LEV Pressing. Sold 17,727 longs AND added 11,688 shorts. Flipped from net long +12k to net short -17k.",
    "The single largest positioning swing of the week. Lev funds reversed a long EUR position "
    "to net short in one week. Index at 0/100 = most extreme bearish EUR reading in history. "
    "Asset mgrs went the opposite direction (+8.2k longs) — a major divergence. "
    "Fast money (lev funds) is aggressively short; slow money (asset mgrs) is buying.",
    warn=True)

story += iblock("GBP", "Neutral", "45/100", "+22,312", "+27,353", "-5,041",
    "No lev/MM signal. Added 244 longs but also added 5,285 new shorts. Net long declining.",
    "GBP dropped from Bullish to Neutral this week (index fell below 60). "
    "Both lev funds building short hedges AND asset mgrs walking away (-7.7k longs) simultaneously. "
    "Double-sided pressure. Still net long +22k but if index falls below 40 next week → Bearish flip risk.")

story += iblock("JPY", "Bearish", "0/100", "-99,844", "-88,063", "-11,781",
    "No lev signal. Added 14,304 JPY shorts; minor long add +2,523. MM Adding JPY longs (divergence).",
    "Index 0/100 = historically maximum JPY bearish positioning. -99.8k net short is deeply crowded. "
    "Asset mgrs diverging (+4,293 JPY longs) is the key risk signal — at crowded extremes "
    "this divergence historically precedes sharp short squeezes. USD/JPY bullish but risk is elevated.",
    warn=True)

story += iblock("USD", "Bearish", "29/100", "-13,656", "-11,112", "-2,544",
    "No lev signal. Added +2,159 USD longs but added 4,703 shorts — net deteriorated.",
    "Lev funds continue building USD shorts. Index 29/100 = in the lower third of history. "
    "Asset mgrs adding USD longs (+3,989) is a divergence — could be hedging FX books or slow-money "
    "USD bull position beginning to build.")

story += iblock("NZD", "Bearish", "16/100", "-22,254", "-27,725", "+5,471",
    "LEV FLIPPED. Covered 1,281 NZD shorts + added 4,190 longs. MM Adding longs.",
    "NZD LEV FLIPPED — prev delta was deeply negative (-14k), this week +5.5k = risk-on signal. "
    "Second-largest FX covering move after EUR. Index still at 16/100 (bearish), "
    "but the direction change mirrors the broader risk-on tone in NQ/ES.")

# ── VIX ───────────────────────────────────────────────────────────────────────
story += [HR(), P("VIX  (TFF — Lev Fund, Inverted for Market Direction)", sSect)]

story += iblock("VIX", "Bearish", "23/100", "-35,290 raw", "-33,033 raw", "+2,257 inv.",
    "LEV FLIPPED (inverted). Dumped 13,079 VIX longs; covered 10,822 shorts.",
    "Index 23/100 after inversion = historically, lev funds have held MORE net short VIX (less fear) "
    "than today — meaning current positioning is relatively FEARFUL = market Bearish by COT index. "
    "The ▲+2.3k delta and LEV FLIPPED show fear is REDUCING this week, which is bullish directionally, "
    "but the absolute index remains in bearish territory. Watch for index to recover toward 40+.")

# ── COMMODITIES ───────────────────────────────────────────────────────────────
story += [HR(), P("COMMODITIES  (Disaggregated Dataset — Managed Money Net, kh3c-gbw2)", sSect)]

story += iblock("GOLD", "Bearish", "11/100", "+105,863", "+113,563", "-7,700",
    "Lev Pressing. Trimmed ~7.7k net this week; rate of selling accelerating vs recent avg.",
    "IMPORTANT: Despite +106k Mgd Money net long, index is 11/100 — Bearish by COT method. "
    "Managed Money gold positioning has reached 200k+ at prior peaks; +106k is in the lower 11% of history. "
    "The COT index measures RELATIVE positioning, not absolute. Lev Pressing signal = this week's "
    "trim is larger than recent 4-week average — worth monitoring for continued de-positioning.")

story += iblock("SILVER", "Bearish", "12/100", "+10,403", "+11,003", "-600",
    "No signals. Minor long trim; Mgd Money positioning near historical minimum.",
    "Index 12/100 = near historical minimum relative positioning for SILVER Managed Money. "
    "Similar to gold: still net long in absolute terms but historically very underweight. "
    "Mgd Money has been 50k+ net long in silver at prior highs; +10k is a fraction of that. "
    "Minor trim this week (-0.6k). No directional urgency but the relative signal is strongly bearish.")

story += iblock("CRUDE", "Bullish", "73/100", "+94,725", "+95,825", "-1,100",
    "No signals. Mild trim (-1.1k) but Mgd Money positioning in upper third of history.",
    "KEY CHANGE vs prior analysis: under the Managed Money (disaggregated) method, CRUDE is Bullish 73/100 "
    "— vs Neutral 46/100 under the old Non-Commercial (legacy) method. This is because Mgd Money funds "
    "are historically well-positioned in crude at +95k even after a mild trim. "
    "The prior -25.6k weekly dump was in NonComm positioning (different trader category). "
    "No lev or MM signal fired — the mild -1.1k trim is within normal noise. "
    "COT signal for crude is constructively bullish until index falls below 60.")

# ── Key watch items ───────────────────────────────────────────────────────────
story += [HR(), P("KEY WATCH ITEMS — NEXT WEEK", sSect)]

watches = [
    ("EUR  0/100", "Lev funds at historical extreme short while asset mgrs add longs — divergence must resolve. "
                   "If asset mgrs capitulate: EUR Bearish confirmed. If lev funds cover: potential squeeze."),
    ("JPY  0/100", "Net short -100k is crowded. Asset mgr divergence at this extreme historically precedes "
                   "short squeezes. Watch for USD/JPY reversal signals."),
    ("CRUDE  73/100","Mgd Money Bullish at 73/100 — COT signal is constructively bullish. Mild trim (-1.1k) this "
                    "week is not a reversal. Watch for sustained selling that would break index below 60."),
    ("GOLD  11/100", "Mgd Money at 11/100 — accelerating trim (-7.7k) with Lev Pressing signal. "
                    "If index drops below 5 and selling continues, extreme bearish signal fires."),
    ("NQ/ES  FLIPPED","Both have LEV FLIPPED — direction changed from bearish to bullish. "
                      "Sustained bias change requires index to recover above 40+. NQ at 27 is closer."),
    ("GBP  45/100", "Both lev funds and asset mgrs reducing simultaneously. Next week watch for net "
                    "to drop below +15k as the Bearish flip trigger."),
]

wdata = [["Asset / Index","Watch Note"]] + [[s,n] for s,n in watches]
wts = tbl_base()
wts.add("FONTNAME",  (0,1),(0,-1), "Helvetica-Bold")
wts.add("TEXTCOLOR", (0,1),(0,-1), AMBER)
wt = Table(wdata, colWidths=[30*mm, W-28*mm-30*mm], repeatRows=1)
wt.setStyle(wts)
story.append(wt)

# ── Bias corrections note ─────────────────────────────────────────────────────
story += [SP(8), HR(),
    P("<b>Note on Bias Methodology:</b>  The COT Index uses RELATIVE positioning vs history "
      "(Larry Williams method), not absolute net long/short. "
      "GOLD and SILVER are Bearish by index despite positive net because Managed Money is underweight "
      "vs their historical peak levels. US30 and BTC are Bullish by index because their current "
      "short positions are small (or at highs) relative to history. "
      "CRUDE is Bullish 73/100 using Managed Money (disaggregated) — this differs from the "
      "legacy NonComm method which showed Neutral 46/100; Managed Money is the industry-standard category. "
      "Always read the Index value alongside the bias label.", sCap),
    SP(8), HR(),
    P("Trading Bot V4  ·  Paper Trading POC  ·  CFTC publicreporting.cftc.gov  ·  "
      "TFF gpe5-46if (Lev Funds) / Disaggregated kh3c-gbw2 (Mgd Money)  ·  "
      "Updated after data source switch from NonComm to Managed Money for commodities  ·  "
      "Biases verified against live dashboard rendering on Jun 14 2026  ·  "
      "For research purposes only — not financial advice.", sFooter),
]

def dark_bg(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(BG)
    canvas.rect(0, 0, W, H, fill=1, stroke=0)
    canvas.restoreState()

doc.build(story, onFirstPage=dark_bg, onLaterPages=dark_bg)
print(f"PDF written: {OUT}")
