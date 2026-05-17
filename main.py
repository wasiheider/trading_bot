import time
import schedule
import threading
import requests
from datetime import datetime
import pytz

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from risk import reset_tpt_daily, reset_ftmo_daily, tpt_state
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
    # Guards inside each function handle day filtering since
    # the `schedule` library runs .day jobs every day.
    schedule.every().day.at("15:53").do(tpt_close_warning)   # Mon–Thu warning (skips Sat/Sun)
    schedule.every().day.at("15:55").do(tpt_kill)             # Mon–Thu kill   (skips Sat/Sun)
    schedule.every().day.at("17:00").do(tpt_reopen)           # Tue–Fri reopen (skips Sat/Sun)

    # ── Weekend cycle ──────────────────────────────────────
    schedule.every().day.at("15:58").do(tpt_weekend_kill_warning)  # Fri only — 2-min weekend warning
    schedule.every().day.at("16:00").do(tpt_weekend_kill)          # Fri only — weekend shutdown
    schedule.every().day.at("17:00").do(tpt_weekend_reopen)        # Sun only — Asian session open

    # ── Daily resets (both accounts) — midnight CT ─────────
    schedule.every().day.at("00:00").do(midnight_reset)

    # ── Heartbeat (keeps Railway alive) ───────────────────
    schedule.every().hour.do(heartbeat)

    log(
        "Scheduler started — "
        "Weekday kill 15:55 CT | reopen 17:00 CT | "
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