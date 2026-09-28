"""v7 strategy scorecard — per-setup performance since v7 went live.

Shared by GET /scorecard (dashboard card) and the Friday Telegram scorecard
in main.py, so both always show the same numbers.

v7 trades = setup range_rev or mid_cont, or box_break on the 4H timeframe
("240"), dated on/after V7_START. v5 box_break trades were 15M, so the
timeframe check keeps them out even though the setup name is shared.
"""
from datetime import datetime, timedelta

import pytz

from config import PAPER_ACCOUNT_SIZE, RISK_PER_TRADE, MID_CONT_RISK_PER_TRADE

CT = pytz.timezone("America/Chicago")
V7_START = "2026-09-28"

SETUPS = [
    ("range_rev", "F — 4H Reversal"),
    ("box_break", "E — Box Break (4H)"),
    ("mid_cont",  "G — Mid Continuation"),
]


def _is_v7(t) -> bool:
    if str(t.get("date") or "")[:10] < V7_START:
        return False
    setup = (t.get("setup") or "").lower()
    if setup in ("range_rev", "mid_cont"):
        return True
    return setup == "box_break" and str(t.get("timeframe")) == "240"


def _planned_risk(setup: str) -> float:
    pct = MID_CONT_RISK_PER_TRADE if setup == "mid_cont" else RISK_PER_TRADE
    return PAPER_ACCOUNT_SIZE * pct


def _stats(trades: list) -> dict:
    closed = [t for t in trades if (t.get("result") or "") not in ("", "OPEN", "UNKNOWN", "FAILED")]
    wins   = [t for t in closed if "TP" in (t.get("result") or "")]
    pnl    = sum(t.get("pnl") or 0 for t in closed)
    r_vals = [(t.get("pnl") or 0) / _planned_risk((t.get("setup") or "").lower()) for t in closed]
    gross_win  = sum(t.get("pnl") or 0 for t in closed if (t.get("pnl") or 0) > 0)
    gross_loss = -sum(t.get("pnl") or 0 for t in closed if (t.get("pnl") or 0) < 0)
    return {
        "trades":        len(closed),
        "wins":          len(wins),
        "losses":        len(closed) - len(wins),
        "win_rate":      round(len(wins) / len(closed) * 100) if closed else 0,
        "pnl":           round(pnl, 2),
        "avg_r":         round(sum(r_vals) / len(r_vals), 2) if r_vals else 0.0,
        "total_r":       round(sum(r_vals), 2),
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "open":          sum(1 for t in trades if t.get("result") == "OPEN"),
    }


def compute(all_trades: list, now: datetime = None) -> dict:
    now = now or datetime.now(CT)
    v7 = [t for t in all_trades if _is_v7(t)]
    week_start = (now - timedelta(days=now.weekday())).strftime("%Y-%m-%d")
    this_week = [t for t in v7 if str(t.get("date") or "")[:10] >= week_start]

    def by_setup(trades):
        return [
            {"setup": key, "label": label, **_stats([t for t in trades if (t.get("setup") or "").lower() == key])}
            for key, label in SETUPS
        ]

    return {
        "since":      V7_START,
        "days_live":  (now.date() - datetime.strptime(V7_START, "%Y-%m-%d").date()).days,
        "week_start": week_start,
        "total":      _stats(v7),
        "setups":     by_setup(v7),
        "week":       {"total": _stats(this_week), "setups": by_setup(this_week)},
    }


def telegram_message(sc: dict) -> str:
    def line(s):
        if not s["trades"]:
            return f"{s['label']}: no closed trades" + (f" ({s['open']} open)" if s["open"] else "")
        pf = f" · PF {s['profit_factor']}" if s["profit_factor"] is not None else ""
        return (f"{s['label']}: <code>{s['trades']}</code> ({s['wins']}W/{s['losses']}L) "
                f"<code>{s['win_rate']}%</code> · <code>${s['pnl']:+,.2f}</code> · "
                f"avg <code>{s['avg_r']:+.2f}R</code>{pf}")

    tot, wk = sc["total"], sc["week"]["total"]
    tot_pf = f" · PF {tot['profit_factor']}" if tot["profit_factor"] is not None else ""
    return (
        f"🩷👑🤖👑🩷\n"
        f"📈 <b>v7 Scorecard — day {sc['days_live']} (since {sc['since']})</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"<b>This week</b>: <code>{wk['trades']}</code> trades ({wk['wins']}W/{wk['losses']}L) · "
        f"<code>${wk['pnl']:+,.2f}</code> · <code>{wk['total_r']:+.2f}R</code>\n\n"
        f"<b>Since v7 went live</b>\n"
        + "\n".join(line(s) for s in sc["setups"]) +
        f"\n\n<b>Total</b>: <code>{tot['trades']}</code> ({tot['wins']}W/{tot['losses']}L) "
        f"<code>{tot['win_rate']}%</code> · <code>${tot['pnl']:+,.2f}</code> · "
        f"<code>{tot['total_r']:+.2f}R</code>{tot_pf}"
        + (f" · {tot['open']} open" if tot["open"] else "")
    )
