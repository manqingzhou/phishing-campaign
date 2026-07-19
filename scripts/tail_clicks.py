#!/usr/bin/env python3
"""Tail clicks.db like a log file: print only NEW rows as they appear.

Stdlib only. Reads the same SQLite file the tracker writes to
(`tracker/data/clicks.db` by default, override with TAIL_DB env or --db).

Usage:
    # 1. Bring the stack up
    docker compose up -d

    # 2. Start the tail in a terminal — prints existing rows once, then
    #    waits and prints ONLY new rows as clicks happen.
    python3 scripts/tail_clicks.py

    # 3. In another terminal (or browser), click any tracking link:
    curl "http://localhost:8084/?email=user@test.ru&token=demo"
    # → the new row streams into the tail terminal immediately.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "tracker" / "data" / "clicks.db"
COLUMNS = "id, email, token, ip_address, clicked_at"
COL_WIDTHS = (4, 28, 26, 16, 22)


def fmt_row(values: tuple) -> str:
    cells = []
    for i, (val, width) in enumerate(zip(values, COL_WIDTHS)):
        if val is None:
            s = ""
        else:
            s = str(val)
        if i == 0:
            s = f"#{s}"  # id prefix
        cells.append(s.ljust(width))
    return "  ".join(cells)


def header() -> str:
    return fmt_row(tuple("ID" if i == 0 else c.upper() for i, c in enumerate(COLUMNS.split(", "))))


def poll(db_path: Path, interval: float) -> None:
    if not db_path.exists():
        print(f"waiting for {db_path} to be created...", file=sys.stderr)
        while not db_path.exists():
            time.sleep(0.5)

    last_id = -1
    while True:
        try:
            conn = sqlite3.connect(str(db_path), timeout=2.0)
            try:
                # If the schema isn't there yet, the tracker hasn't run.
                conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='clicks'"
                ).fetchone()
                rows = list(
                    conn.execute(
                        f"SELECT {COLUMNS} FROM clicks WHERE id > ? ORDER BY id ASC",
                        (last_id,),
                    )
                )
            finally:
                conn.close()
        except sqlite3.OperationalError:
            # DB locked or not ready — brief retry.
            time.sleep(interval)
            continue

        if rows:
            if last_id == -1:
                # First connect — print the header and all existing rows
                # (so you can see history + new ones without re-polling).
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                print(f"[{ts}] {header()}", flush=True)
            for row in rows:
                ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
                print(f"[{ts}] {fmt_row(row)}", flush=True)
                last_id = max(last_id, row[0])

        time.sleep(interval)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--db",
        default=os.environ.get("TAIL_DB", str(DEFAULT_DB)),
        help=f"Path to clicks.db (default: {DEFAULT_DB})",
    )
    p.add_argument(
        "--interval",
        type=float,
        default=0.5,
        help="Poll interval in seconds (default: 0.5)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    print(f"tailing {args.db} every {args.interval}s — Ctrl-C to stop", file=sys.stderr)
    try:
        poll(Path(args.db), args.interval)
    except KeyboardInterrupt:
        print("", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
