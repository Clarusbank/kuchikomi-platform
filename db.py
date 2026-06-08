"""
db.py — ウォレット情報の永続化
追跡ウォレットの履歴・スコアを記録する
"""

import sqlite3
import json
from datetime import datetime
from config import DB_PATH


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """テーブル初期化（初回のみ）"""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS wallets (
                address         TEXT PRIMARY KEY,
                label           TEXT DEFAULT '',
                first_seen      TEXT,
                last_seen       TEXT,
                trade_count     INTEGER DEFAULT 0,
                win_count       INTEGER DEFAULT 0,
                total_pnl_usd   REAL DEFAULT 0.0,
                avg_leverage    REAL DEFAULT 0.0,
                insider_score   REAL DEFAULT 0.0,
                is_watchlist    INTEGER DEFAULT 0,
                notes           TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS trade_events (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address  TEXT,
                detected_at     TEXT,
                coin            TEXT,
                side            TEXT,
                size_usd        REAL,
                leverage        REAL,
                insider_score   REAL,
                score_breakdown TEXT,
                action_taken    TEXT DEFAULT 'none'
            );

            CREATE TABLE IF NOT EXISTS my_trades (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                opened_at       TEXT,
                closed_at       TEXT,
                coin            TEXT,
                side            TEXT,
                size_usd        REAL,
                entry_price     REAL,
                exit_price      REAL,
                pnl_usd         REAL DEFAULT 0.0,
                trigger_wallet  TEXT,
                insider_score   REAL,
                status          TEXT DEFAULT 'open'
            );
        """)
    print("[DB] 初期化完了")


def upsert_wallet(address: str, data: dict):
    """ウォレット情報を登録または更新"""
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT address FROM wallets WHERE address = ?", (address,)
        ).fetchone()

        if existing:
            sets = ", ".join(f"{k} = ?" for k in data)
            vals = list(data.values()) + [address]
            conn.execute(f"UPDATE wallets SET {sets}, last_seen = ? WHERE address = ?",
                         vals + [now, address])
        else:
            data["address"] = address
            data["first_seen"] = now
            data["last_seen"] = now
            cols = ", ".join(data.keys())
            placeholders = ", ".join("?" * len(data))
            conn.execute(f"INSERT INTO wallets ({cols}) VALUES ({placeholders})",
                         list(data.values()))


def get_wallet(address: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM wallets WHERE address = ?", (address,)
        ).fetchone()
        return dict(row) if row else None


def get_watchlist() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM wallets WHERE is_watchlist = 1 ORDER BY insider_score DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def log_trade_event(wallet: str, coin: str, side: str, size_usd: float,
                    leverage: float, score: float, breakdown: dict, action: str = "none"):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO trade_events
            (wallet_address, detected_at, coin, side, size_usd, leverage,
             insider_score, score_breakdown, action_taken)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (wallet, datetime.utcnow().isoformat(), coin, side, size_usd,
              leverage, score, json.dumps(breakdown), action))


def log_my_trade(coin: str, side: str, size_usd: float, entry_price: float,
                 trigger_wallet: str, insider_score: float) -> int:
    with get_conn() as conn:
        cur = conn.execute("""
            INSERT INTO my_trades
            (opened_at, coin, side, size_usd, entry_price, trigger_wallet, insider_score)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (datetime.utcnow().isoformat(), coin, side, size_usd,
              entry_price, trigger_wallet, insider_score))
        return cur.lastrowid


def close_my_trade(trade_id: int, exit_price: float, pnl_usd: float):
    with get_conn() as conn:
        conn.execute("""
            UPDATE my_trades
            SET closed_at = ?, exit_price = ?, pnl_usd = ?, status = 'closed'
            WHERE id = ?
        """, (datetime.utcnow().isoformat(), exit_price, pnl_usd, trade_id))


def get_open_my_trades() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM my_trades WHERE status = 'open'"
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_events(hours: int = 24) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM trade_events
            WHERE detected_at >= datetime('now', ?)
            ORDER BY insider_score DESC
        """, (f"-{hours} hours",)).fetchall()
        return [dict(r) for r in rows]
