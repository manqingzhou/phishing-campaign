"""FastAPI click tracker.

Routes:
- GET /        Records a click (email, token, client IP, UA, timestamp) and
               always serves the educational phishing landing page.
- GET /clicks  Returns all recorded clicks as JSON. Requires the
               `X-Admin-Key` header to match ADMIN_API_KEY (401 otherwise).
               Publicly unreachable — only proxied via the loopback admin port.
- GET /health  DB connectivity check. Internal healthcheck only.

Client IP resolution order: first hop of X-Forwarded-For -> X-Real-IP ->
the socket peer. nginx sets these headers so we capture the true client IP.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

import database
from models import ClickQuery, ClickRecord

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger("tracker")

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY", "")


@asynccontextmanager
async def lifespan(app: FastAPI):
    database.init_db()
    logger.info("database initialized at %s", database.DB_PATH)
    yield


app = FastAPI(
    title="Click Tracker",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


def resolve_client_ip(request: Request) -> str:
    """Resolve the real client IP from proxy headers, falling back to socket."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # First hop is the original client.
        return xff.split(",")[0].strip()
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()
    return request.client.host if request.client else ""


@app.get("/", response_class=HTMLResponse)
async def track(request: Request):
    """Record the click and always serve the landing page."""
    email = request.query_params.get("email")
    token = request.query_params.get("token")
    ip = resolve_client_ip(request)
    user_agent = request.headers.get("user-agent", "")

    # Validate the email for logging/flagging only — we still record the raw
    # value and always serve the page.
    email_valid = True
    if email is not None:
        try:
            ClickQuery(email=email, token=token)
        except ValidationError:
            email_valid = False

    database.insert_click(
        email=email,
        token=token,
        ip_address=ip,
        user_agent=user_agent,
    )

    flags = []
    if email is None:
        flags.append("missing_email")
    elif not email_valid:
        flags.append("invalid_email")
    if token is None:
        flags.append("missing_token")
    flag_str = f" flags={','.join(flags)}" if flags else ""

    ts = database.utc_now_iso()
    logger.info(
        "click email=%s token=%s ip=%s date=%s%s",
        email,
        token,
        ip,
        ts,
        flag_str,
    )

    return templates.TemplateResponse(request, "index.html")


@app.get("/clicks", response_model=list[ClickRecord])
async def clicks(x_admin_key: str | None = Header(default=None)):
    """Return recorded clicks as JSON. Requires a valid X-Admin-Key.

    The response is filtered through ClickRecord, so only the model's declared
    fields are ever serialized — added DB columns won't leak through this API.
    """
    if not ADMIN_API_KEY or x_admin_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")
    return database.get_clicks()


@app.get("/health")
async def health():
    """Internal healthcheck: verify DB connectivity."""
    try:
        database.check_db()
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=503, detail="db unavailable") from exc
    return {"status": "ok"}
