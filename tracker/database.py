"""SQLite persistence for click events.

Design notes:
- WAL journal mode for better read/write concurrency under the async app.
- Short-lived connections via a context manager: each call opens, uses, and
  closes its own connection. This keeps things async-safe (no shared connection
  across coroutines) at the cost of a little per-call overhead, which is fine
  for a click tracker.
- Every click is recorded as its own row (full history, incl. repeat clicks).
  `token` is indexed and treated as a unique per-recipient id via the link
  generator. Flip to `UNIQUE(token)` + upsert if you want one row per token.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator, Optional

# Default DB location; overridable via env for containers/tests.
DB_PATH = os.environ.get("DB_PATH", "clicks.db")


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (seconds precision)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def get_connection(db_path: Optional[str] = None) -> Iterator[sqlite3.Connection]:
    """Yield a short-lived SQLite connection with sane pragmas.

    Row factory is set to sqlite3.Row so callers get dict-like rows.
    """
    path = db_path or DB_PATH
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: Optional[str] = None) -> None:
    """Create the clicks table and token index if they do not exist."""
    with get_connection(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clicks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email       TEXT,
                token       TEXT,
                ip_address  TEXT,
                user_agent  TEXT,
                clicked_at  TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_clicks_token ON clicks (token)"
        )


def insert_click(
    email: Optional[str],
    token: Optional[str],
    ip_address: Optional[str],
    user_agent: Optional[str],
    clicked_at: Optional[str] = None,
    db_path: Optional[str] = None,
) -> int:
    """Insert one click event and return its new row id."""
    ts = clicked_at or utc_now_iso()
    with get_connection(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO clicks (email, token, ip_address, user_agent, clicked_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (email, token, ip_address, user_agent, ts),
        )
        return int(cur.lastrowid)


def get_clicks(db_path: Optional[str] = None) -> list[dict]:
    """Return all click events, newest first, as a list of dicts."""
    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, email, token, ip_address, user_agent, clicked_at
            FROM clicks
            ORDER BY id DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def check_db(db_path: Optional[str] = None) -> bool:
    """Lightweight connectivity check used by the health endpoint."""
    with get_connection(db_path) as conn:
        conn.execute("SELECT 1")
    return True
