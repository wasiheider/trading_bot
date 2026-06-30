import db
db.init_db()  # must run before risk import triggers module-level state load

import time
import schedule
import threading
import requests
from datetime import datetime
import pytz

from risk import reset_paper_daily, reset_paper_weekly, paper_state
from notifier import send_telegram

CT = pytz.timezone("America/Chicago")

def ct_now():
    return datetime.now(CT)

def log(msg):
    print(f"[{ct_now().strftime('%Y-%m-%d %H:%M:%S')} CT] {msg}", flush=True)


# ── Midnight Reset ─────────────────────────────────────────

def midnight_reset():
    now = ct_now()
    if now.weekday() == 0:
        reset_paper_weekly()
        log("Weekly reset — paper weekly counters cleared")
        send_telegram("🔄 <b>Weekly Reset</b>\nPaper trading weekly P&L and SL counters reset.")
    reset_paper_daily()
    log("Midnight reset — paper daily counters cleared")
    send_telegram("🔄 <b>Midnight Reset</b>\nPaper trading daily P&L and counters reset.")


# ── Weekly Summary (Friday 3:50 PM CT) ────────────────────

def weekly_summary():
    if ct_now().weekday() != 4:
        return
    log("Generating weekly summary...")

    now = ct_now()
    days_since_monday = now.weekday()
    week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = week_start.replace(day=now.day - days_since_monday)

    def parse_dt(t):
        date_str = t.get("date") or ""
        if not date_str:
            return None
        try:
            from datetime import datetime as _dt
            naive = _dt.strptime(str(date_str)[:16], "%Y-%m-%d %H:%M")
            return CT.localize(naive)
        except Exception:
            return None

    try:
        all_trades = db.load_trades()
    except Exception:
        all_trades = []

    weekly = [t for t in all_trades if parse_dt(t) and parse_dt(t) >= week_start]
    closed = [t for t in weekly if t.get("result") and t["result"] != "OPEN"]
    wins   = [t for t in closed if "TP" in (t.get("result") or "")]
    losses = [t for t in closed if "SL" in (t.get("result") or "")]
    pnl    = sum(t.get("pnl") or 0 for t in closed)
    wr     = round(len(wins) / len(closed) * 100) if closed else 0

    balance  = paper_state.get("account_balance", 100000)
    week_str = f"{week_start.strftime('%b %d')} – {now.strftime('%b %d, %Y')}"
    pnl_emoji = "🟢" if pnl >= 0 else "🔴"

    msg = (
        f"📊 <b>Weekly Summary — {week_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Paper Trading (POC)</b>\n"
        f"Trades: <code>{len(closed)}</code> ({len(wins)}W / {len(losses)}L)\n"
        f"Win Rate: <code>{wr}%</code>\n"
        f"Week P&L: {pnl_emoji} <code>${pnl:+,.2f}</code>\n"
        f"Balance: <code>${balance:,.2f}</code>\n"
        f"\n<i>POC validation — 2-week window</i> 🤖"
    )
    send_telegram(msg)
    log("Weekly summary sent")


# ── Heartbeat ──────────────────────────────────────────────

def heartbeat():
    try:
        r = requests.get("https://tradingbot-production-1e5a.up.railway.app")
        log(f"Heartbeat — status {r.status_code}")
    except Exception as e:
        log(f"Heartbeat failed: {e}")


# ── Scheduler ──────────────────────────────────────────────

def purge_stale_non_forex_opens():
    import oanda
    forex_instruments = set(oanda.INSTRUMENT_MAP.keys())
    deleted = db.delete_stale_non_forex_opens(forex_instruments, min_age_hours=6)
    if deleted:
        log(f"[scheduler] Purged {deleted} stale non-forex OPEN/UNKNOWN trade(s)")


def run_scheduler():
    schedule.every().day.at("05:00").do(midnight_reset)
    schedule.every().day.at("20:50").do(weekly_summary)
    schedule.every().hour.do(heartbeat)
    schedule.every(6).hours.do(purge_stale_non_forex_opens)

    log("Scheduler started — midnight reset 00:00 CT | weekly summary Fri 15:50 CT | heartbeat hourly | stale-open purge every 6h")

    while True:
        schedule.run_pending()
        time.sleep(30)


# ── Entry point ────────────────────────────────────────────

if __name__ == "__main__":
    log("main.py starting — paper trading mode")
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()

    from server import app
    app.run(host="0.0.0.0", port=8080)
