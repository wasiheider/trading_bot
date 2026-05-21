import time
import schedule
import threading
import requests
from datetime import datetime
import pytz
import json
import os

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from risk import reset_tpt_daily, reset_ftmo_daily, reset_tpt_intraday, tpt_state, ftmo_state
from notifier import send_telegram

CT = pytz.timezone("America/Chicago")

# ── Helpers ────────────────────────────────────────────────
def ct_now():
    return datetime.now(CT)

def log(msg):
    print(f"[{ct_now().strftime('%Y-%m-%d %H:%M:%S')} CT] {msg}", flush=True)

# ── TPT Kill Switch (3:55 PM CT, Mon–Thu only) ────────────
def tpt_kill():
    if ct_now().weekday() in (5, 6):  # Sat/Sun — skip, weekend handles it
        return
    tpt_state["killed"] = True
    from risk import _save_state
    _save_state()
    log("TPT kill switch fired — maintenance window 3:55–5:00 PM CT")
    send_telegram("🛑 *TPT Kill Switch*\nMarket entering maintenance window.\nSignals resume at 5:00 PM CT.")

# ── TPT Hard Close Warning (3:53 PM CT, Mon–Thu only) ─────
def tpt_close_warning():
    if ct_now().weekday() in (5, 6):  # Sat/Sun — skip
        return
    log("TPT 2-min warning — close any open positions now")
    send_telegram("⚠️ *TPT 2-Min Warning*\nClose all open TPT positions — hard cutoff at 3:55 PM CT.")

# ── TPT Market Reopen (5:00 PM CT, Tue–Fri only) ──────────
def tpt_reopen():
    if ct_now().weekday() in (5, 6):  # Sat/Sun — skip, weekend handles it
        return
    tpt_state["killed"] = False
    reset_tpt_intraday()        # clear last_signal so dashboard doesn't show stale trade
    from risk import _save_state
    _save_state()
    log("TPT market reopen — signals active (full futures session)")
    send_telegram("🟢 *TPT Market Open*\nFutures session live — signals active 5:00 PM CT.")

# ── Weekend Kill (Friday 4:00 PM CT) ──────────────────────
def tpt_weekend_kill():
    if ct_now().weekday() != 4:  # Only Friday (0=Mon … 4=Fri)
        return
    tpt_state["killed"] = True
    from risk import _save_state
    _save_state()
    log("TPT weekend kill fired — bot offline until Sunday 5:00 PM CT")
    send_telegram("🛑 *TPT Weekend Shutdown*\nMarkets closed for the weekend.\nSignals resume Sunday 5:00 PM CT.")

# ── Weekend Kill Warning (Friday 3:58 PM CT) ──────────────
def tpt_weekend_kill_warning():
    if ct_now().weekday() != 4:  # Only Friday
        return
    log("TPT weekend 2-min warning — close all positions, shutting down for weekend")
    send_telegram("⚠️ *TPT Weekend Warning*\nClose all open TPT positions — weekend shutdown at 4:00 PM CT.")

# ── Weekend Reopen (Sunday 5:00 PM CT) ────────────────────
def tpt_weekend_reopen():
    if ct_now().weekday() != 6:  # Only Sunday (6=Sun)
        return
    tpt_state["killed"] = False
    reset_tpt_intraday()        # clear last_signal / stale state from Friday
    from risk import _save_state
    _save_state()
    log("TPT weekend reopen — Asian session live, signals active")
    send_telegram("🟢 *TPT Weekend Open*\nFutures session live — Asian session open, signals active.")

# ── Midnight Reset ─────────────────────────────────────────
def midnight_reset():
    reset_tpt_daily()
    reset_ftmo_daily()
    # Monday midnight — reset FTMO weekly consecutive loss counter
    if ct_now().weekday() == 0:  # 0 = Monday
        from risk import reset_ftmo_weekly
        reset_ftmo_weekly()
        log("FTMO weekly reset — consecutive loss counter cleared")
        send_telegram("🔄 *FTMO Weekly Reset*\nConsecutive loss counter cleared — new week active.")
    log("Midnight reset — TPT & FTMO daily counters cleared")
    send_telegram("🔄 *Midnight Reset*\nTPT & FTMO daily P&L and loss counters reset.")

# ── Weekly Summary (Friday 3:50 PM CT) ────────────────────
def weekly_summary():
    if ct_now().weekday() != 4:  # Only Friday
        return
    log("Generating weekly summary...")

    try:
        trades_path = os.path.join(os.path.dirname(__file__), "trades.json")
        if os.path.exists(trades_path):
            with open(trades_path, "r") as f:
                all_trades = json.load(f)
        else:
            all_trades = {"tpt": [], "ftmo": []}
    except Exception as e:
        log(f"Weekly summary — could not load trades.json: {e}")
        all_trades = {"tpt": [], "ftmo": []}

    now = ct_now()
    # Get trades from this week (Mon 00:00 CT to now)
    days_since_monday = now.weekday()  # 0=Mon, 4=Fri
    week_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = week_start.replace(day=now.day - days_since_monday)

    def parse_trade_time(t):
        date_str = t.get("date") or t.get("time") or ""
        if not date_str:
            return None
        try:
            # date field format: "YYYY-MM-DD HH:MM"
            naive = datetime.strptime(date_str[:16], "%Y-%m-%d %H:%M")
            return CT.localize(naive)
        except Exception:
            return None

    def week_stats(trades):
        weekly = [t for t in trades if parse_trade_time(t) and parse_trade_time(t) >= week_start]
        closed = [t for t in weekly if t.get("result") and t["result"] != "OPEN"]
        wins   = [t for t in closed if "TP" in (t.get("result") or "")]
        losses = [t for t in closed if "SL" in (t.get("result") or "")]
        total_pnl = sum(t.get("pnl", 0) for t in closed)
        wr = round(len(wins) / len(closed) * 100) if closed else 0

        # Best session this week
        session_wins = {}
        session_totals = {}
        for t in closed:
            s = t.get("session") or "Unknown"
            session_totals[s] = session_totals.get(s, 0) + 1
            if "TP" in (t.get("result") or ""):
                session_wins[s] = session_wins.get(s, 0) + 1
        best_session = "--"
        best_wr = 0
        for s, total in session_totals.items():
            if total >= 2:
                s_wr = round(session_wins.get(s, 0) / total * 100)
                if s_wr > best_wr:
                    best_wr = s_wr
                    best_session = f"{s} ({s_wr}%)"

        return {
            "total":       len(closed),
            "wins":        len(wins),
            "losses":      len(losses),
            "wr":          wr,
            "pnl":         round(total_pnl, 2),
            "best_session": best_session,
            "open":        len(weekly) - len(closed),
        }

    tpt  = week_stats(all_trades.get("tpt", []))
    ftmo = week_stats(all_trades.get("ftmo", []))

    # All-time stats for context
    def alltime_stats(trades):
        closed = [t for t in trades if t.get("result") and t["result"] != "OPEN"]
        wins   = [t for t in closed if "TP" in (t.get("result") or "")]
        total_pnl = sum(t.get("pnl", 0) for t in closed)
        wr = round(len(wins) / len(closed) * 100) if closed else 0
        return len(closed), wr, round(total_pnl, 2)

    tpt_all_n,  tpt_all_wr,  tpt_all_pnl  = alltime_stats(all_trades.get("tpt",  []))
    ftmo_all_n, ftmo_all_wr, ftmo_all_pnl = alltime_stats(all_trades.get("ftmo", []))

    # TPT drawdown
    tpt_balance  = tpt_state.get("account_balance", 150000)
    tpt_dd_used  = round(150000 - tpt_balance, 2)
    tpt_dd_limit = 4500
    tpt_dd_pct   = round(tpt_dd_used / tpt_dd_limit * 100) if tpt_dd_limit else 0

    # FTMO drawdown
    ftmo_balance  = ftmo_state.get("account_balance", 100000)
    ftmo_dd_used  = round(100000 - ftmo_balance, 2)
    ftmo_dd_limit = 10000
    ftmo_dd_pct   = round(ftmo_dd_used / ftmo_dd_limit * 100) if ftmo_dd_limit else 0

    week_str = f"{week_start.strftime('%b %d')} – {now.strftime('%b %d, %Y')}"

    pnl_emoji = lambda p: "🟢" if p >= 0 else "🔴"

    msg = (
        f"📊 *Weekly Summary — {week_str}*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"\n"
        f"*TPT (MNQ Futures)*\n"
        f"Trades: `{tpt['total']}` ({tpt['wins']}W / {tpt['losses']}L)\n"
        f"Win Rate: `{tpt['wr']}%`\n"
        f"Week P&L: {pnl_emoji(tpt['pnl'])} `${tpt['pnl']:+,.2f}`\n"
        f"Best Session: `{tpt['best_session']}`\n"
        f"Drawdown Used: `${tpt_dd_used:,.0f}` / `${tpt_dd_limit:,}` ({tpt_dd_pct}%)\n"
        f"\n"
        f"*FTMO (Forex/Indices)*\n"
        f"Trades: `{ftmo['total']}` ({ftmo['wins']}W / {ftmo['losses']}L)\n"
        f"Win Rate: `{ftmo['wr']}%`\n"
        f"Week P&L: {pnl_emoji(ftmo['pnl'])} `${ftmo['pnl']:+,.2f}`\n"
        f"Best Session: `{ftmo['best_session']}`\n"
        f"Drawdown Used: `${ftmo_dd_used:,.0f}` / `${ftmo_dd_limit:,}` ({ftmo_dd_pct}%)\n"
        f"\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"*All-Time*\n"
        f"TPT: `{tpt_all_n}` trades | `{tpt_all_wr}%` WR | {pnl_emoji(tpt_all_pnl)} `${tpt_all_pnl:+,.2f}`\n"
        f"FTMO: `{ftmo_all_n}` trades | `{ftmo_all_wr}%` WR | {pnl_emoji(ftmo_all_pnl)} `${ftmo_all_pnl:+,.2f}`\n"
        f"\n"
        f"_Bot resumes Sunday 17:00 CT_ 🤖"
    )

    send_telegram(msg)
    log("Weekly summary sent to Telegram")

# ── Heartbeat (keeps Railway alive) ───────────────────────
def heartbeat():
    try:
        r = requests.get("https://tradingbot-production-1e5a.up.railway.app")
        log(f"Heartbeat — status {r.status_code}")
    except Exception as e:
        log(f"Heartbeat failed: {e}")

# ── Scheduler setup ────────────────────────────────────────
def run_scheduler():
    # ── Weekday cycle (Mon–Thu) ────────────────────────────
    schedule.every().day.at("20:53").do(tpt_close_warning)   # 15:53 CT
    schedule.every().day.at("20:55").do(tpt_kill)             # 15:55 CT
    schedule.every().day.at("22:00").do(tpt_reopen)           # 17:00 CT

    # ── Weekend cycle ──────────────────────────────────────
    schedule.every().day.at("20:50").do(weekly_summary)           # Fri 15:50 CT
    schedule.every().day.at("20:58").do(tpt_weekend_kill_warning) # Fri 15:58 CT
    schedule.every().day.at("21:00").do(tpt_weekend_kill)         # Fri 16:00 CT
    schedule.every().day.at("22:00").do(tpt_weekend_reopen)       # Sun 17:00 CT

    # ── Daily resets (both accounts) — midnight CT = 05:00 UTC
    schedule.every().day.at("05:00").do(midnight_reset)

    # ── Heartbeat (keeps Railway alive) ───────────────────
    schedule.every().hour.do(heartbeat)

    log(
        "Scheduler started — "
        "Weekday kill 15:55 CT | reopen 17:00 CT | "
        "Weekly summary Fri 15:50 CT | "
        "Weekend kill Fri 16:00 CT | reopen Sun 17:00 CT | "
        "resets 00:00 CT | heartbeat hourly"
    )

    while True:
        schedule.run_pending()
        time.sleep(30)

# ── Entry point ────────────────────────────────────────────
if __name__ == "__main__":
    log("main.py starting...")
    t = threading.Thread(target=run_scheduler, daemon=True)
    t.start()

    # Import and run Flask server
    from server import app
    app.run(host="0.0.0.0", port=8080)
