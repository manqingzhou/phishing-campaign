"""End-to-end tests against the running Docker Compose stack (M6).

Prerequisites (not started by these tests):
    cp .env.example .env        # or provide ADMIN_API_KEY in the environment
    docker compose up --build -d

Configuration via environment (with sensible defaults):
    PUBLIC_URL     default http://localhost:8084
    ADMIN_URL      default http://127.0.0.1:8085
    ADMIN_API_KEY  default read from .env, else "supersecret-admin-key-123"

These tests are skipped automatically if the public endpoint is unreachable,
so the unit suites (test_database.py, test_app.py) can still run without Docker.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path

import httpx
import pytest

PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8084").rstrip("/")
ADMIN_URL = os.environ.get("ADMIN_URL", "http://127.0.0.1:8085").rstrip("/")


def _load_admin_key() -> str:
    key = os.environ.get("ADMIN_API_KEY")
    if key:
        return key
    # Fall back to reading the repo .env (two levels up from this file).
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("ADMIN_API_KEY="):
                return line.split("=", 1)[1].strip()
    return "supersecret-admin-key-123"


ADMIN_API_KEY = _load_admin_key()


def _stack_up() -> bool:
    try:
        httpx.get(f"{PUBLIC_URL}/", timeout=2.0)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _stack_up(),
    reason="compose stack not reachable on PUBLIC_URL; run `docker compose up -d`",
)


def _get_clicks() -> list[dict]:
    resp = httpx.get(
        f"{ADMIN_URL}/admin/clicks",
        headers={"X-Admin-Key": ADMIN_API_KEY},
        timeout=5.0,
    )
    resp.raise_for_status()
    return resp.json()


# 1. nginx up / page display
def test_public_root_serves_hello_world():
    resp = httpx.get(f"{PUBLIC_URL}/", timeout=5.0)
    assert resp.status_code == 200
    assert "educational phishing link" in resp.text


# 2. HTTP handling of a tracking link
def test_tracking_link_returns_200():
    resp = httpx.get(
        f"{PUBLIC_URL}/",
        params={"email": "user@test.ru", "token": "12345"},
        timeout=5.0,
    )
    assert resp.status_code == 200
    assert "educational phishing link" in resp.text


# 3. DB saving visible via admin
def test_click_recorded_and_visible_in_admin():
    token = f"e2e-{uuid.uuid4().hex[:8]}"
    httpx.get(
        f"{PUBLIC_URL}/",
        params={"email": "user@test.ru", "token": token},
        timeout=5.0,
    )
    time.sleep(0.2)
    rows = _get_clicks()
    match = [r for r in rows if r["token"] == token]
    assert match, f"token {token} not found in admin clicks"
    rec = match[0]
    assert rec["email"] == "user@test.ru"
    assert rec["ip_address"]  # non-empty
    # ISO-8601 UTC timestamp
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", rec["clicked_at"])


# 4. real IP through proxy (X-Forwarded-For first hop honored)
def test_real_ip_through_proxy():
    token = f"xff-{uuid.uuid4().hex[:8]}"
    httpx.get(
        f"{PUBLIC_URL}/",
        params={"email": "ip@test.ru", "token": token},
        headers={"X-Forwarded-For": "203.0.113.77"},
        timeout=5.0,
    )
    time.sleep(0.2)
    rec = [r for r in _get_clicks() if r["token"] == token][0]
    assert rec["ip_address"] == "203.0.113.77"


# 5a. admin locked down on the public port
def test_clicks_not_public():
    resp = httpx.get(f"{PUBLIC_URL}/clicks", timeout=5.0)
    assert resp.status_code == 404


def test_health_not_public():
    resp = httpx.get(f"{PUBLIC_URL}/health", timeout=5.0)
    assert resp.status_code == 404


# 5b. admin key enforcement on the admin port
def test_admin_requires_key():
    resp = httpx.get(f"{ADMIN_URL}/admin/clicks", timeout=5.0)
    assert resp.status_code == 401


def test_admin_wrong_key_rejected():
    resp = httpx.get(
        f"{ADMIN_URL}/admin/clicks",
        headers={"X-Admin-Key": "wrong"},
        timeout=5.0,
    )
    assert resp.status_code == 401


def test_admin_with_key_ok():
    resp = httpx.get(
        f"{ADMIN_URL}/admin/clicks",
        headers={"X-Admin-Key": ADMIN_API_KEY},
        timeout=5.0,
    )
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# 6. edge cases: missing params still served + recorded; repeat token adds a row
def test_missing_params_still_served():
    resp = httpx.get(f"{PUBLIC_URL}/", timeout=5.0)
    assert resp.status_code == 200
    assert "educational phishing link" in resp.text


def test_repeat_token_adds_new_event():
    token = f"rep-{uuid.uuid4().hex[:8]}"
    for _ in range(2):
        httpx.get(
            f"{PUBLIC_URL}/",
            params={"email": "rep@test.ru", "token": token},
            timeout=5.0,
        )
    time.sleep(0.2)
    matches = [r for r in _get_clicks() if r["token"] == token]
    assert len(matches) == 2
