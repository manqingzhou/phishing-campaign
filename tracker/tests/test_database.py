"""Unit tests for the SQLite DB layer (M1).

Runs against a temporary DB file so nothing touches real data.
"""

from __future__ import annotations

import sqlite3

import pytest

import database


@pytest.fixture
def db_path(tmp_path):
    """Provide a fresh temp DB file path and initialize the schema."""
    path = str(tmp_path / "test_clicks.db")
    database.init_db(path)
    return path


def test_init_db_creates_table_and_index(db_path):
    with database.get_connection(db_path) as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        indexes = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
    assert "clicks" in tables
    assert "idx_clicks_token" in indexes


def test_init_db_is_idempotent(db_path):
    # Calling init_db again must not raise.
    database.init_db(db_path)
    database.init_db(db_path)


def test_wal_mode_enabled(db_path):
    with database.get_connection(db_path) as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"


def test_insert_and_roundtrip(db_path):
    new_id = database.insert_click(
        email="user@test.ru",
        token="12345",
        ip_address="203.0.113.7",
        user_agent="pytest-agent",
        db_path=db_path,
    )
    assert isinstance(new_id, int) and new_id > 0

    rows = database.get_clicks(db_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["email"] == "user@test.ru"
    assert row["token"] == "12345"
    assert row["ip_address"] == "203.0.113.7"
    assert row["user_agent"] == "pytest-agent"
    assert row["clicked_at"]  # non-empty ISO timestamp


def test_clicked_at_defaults_to_utc_iso(db_path):
    database.insert_click("a@b.co", "t", "1.2.3.4", "ua", db_path=db_path)
    row = database.get_clicks(db_path)[0]
    # ISO-8601 UTC with +00:00 offset
    assert row["clicked_at"].endswith("+00:00")


def test_multiple_clicks_preserved_as_separate_rows(db_path):
    # Same token clicked twice -> two rows (full history).
    database.insert_click("a@b.co", "same-token", "1.1.1.1", "ua1", db_path=db_path)
    database.insert_click("a@b.co", "same-token", "2.2.2.2", "ua2", db_path=db_path)
    rows = database.get_clicks(db_path)
    assert len(rows) == 2
    assert {r["ip_address"] for r in rows} == {"1.1.1.1", "2.2.2.2"}


def test_get_clicks_newest_first(db_path):
    id1 = database.insert_click("a@b.co", "t1", "1.1.1.1", "ua", db_path=db_path)
    id2 = database.insert_click("a@b.co", "t2", "2.2.2.2", "ua", db_path=db_path)
    rows = database.get_clicks(db_path)
    assert [r["id"] for r in rows] == [id2, id1]


def test_partial_record_allows_null_email_token(db_path):
    # Missing email/token still records a row.
    new_id = database.insert_click(None, None, "9.9.9.9", "ua", db_path=db_path)
    assert new_id > 0
    row = database.get_clicks(db_path)[0]
    assert row["email"] is None
    assert row["token"] is None


def test_check_db_ok(db_path):
    assert database.check_db(db_path) is True
