# =============================================================================
# LOGGER.PY — SQLITE TRADE LOGGER (Phase 6)
# =============================================================================
# Logs every stage of the signal pipeline to a local SQLite database.
# Replaces the in-memory daily P&L tracker in risk.py with persistent storage.
#
# Tables:
#   signals       — every incoming webhook signal
#   decisions     — Claude approve/reject with reasoning
#   risk_results  — risk engine pass/block with sizing
#   trade_events  — ready-to-execute records (entry, SL, TP1, TP2, sizing)
#   alerts        — Telegram alert success/failure log
#
# Provides:
#   get_recent_logs(n)  — returns last n rows across all tables for /logs endpoint
# =============================================================================

import sqlite3
import logging
import os
from datetime import datetime
from typing import Optional
import pytz

import config

log = logging.getLogger(__name__)

CT = pytz.timezone("America/Chicago")

# -----------------------------------------------------------------------------
# DATABASE PATH
# -----------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "trades.db")

# -----------------------------------------------------------------------------
# INIT — CREATE TABLES IF NOT EXIST
# -----------------------------------------------------------------------------

def init_db():
    """Create all tables if they don't exist. Safe to call on every startup."""
    os.makedirs("data", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS signals (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            account     TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            direction   TEXT NOT NULL,
            timeframe   TEXT NOT NULL,
            session     TEXT NOT NULL,
            range_high  REAL,
            range_mid   REAL,
            range_low   REAL,
            entry_price REAL,
            stop_loss   REAL,
            tp1         REAL,
            tp2         REAL,
            rr_to_tp1   REAL,
            bar_time    TEXT,
            test_mode   INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS decisions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            account     TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            direction   TEXT NOT NULL,
            approved    INTEGER NOT NULL,
            confidence  INTEGER,
            action      TEXT,
            reasoning   TEXT,
            warnings    TEXT,
            risk_notes  TEXT
        );

        CREATE TABLE IF NOT EXISTS risk_results (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            account     TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            direction   TEXT NOT NULL,
            passed      INTEGER NOT NULL,
            reason      TEXT,
            contracts   INTEGER,
            lots        REAL,
            dollar_risk REAL,
            session     TEXT
        );

        CREATE TABLE IF NOT EXISTS trade_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            account     TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            direction   TEXT NOT NULL,
            entry_price REAL,
            stop_loss   REAL,
            tp1         REAL,
            tp2         REAL,
            rr_to_tp1   REAL,
            contracts   INTEGER,
            lots        REAL,
            dollar_risk REAL,
            action      TEXT,
            session     TEXT,
            status      TEXT DEFAULT 'PENDING'
        );

        CREATE TABLE IF NOT EXISTS alerts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ts          TEXT NOT NULL,
            account     TEXT NOT NULL,
            alert_type  TEXT NOT NULL,
            success     INTEGER NOT NULL,
            message     TEXT
        );
    """)

    conn.commit()
    conn.close()
    log.info(f"[LOGGER] Database initialized at {DB_PATH}")


def now_ts() -> str:
    """Return current CT timestamp as ISO string."""
    return datetime.now(CT).strftime("%Y-%m-%d %H:%M:%S")


# -----------------------------------------------------------------------------
# LOG — SIGNAL RECEIVED
# -----------------------------------------------------------------------------

def log_signal(signal) -> int:
    """
    Log an incoming signal. Returns the row ID for reference.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO signals (
                ts, account, symbol, direction, timeframe, session,
                range_high, range_mid, range_low,
                entry_price, stop_loss, tp1, tp2, rr_to_tp1,
                bar_time, test_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_ts(),
            signal.account, signal.symbol, signal.direction,
            signal.timeframe, signal.session,
            signal.range_high, signal.range_mid, signal.range_low,
            signal.entry_price, signal.stop_loss,
            signal.tp1, signal.tp2, signal.rr_to_tp1,
            signal.bar_time,
            1 if getattr(signal, "test_mode", False) else 0,
        ))
        row_id = c.lastrowid
        conn.commit()
        conn.close()
        log.info(f"[LOGGER] Signal logged — ID {row_id} | {signal.account} {signal.symbol} {signal.direction}")
        return row_id
    except Exception as e:
        log.warning(f"[LOGGER] Failed to log signal — {e}")
        return -1


# -----------------------------------------------------------------------------
# LOG — CLAUDE DECISION
# -----------------------------------------------------------------------------

def log_decision(signal, decision: dict) -> None:
    """Log Claude's approve/reject decision."""
    try:
        warnings = "; ".join(decision.get("warnings", []))
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO decisions (
                ts, account, symbol, direction,
                approved, confidence, action, reasoning, warnings, risk_notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_ts(),
            signal.account, signal.symbol, signal.direction,
            1 if decision.get("approve") else 0,
            decision.get("confidence"),
            decision.get("action"),
            decision.get("reasoning"),
            warnings,
            decision.get("risk_notes"),
        ))
        conn.commit()
        conn.close()
        status = "APPROVED" if decision.get("approve") else "REJECTED"
        log.info(f"[LOGGER] Decision logged — {signal.account} {signal.symbol} {status}")
    except Exception as e:
        log.warning(f"[LOGGER] Failed to log decision — {e}")


# -----------------------------------------------------------------------------
# LOG — RISK ENGINE RESULT
# -----------------------------------------------------------------------------

def log_risk_result(signal, passed: bool, reason: str, sizing: dict) -> None:
    """Log the risk engine pass/block result with sizing info."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO risk_results (
                ts, account, symbol, direction,
                passed, reason, contracts, lots, dollar_risk, session
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_ts(),
            signal.account, signal.symbol, signal.direction,
            1 if passed else 0,
            reason,
            sizing.get("contracts"),
            sizing.get("lots"),
            sizing.get("dollar_risk"),
            sizing.get("session"),
        ))
        conn.commit()
        conn.close()
        status = "PASSED" if passed else "BLOCKED"
        log.info(f"[LOGGER] Risk result logged — {signal.account} {signal.symbol} {status}")
    except Exception as e:
        log.warning(f"[LOGGER] Failed to log risk result — {e}")


# -----------------------------------------------------------------------------
# LOG — TRADE READY EVENT
# -----------------------------------------------------------------------------

def log_trade_event(signal, decision: dict, sizing: dict) -> int:
    """
    Log a trade-ready event — signal cleared all checks, ready for execution.
    Returns the row ID (used later to update status when trade closes).
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO trade_events (
                ts, account, symbol, direction,
                entry_price, stop_loss, tp1, tp2, rr_to_tp1,
                contracts, lots, dollar_risk, action, session, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_ts(),
            signal.account, signal.symbol, signal.direction,
            signal.entry_price, signal.stop_loss,
            signal.tp1, signal.tp2, signal.rr_to_tp1,
            sizing.get("contracts"),
            sizing.get("lots"),
            sizing.get("dollar_risk"),
            decision.get("action"),
            sizing.get("session", signal.session),
            "PENDING",
        ))
        row_id = c.lastrowid
        conn.commit()
        conn.close()
        log.info(f"[LOGGER] Trade event logged — ID {row_id} | {signal.account} {signal.symbol} PENDING")
        return row_id
    except Exception as e:
        log.warning(f"[LOGGER] Failed to log trade event — {e}")
        return -1


def update_trade_status(trade_id: int, status: str) -> None:
    """
    Update a trade event status.
    Status values: PENDING → TP1_HIT | TP2_HIT | SL_HIT | CANCELLED
    Called in Phase 7 when Pine Script reports trade outcome.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("UPDATE trade_events SET status = ? WHERE id = ?", (status, trade_id))
        conn.commit()
        conn.close()
        log.info(f"[LOGGER] Trade {trade_id} status updated → {status}")
    except Exception as e:
        log.warning(f"[LOGGER] Failed to update trade status — {e}")


# -----------------------------------------------------------------------------
# LOG — TELEGRAM ALERT
# -----------------------------------------------------------------------------

def log_alert(account: str, alert_type: str, success: bool, message: str = "") -> None:
    """Log a Telegram alert send attempt."""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            INSERT INTO alerts (ts, account, alert_type, success, message)
            VALUES (?, ?, ?, ?, ?)
        """, (
            now_ts(),
            account,
            alert_type,
            1 if success else 0,
            message[:500],  # truncate long messages
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        log.warning(f"[LOGGER] Failed to log alert — {e}")


# -----------------------------------------------------------------------------
# QUERY — RECENT LOGS FOR /logs ENDPOINT
# -----------------------------------------------------------------------------

def get_recent_logs(limit: int = 50) -> dict:
    """
    Return the most recent rows from all tables.
    Used by the /logs endpoint in server.py.
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        def fetch(table, n):
            c.execute(f"SELECT * FROM {table} ORDER BY id DESC LIMIT ?", (n,))
            return [dict(row) for row in c.fetchall()]

        result = {
            "signals":      fetch("signals", limit),
            "decisions":    fetch("decisions", limit),
            "risk_results": fetch("risk_results", limit),
            "trade_events": fetch("trade_events", limit),
            "alerts":       fetch("alerts", limit),
            "generated_at": now_ts(),
        }

        conn.close()
        return result

    except Exception as e:
        log.warning(f"[LOGGER] Failed to fetch logs — {e}")
        return {"error": str(e)}


# -----------------------------------------------------------------------------
# DAILY P&L FROM DB (replaces in-memory tracker in risk.py — Phase 6+)
# -----------------------------------------------------------------------------

def get_daily_pnl(account: str) -> float:
    """
    Sum P&L for today from closed trade_events.
    Currently returns 0.0 until trade outcomes are tracked in Phase 7.
    """
    try:
        today = datetime.now(CT).strftime("%Y-%m-%d")
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT COUNT(*) FROM trade_events
            WHERE account = ? AND ts LIKE ? AND status != 'PENDING'
        """, (account, f"{today}%"))
        count = c.fetchone()[0]
        conn.close()
        return 0.0  # Will be real P&L in Phase 7 when outcomes are tracked
    except Exception as e:
        log.warning(f"[LOGGER] Failed to get daily P&L — {e}")
        return 0.0
