"""
generate_architecture_pdf.py
Generates a PDF architecture document for the Trading Bot system.
Run locally: python generate_architecture_pdf.py
Output: Trading_Bot_Architecture.pdf
"""

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
)
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from datetime import datetime

OUTPUT = "Trading_Bot_Architecture.pdf"

# ── Colour palette ──────────────────────────────────────────
C_BG        = colors.HexColor("#0d1117")   # dark bg (not used on white PDF)
C_RAIL      = colors.HexColor("#7c3aed")   # Railway purple
C_GH        = colors.HexColor("#24292f")   # GitHub dark
C_OANDA     = colors.HexColor("#1d4ed8")   # OANDA blue
C_TG        = colors.HexColor("#0088cc")   # Telegram blue
C_TV        = colors.HexColor("#1e53e5")   # TradingView blue
C_PG        = colors.HexColor("#336791")   # PostgreSQL blue
C_ANTHRO    = colors.HexColor("#d97706")   # Anthropic amber
C_GREEN     = colors.HexColor("#16a34a")
C_LIGHT     = colors.HexColor("#f3f4f6")
C_MID       = colors.HexColor("#e5e7eb")
C_DARK      = colors.HexColor("#1f2937")
C_TEXT      = colors.HexColor("#111827")
WHITE       = colors.white

W = letter[0]

# ── Styles ──────────────────────────────────────────────────
def sty(name, **kw):
    base = {"fontName": "Helvetica", "fontSize": 10, "leading": 14,
            "textColor": C_TEXT, "spaceAfter": 4}
    base.update(kw)
    return ParagraphStyle(name, **base)

S_TITLE   = sty("title",  fontName="Helvetica-Bold", fontSize=22, leading=28,
                textColor=C_RAIL, alignment=TA_CENTER, spaceAfter=4)
S_SUB     = sty("sub",    fontName="Helvetica", fontSize=11, textColor=colors.HexColor("#6b7280"),
                alignment=TA_CENTER, spaceAfter=16)
S_H1      = sty("h1",     fontName="Helvetica-Bold", fontSize=14, leading=18,
                textColor=C_DARK, spaceBefore=14, spaceAfter=6)
S_H2      = sty("h2",     fontName="Helvetica-Bold", fontSize=11, leading=14,
                textColor=C_DARK, spaceBefore=8, spaceAfter=4)
S_BODY    = sty("body",   fontSize=9, leading=13)
S_CODE    = sty("code",   fontName="Courier", fontSize=8, leading=12,
                textColor=colors.HexColor("#374151"), backColor=C_LIGHT)
S_CAPTION = sty("caption", fontSize=8, textColor=colors.HexColor("#6b7280"),
                alignment=TA_CENTER, spaceBefore=2, spaceAfter=8)
S_CENTER  = sty("center", alignment=TA_CENTER)
S_BADGE   = sty("badge",  fontName="Helvetica-Bold", fontSize=9,
                textColor=WHITE, alignment=TA_CENTER)

def hr():
    return HRFlowable(width="100%", thickness=1, color=C_MID, spaceAfter=10, spaceBefore=4)

def sp(h=6):
    return Spacer(1, h)

# ── Table helpers ───────────────────────────────────────────

def header_row(cells, bg=C_RAIL):
    return [Paragraph(f"<b>{c}</b>", sty("th", fontName="Helvetica-Bold",
            fontSize=9, textColor=WHITE, alignment=TA_CENTER)) for c in cells]

def cell(text, bold=False, center=False, color=C_TEXT):
    fn = "Helvetica-Bold" if bold else "Helvetica"
    al = TA_CENTER if center else TA_LEFT
    return Paragraph(text, sty("td", fontName=fn, fontSize=9, textColor=color, alignment=al))

def base_ts(col_widths):
    return TableStyle([
        ("BACKGROUND",  (0, 0), (-1, 0),  C_RAIL),
        ("TEXTCOLOR",   (0, 0), (-1, 0),  WHITE),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, C_LIGHT]),
        ("GRID",        (0, 0), (-1, -1), 0.4, C_MID),
        ("TOPPADDING",  (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",(0,0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING",(0, 0), (-1, -1), 6),
        ("VALIGN",      (0, 0), (-1, -1), "MIDDLE"),
    ])

def colored_badge(text, bg):
    return Table([[Paragraph(f"<b>{text}</b>", S_BADGE)]],
                 colWidths=[1.1*inch],
                 style=TableStyle([
                     ("BACKGROUND", (0,0), (-1,-1), bg),
                     ("ROUNDEDCORNERS", [4]),
                     ("TOPPADDING",    (0,0), (-1,-1), 3),
                     ("BOTTOMPADDING", (0,0), (-1,-1), 3),
                 ]))

def flow_row(items):
    """Horizontal flow diagram: [label, arrow, label, arrow, ...]"""
    cells = []
    for i, item in enumerate(items):
        if item == "→":
            cells.append(Paragraph("→", sty("arr", fontSize=16, alignment=TA_CENTER,
                                             textColor=C_RAIL)))
        else:
            bg, txt = item
            cells.append(Table([[Paragraph(f"<b>{txt}</b>",
                sty("fb", fontName="Helvetica-Bold", fontSize=8,
                    textColor=WHITE, alignment=TA_CENTER))]],
                colWidths=[1.3*inch],
                style=TableStyle([
                    ("BACKGROUND",    (0,0), (-1,-1), bg),
                    ("TOPPADDING",    (0,0), (-1,-1), 5),
                    ("BOTTOMPADDING", (0,0), (-1,-1), 5),
                    ("LEFTPADDING",   (0,0), (-1,-1), 4),
                    ("RIGHTPADDING",  (0,0), (-1,-1), 4),
                ])))
    widths = []
    for item in items:
        if item == "→":
            widths.append(0.35*inch)
        else:
            widths.append(1.3*inch)
    return Table([cells], colWidths=widths,
                 style=TableStyle([
                     ("VALIGN",  (0,0), (-1,-1), "MIDDLE"),
                     ("ALIGN",   (0,0), (-1,-1), "CENTER"),
                     ("LEFTPADDING",  (0,0), (-1,-1), 0),
                     ("RIGHTPADDING", (0,0), (-1,-1), 0),
                 ]))


# ── Build PDF ───────────────────────────────────────────────

def build():
    doc = SimpleDocTemplate(OUTPUT, pagesize=letter,
                            leftMargin=0.75*inch, rightMargin=0.75*inch,
                            topMargin=0.75*inch, bottomMargin=0.75*inch)
    story = []
    now = datetime.now().strftime("%B %d, %Y")

    # ── Cover ────────────────────────────────────────────────
    story += [
        sp(20),
        Paragraph("Trading Bot — System Architecture", S_TITLE),
        Paragraph("Wyckoff Range Strategy · Paper Trading POC · v5", S_SUB),
        Paragraph(f"Generated {now}", S_CAPTION),
        hr(),
        sp(4),
    ]

    # ── 1. System Overview ───────────────────────────────────
    story.append(Paragraph("1. System Overview", S_H1))
    story.append(Paragraph(
        "A fully cloud-hosted automated paper trading system. TradingView Pine Script detects "
        "Wyckoff range setups on 15-minute charts and fires webhook alerts to a Flask bot "
        "running on Railway. The bot executes orders on an OANDA demo account (forex pairs), "
        "logs all data to a Railway PostgreSQL database, and notifies via Telegram. "
        "A scheduled Anthropic cloud agent monitors the bot daily. "
        "<b>No local machine is required for any part of the system to operate.</b>",
        S_BODY))
    story.append(sp(8))

    # Signal flow diagram
    story.append(Paragraph("Live Signal Flow", S_H2))
    story.append(flow_row([
        (C_TV,    "TradingView\n15M Alert"),
        "→",
        (C_RAIL,  "Railway\nFlask Bot"),
        "→",
        (C_OANDA, "OANDA\nDemo API"),
    ]))
    story.append(sp(4))
    story.append(flow_row([
        (C_TV,    "TradingView\n15M Alert"),
        "→",
        (C_RAIL,  "Railway\nFlask Bot"),
        "→",
        (C_TG,    "Telegram\nNotification"),
    ]))
    story.append(sp(4))
    story.append(flow_row([
        (C_TV,    "TradingView\n15M Alert"),
        "→",
        (C_RAIL,  "Railway\nFlask Bot"),
        "→",
        (C_PG,    "PostgreSQL\nDB Log"),
    ]))
    story.append(sp(6))

    # Daily monitor flow
    story.append(Paragraph("Daily Monitoring Flow (7am CT)", S_H2))
    story.append(flow_row([
        (C_ANTHRO, "Anthropic\nCloud Agent"),
        "→",
        (C_RAIL,   "GET /report\nRailway Bot"),
        "→",
        (C_TG,     "Telegram\nSummary"),
    ]))
    story.append(sp(8))

    # Deploy flow
    story.append(Paragraph("Deploy Flow", S_H2))
    story.append(flow_row([
        (C_GH,   "GitHub\npaper-trading"),
        "→",
        (C_RAIL, "Railway\nAuto-Deploy"),
        "→",
        (C_RAIL, "Live Bot\n~2 min"),
    ]))
    story.append(sp(10))

    # ── 2. Where Everything Lives ────────────────────────────
    story.append(hr())
    story.append(Paragraph("2. Where Everything Lives", S_H1))

    housing_data = [
        header_row(["Component", "Platform", "URL / Location", "Always On?", "Local Machine?"]),
        [cell("Bot Runtime (Flask)"),    cell("Railway", bold=True, color=C_RAIL),
         cell("tradingbot-production-1e5a.up.railway.app"),
         cell("✅ 24/7", center=True, color=C_GREEN), cell("❌ No", center=True)],
        [cell("Source Code"),            cell("GitHub", bold=True, color=C_GH),
         cell("github.com/wasiheider/trading_bot"),
         cell("✅", center=True, color=C_GREEN), cell("❌ No", center=True)],
        [cell("Database"),               cell("Railway PostgreSQL", bold=True, color=C_PG),
         cell("Same Railway project — DATABASE_URL injected"),
         cell("✅ 24/7", center=True, color=C_GREEN), cell("❌ No", center=True)],
        [cell("Env Vars / Secrets"),     cell("Railway Variables", bold=True, color=C_RAIL),
         cell("trading_bot service → Variables tab"),
         cell("✅", center=True, color=C_GREEN), cell("❌ No", center=True)],
        [cell("TradingView Alerts"),     cell("TradingView Cloud", bold=True, color=C_TV),
         cell("12 × 15M charts, paper-trading alerts"),
         cell("✅ 24/7", center=True, color=C_GREEN), cell("❌ No", center=True)],
        [cell("Order Execution"),        cell("OANDA Demo API", bold=True, color=C_OANDA),
         cell("api-fxpractice.oanda.com · Acct 101-001-39435783-001"),
         cell("✅ 24/7", center=True, color=C_GREEN), cell("❌ No", center=True)],
        [cell("Notifications"),          cell("Telegram", bold=True, color=C_TG),
         cell("Bot token + chat ID in Railway env vars"),
         cell("✅", center=True, color=C_GREEN), cell("❌ No", center=True)],
        [cell("Daily Monitor Agent"),    cell("Anthropic Cloud", bold=True, color=C_ANTHRO),
         cell("claude.ai/code/routines · 7am CT daily"),
         cell("✅ Scheduled", center=True, color=C_GREEN), cell("❌ No", center=True)],
        [cell("CLAUDE.md / README"),     cell("GitHub", bold=True, color=C_GH),
         cell("In repo root — loaded automatically by Claude Code"),
         cell("✅", center=True, color=C_GREEN), cell("❌ No", center=True)],
    ]
    cw = [1.35*inch, 1.2*inch, 2.45*inch, 0.7*inch, 0.8*inch]
    t = Table(housing_data, colWidths=cw)
    t.setStyle(base_ts(cw))
    story += [t, sp(4),
              Paragraph("★  Zero local dependencies — delete the local folder and the system keeps running.",
                        sty("note", fontSize=8, textColor=C_GREEN, fontName="Helvetica-Bold")),
              sp(10)]

    # ── 3. Railway Project Layout ────────────────────────────
    story.append(hr())
    story.append(Paragraph("3. Railway Project Layout  (heartfelt-serenity / production)", S_H1))

    rail_data = [
        header_row(["Service", "Status", "Volume", "Key Detail"]),
        [cell("trading_bot", bold=True), cell("🟢 Online", color=C_GREEN),
         cell("—"),
         cell("Flask app · paper-trading branch · US West")],
        [cell("Postgres", bold=True),    cell("🟢 Online", color=C_GREEN),
         cell("postgres-volume"),
         cell("DATABASE_URL auto-injected into trading_bot")],
    ]
    cw2 = [1.2*inch, 0.9*inch, 2.0*inch, 2.45*inch]
    t2 = Table(rail_data, colWidths=cw2)
    t2.setStyle(base_ts(cw2))
    story += [t2, sp(10)]

    # ── 4. Database Schema ───────────────────────────────────
    story.append(hr())
    story.append(Paragraph("4. PostgreSQL Database Schema", S_H1))

    db_data = [
        header_row(["Table", "Primary Purpose", "Key Columns"]),
        [cell("trades", bold=True),
         cell("Full trade ledger — every entry and outcome"),
         cell("instrument, direction, setup, result, pnl, oanda_trade_id")],
        [cell("bot_state", bold=True),
         cell("Daily/weekly PNL, balance, win/loss counters (1 row)"),
         cell("account_balance, daily_pnl, weekly_pnl, total_wins, total_losses")],
        [cell("signals", bold=True),
         cell("Every incoming TradingView webhook — full pipeline trace"),
         cell("symbol, direction, setup, entry_price, sl, tp1, tp2, rr_to_tp1")],
    ]
    cw3 = [0.9*inch, 2.4*inch, 3.25*inch]
    t3 = Table(db_data, colWidths=cw3)
    t3.setStyle(base_ts(cw3))
    story += [t3, sp(4),
              Paragraph("All DB operations route through db.py — never write directly to tables from other modules.",
                        sty("note2", fontSize=8, textColor=colors.HexColor("#6b7280"))),
              sp(10)]

    # ── 5. Strategy ──────────────────────────────────────────
    story.append(hr())
    story.append(Paragraph("5. Strategy — v5 (Active)", S_H1))

    entry_data = [
        header_row(["Model", "Setup Field", "Trigger", "Execution"]),
        [cell("A — BOS + Rejection",    bold=True), cell('"bos" or ""'),
         cell("15M BOS near boundary → retest → rejection candle"),
         cell("OANDA (forex) / Telegram (others)")],
        [cell("B — Spring / Upthrust",  bold=True), cell('"spring" / "upthrust"'),
         cell("Wick beyond boundary closes back inside; deferred entry"),
         cell("OANDA (forex) / Telegram (others)")],
        [cell("C — BOS + RSI Div",      bold=True), cell('"bos_div"'),
         cell("Same as A + RSI divergence at retest (30–70); priority over A"),
         cell("OANDA (forex) / Telegram (others)")],
        [cell("D — Mid BOS + Retest",   bold=True), cell('"mid_bos"'),
         cell("15M structural BOS within ±35% of range mid → retest"),
         cell("OANDA (forex) / Telegram (others)")],
    ]
    cw4 = [1.3*inch, 0.9*inch, 2.6*inch, 1.75*inch]
    t4 = Table(entry_data, colWidths=cw4)
    t4.setStyle(base_ts(cw4))
    story += [t4, sp(6)]

    tp_data = [
        header_row(["Parameter", "Value"]),
        [cell("Range logic"),       cell("4H Pivot-Anchored — levels lock until broken by 30% of range size")],
        [cell("Quality Gate"),      cell("Boundary touches ≥ 3  ·  Mid crosses ≥ 3  ·  30M swing pattern")],
        [cell("TP1"),               cell("1:1 RR — close 50% + SL moves to breakeven")],
        [cell("TP2"),               cell("1:3 RR — close remaining 50%")],
        [cell("Trailing SL"),       cell("After TP1: at 1.5R peak → move to 1R, then trail 0.5R behind peak")],
        [cell("Risk per trade"),    cell("$250 (0.25% of $100k account)")],
        [cell("Daily loss limit"),  cell("$4,000 (4%)")],
        [cell("Weekly loss limit"), cell("$10,000 (10%)")],
        [cell("Pine Script"),       cell("pine_script_paper_v5.pine — 15M chart, Once Per Bar Close alert")],
    ]
    cw5 = [1.5*inch, 5.05*inch]
    t5 = Table(tp_data, colWidths=cw5)
    t5.setStyle(base_ts(cw5))
    story += [t5, sp(10)]

    # ── 6. Instruments ───────────────────────────────────────
    story.append(hr())
    story.append(Paragraph("6. Instruments", S_H1))

    inst_data = [
        header_row(["Instrument", "OANDA Execution", "Telegram + Log", "Note"]),
        [cell("EURUSD, GBPUSD, USDJPY, EURNZD, NZDUSD"),
         cell("✅ Yes", center=True, color=C_GREEN),
         cell("✅ Yes", center=True, color=C_GREEN),
         cell("Forex pairs — full execution")],
        [cell("XAUUSD, XAGUSD, US100, US30, US500, USOIL, BTCUSD"),
         cell("❌ No",  center=True, color=colors.red),
         cell("✅ Yes", center=True, color=C_GREEN),
         cell("CFDs/metals/indices — OANDA demo doesn't support")],
    ]
    cw6 = [2.3*inch, 1.1*inch, 1.1*inch, 2.05*inch]
    t6 = Table(inst_data, colWidths=cw6)
    t6.setStyle(base_ts(cw6))
    story += [t6, sp(10)]

    # ── 7. API Endpoints ─────────────────────────────────────
    story.append(hr())
    story.append(Paragraph("7. API Endpoints", S_H1))

    ep_data = [
        header_row(["Endpoint", "Method", "Description"]),
        [cell("/"),              cell("GET",  center=True), cell("Health check — returns strategy version + CT time")],
        [cell("/dashboard"),     cell("GET",  center=True), cell("Live trading dashboard (HTML)")],
        [cell("/state"),         cell("GET",  center=True), cell("Balance, PNL, open trades, per-model stats (A/B/C/D)")],
        [cell("/trades"),        cell("GET",  center=True), cell("Full trade ledger from PostgreSQL")],
        [cell("/report"),        cell("GET",  center=True), cell("Daily monitor — sends Telegram summary (called by cloud agent)")],
        [cell("/news"),          cell("GET",  center=True), cell("Yahoo Finance RSS headlines (15-min cache)")],
        [cell("/calendar"),      cell("GET",  center=True), cell("ForexFactory economic calendar (1-hour cache)")],
        [cell("/webhook/paper"), cell("POST", center=True), cell("TradingView signal receiver — entries + lifecycle events")],
        [cell("/admin/reset"),   cell("POST", center=True), cell("Full state reset — requires PAPER_WEBHOOK_TOKEN")],
    ]
    cw7 = [1.5*inch, 0.65*inch, 4.4*inch]
    t7 = Table(ep_data, colWidths=cw7)
    t7.setStyle(base_ts(cw7))
    story += [t7, sp(10)]

    # ── 8. File Map ──────────────────────────────────────────
    story.append(hr())
    story.append(Paragraph("8. Source File Map  (github.com/wasiheider/trading_bot)", S_H1))

    file_data = [
        header_row(["File", "Role"]),
        [cell("main.py",                    bold=True), cell("Entry point — scheduler (midnight reset, weekly summary, heartbeat) + Flask server")],
        [cell("server.py",                  bold=True), cell("Flask app — all endpoints, webhook handler, /report monitor")],
        [cell("db.py",                      bold=True), cell("PostgreSQL layer — all CRUD for trades, bot_state, signals tables")],
        [cell("risk.py",                    bold=True), cell("Position sizing, PNL limit checks, in-memory paper_state backed by PostgreSQL")],
        [cell("oanda.py",                   bold=True), cell("OANDA REST API — place/close orders, open trades, account summary")],
        [cell("config.py",                  bold=True), cell("All environment variables and risk constants")],
        [cell("notifier.py",                bold=True), cell("Telegram send helper — parse_mode: HTML")],
        [cell("logger.py",                  bold=True), cell("Legacy SQLite schema — not actively wired in current flow")],
        [cell("pine_script_paper_v5.pine",  bold=True), cell("Active Pine Script — load on all 12 × 15M TradingView charts")],
        [cell("dashboard.html",             bold=True), cell("Single-file live dashboard served at /dashboard")],
        [cell("CLAUDE.md",                  bold=True), cell("Auto-loaded by Claude Code — full project brief and constraints")],
        [cell("README.md",                  bold=True), cell("Public project documentation")],
    ]
    cw8 = [2.0*inch, 4.55*inch]
    t8 = Table(file_data, colWidths=cw8)
    t8.setStyle(base_ts(cw8))
    story += [t8, sp(10)]

    # ── 9. Environment Variables ─────────────────────────────
    story.append(hr())
    story.append(Paragraph("9. Environment Variables  (stored in Railway — never in code)", S_H1))

    env_data = [
        header_row(["Variable", "Description"]),
        [cell("DATABASE_URL"),          cell("PostgreSQL connection string — auto-injected from Postgres service")],
        [cell("OANDA_API_TOKEN"),       cell("OANDA demo API key")],
        [cell("OANDA_ACCOUNT_ID"),      cell("101-001-39435783-001")],
        [cell("OANDA_BASE_URL"),        cell("https://api-fxpractice.oanda.com")],
        [cell("TELEGRAM_BOT_TOKEN"),    cell("Telegram bot token")],
        [cell("TELEGRAM_CHAT_ID"),      cell("Telegram chat ID for notifications")],
        [cell("PAPER_WEBHOOK_TOKEN"),   cell("Shared secret — must match Pine Script token input")],
        [cell("PAPER_ACCOUNT_SIZE"),    cell("100000 (starting demo balance)")],

        [cell("ANTHROPIC_API_KEY"),     cell("Reserved for future Claude integration")],
    ]
    cw9 = [1.8*inch, 4.75*inch]
    t9 = Table(env_data, colWidths=cw9)
    t9.setStyle(base_ts(cw9))
    story += [t9, sp(10)]

    # ── 10. Automation Summary ───────────────────────────────
    story.append(hr())
    story.append(Paragraph("10. Automation Summary — What Runs Without Human Intervention", S_H1))

    auto_data = [
        header_row(["Function", "Trigger", "Automated?"]),
        [cell("Signal detection"),         cell("TradingView 15M bar close"),     cell("✅ Fully automated", color=C_GREEN)],
        [cell("Webhook delivery"),         cell("TradingView alert fires"),        cell("✅ Fully automated", color=C_GREEN)],
        [cell("Risk check + sizing"),      cell("Every incoming signal"),          cell("✅ Fully automated", color=C_GREEN)],
        [cell("OANDA order placement"),    cell("Signal approved"),                cell("✅ Fully automated", color=C_GREEN)],
        [cell("TP1/TP2/SL management"),    cell("TradingView lifecycle alert"),    cell("✅ Fully automated", color=C_GREEN)],
        [cell("Telegram notifications"),   cell("Every trade event"),              cell("✅ Fully automated", color=C_GREEN)],
        [cell("Daily PNL reset"),          cell("Midnight CT (scheduler)"),        cell("✅ Fully automated", color=C_GREEN)],
        [cell("Weekly PNL reset"),         cell("Monday midnight CT"),             cell("✅ Fully automated", color=C_GREEN)],
        [cell("Weekly summary"),           cell("Friday 3:50pm CT (scheduler)"),  cell("✅ Fully automated", color=C_GREEN)],
        [cell("Daily monitor report"),     cell("7am CT (Anthropic cloud agent)"), cell("✅ Fully automated", color=C_GREEN)],
        [cell("Database persistence"),     cell("Every trade / state change"),     cell("✅ Fully automated", color=C_GREEN)],
        [cell("Code deployment"),          cell("git push to paper-trading"),      cell("✅ Fully automated", color=C_GREEN)],
        [cell("Prop firm trade entry"),    cell("Manual — FTMO/Apex have no API"), cell("⚠️ Manual — always", color=colors.HexColor("#d97706"))],
        [cell("TradingView alert setup"),  cell("When Pine Script is updated"),    cell("⚠️ Manual — one-time", color=colors.HexColor("#d97706"))],
    ]
    cw10 = [2.0*inch, 2.3*inch, 2.25*inch]
    t10 = Table(auto_data, colWidths=cw10)
    t10.setStyle(base_ts(cw10))
    story += [t10, sp(10)]

    # ── Footer ───────────────────────────────────────────────
    story.append(hr())
    story.append(Paragraph(
        f"Trading Bot Architecture · Paper Trading POC · Generated {now} · "
        "github.com/wasiheider/trading_bot",
        sty("foot", fontSize=7, textColor=colors.HexColor("#9ca3af"), alignment=TA_CENTER)
    ))

    doc.build(story)
    print(f"PDF generated: {OUTPUT}")


if __name__ == "__main__":
    build()
