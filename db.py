"""
db.py — PostgreSQL persistence layer (replaces trades.json + state.json)

Tables:
  trades    — every trade entry and outcome (was trades.json)
  bot_state — daily/weekly PNL, balance, counters (was state.json)
  signals   — every incoming webhook signal (pipeline tracing)
"""

import os
import json
import psycopg2
import psycopg2.extras
from datetime import datetime
import pytz

DATABASE_URL = os.getenv("DATABASE_URL")
CT = pytz.timezone("America/Chicago")


def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL environment variable not set")
    return psycopg2.connect(DATABASE_URL, sslmode="require")


# ── Schema ─────────────────────────────────────────────────

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id              SERIAL PRIMARY KEY,
            time            TEXT,
            date            TEXT,
            instrument      TEXT NOT NULL,
            direction       TEXT NOT NULL,
            timeframe       TEXT,
            price           REAL,
            sl              REAL,
            tp1             REAL,
            tp2             REAL,
            bos_level       REAL,
            range_high      REAL,
            range_low       REAL,
            lot_size        REAL,
            setup           TEXT,
            result          TEXT DEFAULT 'OPEN',
            pnl             REAL DEFAULT 0,
            oanda_trade_id  TEXT,
            created_at      TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS bot_state (
            id              INTEGER PRIMARY KEY DEFAULT 1,
            account_balance REAL    DEFAULT 100000,
            daily_pnl       REAL    DEFAULT 0,
            weekly_pnl      REAL    DEFAULT 0,
            daily_signals   INTEGER DEFAULT 0,
            daily_wins      INTEGER DEFAULT 0,
            daily_losses    INTEGER DEFAULT 0,
            total_wins      INTEGER DEFAULT 0,
            total_losses    INTEGER DEFAULT 0,
            weekly_sl_hits  INTEGER DEFAULT 0,
            sl_hits_today   TEXT    DEFAULT '{}',
            last_signal     TEXT    DEFAULT 'null',
            state_date      TEXT,
            state_week      TEXT,
            updated_at      TIMESTAMP DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS signals (
            id          SERIAL PRIMARY KEY,
            ts          TEXT NOT NULL,
            symbol      TEXT NOT NULL,
            direction   TEXT NOT NULL,
            setup       TEXT,
            timeframe   TEXT,
            range_high  REAL,
            range_low   REAL,
            entry_price REAL,
            stop_loss   REAL,
            tp1         REAL,
            tp2         REAL,
            rr_to_tp1   REAL,
            bos_level   REAL,
            created_at  TIMESTAMP DEFAULT NOW()
        );
    """)
    # Ensure bot_state always has exactly one row
    cur.execute("INSERT INTO bot_state (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
    conn.commit()
    cur.close()
    conn.close()
    print("[db] Database initialized", flush=True)


# ── Trade CRUD ─────────────────────────────────────────────

def append_trade(trade: dict):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO trades (
            time, date, instrument, direction, timeframe,
            price, sl, tp1, tp2, bos_level, range_high, range_low,
            lot_size, setup, result, pnl, oanda_trade_id
        ) VALUES (
            %(time)s, %(date)s, %(instrument)s, %(direction)s, %(timeframe)s,
            %(price)s, %(sl)s, %(tp1)s, %(tp2)s, %(bos_level)s, %(range_high)s, %(range_low)s,
            %(lot_size)s, %(setup)s, %(result)s, %(pnl)s, %(oanda_trade_id)s
        )
    """, {
        "time":           trade.get("time"),
        "date":           trade.get("date"),
        "instrument":     trade.get("instrument"),
        "direction":      trade.get("direction"),
        "timeframe":      trade.get("timeframe"),
        "price":          trade.get("price"),
        "sl":             trade.get("sl"),
        "tp1":            trade.get("tp1"),
        "tp2":            trade.get("tp2"),
        "bos_level":      trade.get("bos_level"),
        "range_high":     trade.get("range_high"),
        "range_low":      trade.get("range_low"),
        "lot_size":       trade.get("lot_size"),
        "setup":          trade.get("setup"),
        "result":         trade.get("result", "OPEN"),
        "pnl":            trade.get("pnl", 0),
        "oanda_trade_id": trade.get("oanda_trade_id"),
    })
    conn.commit()
    cur.close()
    conn.close()


def update_trade(instrument: str, direction: str, result: str, pnl: float):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE trades SET result = %s, pnl = %s
        WHERE id = (
            SELECT id FROM trades
            WHERE UPPER(instrument) = UPPER(%s)
              AND UPPER(direction)  = UPPER(%s)
              AND result = 'OPEN'
            ORDER BY id DESC LIMIT 1
        )
    """, (result, pnl or 0, instrument, direction))
    conn.commit()
    cur.close()
    conn.close()


def get_oanda_trade_id(instrument: str, direction: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT oanda_trade_id FROM trades
        WHERE UPPER(instrument) = UPPER(%s)
          AND UPPER(direction)  = UPPER(%s)
          AND result = 'OPEN'
        ORDER BY id DESC LIMIT 1
    """, (instrument, direction))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row[0] if row else None


def has_open_trade(instrument: str, direction: str = None) -> bool:
    conn = get_conn()
    cur = conn.cursor()
    if direction:
        cur.execute("""
            SELECT 1 FROM trades
            WHERE UPPER(instrument) = UPPER(%s)
              AND UPPER(direction)  = UPPER(%s)
              AND result = 'OPEN'
            LIMIT 1
        """, (instrument, direction))
    else:
        cur.execute("""
            SELECT 1 FROM trades
            WHERE UPPER(instrument) = UPPER(%s)
              AND result = 'OPEN'
            LIMIT 1
        """, (instrument,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return row is not None


def load_trades() -> list:
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM trades ORDER BY id")
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def load_recent_trades(limit: int = 10) -> list:
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("""
        SELECT * FROM trades
        WHERE result != 'OPEN'
        ORDER BY id DESC LIMIT %s
    """, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def open_trade_count() -> int:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM trades WHERE result = 'OPEN'")
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count


def close_stale_non_forex_opens(forex_instruments: set):
    """
    Mark any OPEN trade that has no oanda_trade_id AND is not a forex instrument as UNKNOWN.
    These are log-only trades (US100, XAUUSD, etc.) whose lifecycle events were never received.
    Safe to call on every startup — only touches rows that are genuinely unresolvable.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE trades SET result = 'UNKNOWN', pnl = 0
        WHERE result = 'OPEN'
          AND (oanda_trade_id IS NULL OR oanda_trade_id = '')
          AND UPPER(instrument) != ALL(%s)
    """, (list(forex_instruments),))
    affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    if affected:
        print(f"[db] Closed {affected} stale non-forex OPEN trade(s) → UNKNOWN", flush=True)
    return affected


def clear_trades():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM trades")
    conn.commit()
    cur.close()
    conn.close()


# ── State CRUD ─────────────────────────────────────────────

def load_state() -> dict:
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM bot_state WHERE id = 1")
    row = cur.fetchone()
    cur.close()
    conn.close()
    if not row:
        return {}
    d = dict(row)
    d["sl_hits_today"] = json.loads(d.get("sl_hits_today") or "{}")
    d["last_signal"]   = json.loads(d.get("last_signal")   or "null")
    d["_date"] = d.pop("state_date", None)
    d["_week"] = d.pop("state_week", None)
    return d


def save_state(state: dict):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE bot_state SET
            account_balance = %s,
            daily_pnl       = %s,
            weekly_pnl      = %s,
            daily_signals   = %s,
            daily_wins      = %s,
            daily_losses    = %s,
            total_wins      = %s,
            total_losses    = %s,
            weekly_sl_hits  = %s,
            sl_hits_today   = %s,
            last_signal     = %s,
            state_date      = %s,
            state_week      = %s,
            updated_at      = NOW()
        WHERE id = 1
    """, (
        state.get("account_balance", 100000),
        state.get("daily_pnl",       0),
        state.get("weekly_pnl",      0),
        state.get("daily_signals",   0),
        state.get("daily_wins",      0),
        state.get("daily_losses",    0),
        state.get("total_wins",      0),
        state.get("total_losses",    0),
        state.get("weekly_sl_hits",  0),
        json.dumps(state.get("sl_hits_today", {})),
        json.dumps(state.get("last_signal")),
        state.get("_date"),
        state.get("_week"),
    ))
    conn.commit()
    cur.close()
    conn.close()


# ── Signal logging ─────────────────────────────────────────

def log_signal(data: dict):
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO signals (
                ts, symbol, direction, setup, timeframe,
                range_high, range_low, entry_price, stop_loss,
                tp1, tp2, rr_to_tp1, bos_level
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            datetime.now(CT).strftime("%Y-%m-%d %H:%M:%S"),
            data.get("symbol", data.get("instrument", "")),
            data.get("direction", ""),
            data.get("setup", ""),
            data.get("timeframe", ""),
            data.get("range_high"),
            data.get("range_low"),
            data.get("entry_price", data.get("price")),
            data.get("stop_loss",   data.get("sl")),
            data.get("tp1"),
            data.get("tp2"),
            data.get("rr_to_tp1"),
            data.get("bos_level"),
        ))
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        print(f"[db] log_signal failed: {e}", flush=True)
