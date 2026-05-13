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

# ── TPT Kill Switch (3:55 PM CT) ───────────────────────────
def tpt_kill():
    tpt_state["killed"] = True
    log("TPT kill switch fired — no new signals after 3:55 PM CT")
    send_telegram("🛑 *TPT Kill Switch*\nNo new TPT signals until midnight reset.")

# ── TPT Hard Close Warning (3:53 PM CT) ───────────────────
def tpt_close_warning():
    log("TPT 2-min warning — close any open positions now")
    send_telegram("⚠️ *TPT 2-Min Warning*\nClose all open TPT positions — hard cutoff at 3:55 PM CT (5 PM EST).")

# ── Midnight Reset ─────────────────────────────────────────
def midnight_reset():
    reset_tpt_daily()
    reset_ftmo_daily()
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
    # TPT
    schedule.every().day.at("15:53").do(tpt_close_warning)
    schedule.every().day.at("15:55").do(tpt_kill)

    # Daily resets (both accounts)
    schedule.every().day.at("00:00").do(midnight_reset)

    # Heartbeat every hour
    schedule.every().hour.do(heartbeat)

    log("Scheduler started — TPT kill 15:55 CT | resets 00:00 CT | heartbeat hourly")

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