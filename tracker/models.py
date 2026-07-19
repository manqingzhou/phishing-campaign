"""Pydantic models for the click tracker.

Two models:
- ClickQuery: the per-recipient query params on the tracking link (email + token).
- ClickRecord: a stored click event as returned by the admin API.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr


class ClickQuery(BaseModel):
    """Query parameters carried by a tracking link.

    Both fields are optional so that a malformed link (missing email/token)
    still serves the landing page and records a partial event.
    `email` uses EmailStr for validation, but validation failures are handled
    at the route level (we still record the raw value and flag it in logs).
    """

    email: Optional[EmailStr] = None
    token: Optional[str] = None


class ClickRecord(BaseModel):
    """A stored click event, as returned by GET /clicks."""

    id: int
    email: Optional[str] = None
    token: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    clicked_at: str
