"""FastAPI app tests (M2) using TestClient, in-process, temp DB.

Each test points the app at a temp DB via the DB_PATH env / module attribute
before importing/using the app.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient

ADMIN_KEY = "test-admin-key"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Build a TestClient wired to a temp DB and a known admin key."""
    db_file = str(tmp_path / "app_clicks.db")
    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("ADMIN_API_KEY", ADMIN_KEY)

    # Reload modules so they pick up the patched env at import time.
    import database
    import main

    importlib.reload(database)
    importlib.reload(main)

    with TestClient(main.app) as c:
        yield c, main


def test_root_serves_hello_world(client):
    c, _ = client
    resp = c.get("/")
    assert resp.status_code == 200
    assert "educational phishing link" in resp.text
    assert "text/html" in resp.headers["content-type"]


def test_root_records_click(client):
    c, main = client
    resp = c.get("/?email=user@test.ru&token=12345")
    assert resp.status_code == 200
    rows = main.database.get_clicks()
    assert len(rows) == 1
    assert rows[0]["email"] == "user@test.ru"
    assert rows[0]["token"] == "12345"
    assert rows[0]["clicked_at"]


def test_ip_taken_from_x_forwarded_for(client):
    c, main = client
    c.get(
        "/?email=a@b.co&token=t1",
        headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"},
    )
    rows = main.database.get_clicks()
    assert rows[0]["ip_address"] == "203.0.113.9"


def test_ip_falls_back_to_x_real_ip(client):
    c, main = client
    c.get("/?email=a@b.co&token=t1", headers={"X-Real-IP": "198.51.100.5"})
    rows = main.database.get_clicks()
    assert rows[0]["ip_address"] == "198.51.100.5"


def test_clicks_requires_admin_key(client):
    c, _ = client
    resp = c.get("/clicks")
    assert resp.status_code == 401


def test_clicks_wrong_key_rejected(client):
    c, _ = client
    resp = c.get("/clicks", headers={"X-Admin-Key": "wrong"})
    assert resp.status_code == 401


def test_clicks_with_key_returns_records(client):
    c, _ = client
    c.get("/?email=user@test.ru&token=12345")
    resp = c.get("/clicks", headers={"X-Admin-Key": ADMIN_KEY})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["email"] == "user@test.ru"
    assert data[0]["token"] == "12345"


def test_health_ok(client):
    c, _ = client
    resp = c.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_missing_email_and_token_still_serves_and_records(client):
    c, main = client
    resp = c.get("/")
    assert resp.status_code == 200
    assert "educational phishing link" in resp.text
    rows = main.database.get_clicks()
    assert len(rows) == 1
    assert rows[0]["email"] is None
    assert rows[0]["token"] is None


def test_invalid_email_still_recorded(client):
    c, main = client
    resp = c.get("/?email=not-an-email&token=t9")
    assert resp.status_code == 200
    rows = main.database.get_clicks()
    assert rows[0]["email"] == "not-an-email"
    assert rows[0]["token"] == "t9"


def test_repeat_token_adds_new_row(client):
    c, main = client
    c.get("/?email=a@b.co&token=same")
    c.get("/?email=a@b.co&token=same")
    rows = main.database.get_clicks()
    assert len(rows) == 2
