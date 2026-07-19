#!/usr/bin/env python3
"""SMTP configuration for the phishing-simulation mailer.

Loads settings from environment variables (optionally seeded from a `.env`
file) and provides provider presets for Gmail and Outlook. Standard library
only — no third-party dependencies.

Recognized environment variables:
    SMTP_HOST       SMTP server hostname (overrides provider preset)
    SMTP_PORT       SMTP server port (default 587)
    SMTP_USERNAME   Login username (usually the full email address)
    SMTP_PASSWORD   Login password / app password
    SMTP_FROM       From address (defaults to SMTP_USERNAME)
    SMTP_FROM_NAME  Optional display name for the From header
    SMTP_STARTTLS   "true"/"false" — use STARTTLS (default true)
    SMTP_PROXY      Optional proxy URL to tunnel SMTP through, e.g.
                    socks5h://127.0.0.1:15236 or http://127.0.0.1:15236
    EMAIL_SUBJECT   Subject line for the campaign email
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Provider presets: name -> (host, port, use_starttls)
# 163 (NetEase) uses implicit SSL on port 465, so use_starttls is False.
PROVIDERS: dict[str, tuple[str, int, bool]] = {
    "gmail": ("smtp.gmail.com", 587, True),
    "outlook": ("smtp-mail.outlook.com", 587, True),
    "163": ("smtp.163.com", 465, False),
}

DEFAULT_SUBJECT = "Action required: please verify your account"


def _as_bool(value: str | None, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_env_file(path: str | os.PathLike[str]) -> None:
    """Seed os.environ from a simple KEY=VALUE .env file.

    Existing environment variables are NOT overridden (env wins over file),
    matching the behaviour of most dotenv loaders. Missing files are ignored.
    """
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


@dataclass
class SMTPConfig:
    host: str
    port: int
    username: str
    password: str
    sender: str
    sender_name: str
    use_starttls: bool
    subject: str
    proxy: str = ""

    @classmethod
    def from_env(cls, provider: str | None = None) -> "SMTPConfig":
        """Build a config from the environment, applying a provider preset.

        Explicit SMTP_HOST / SMTP_PORT / SMTP_STARTTLS env vars always take
        precedence over the preset.
        """
        preset_host, preset_port, preset_tls = PROVIDERS.get(
            (provider or "").lower(), ("", 0, True)
        )

        host = os.environ.get("SMTP_HOST") or preset_host
        port = int(os.environ.get("SMTP_PORT") or preset_port or 587)
        username = os.environ.get("SMTP_USERNAME", "")
        password = os.environ.get("SMTP_PASSWORD", "")
        sender = os.environ.get("SMTP_FROM") or username
        sender_name = os.environ.get("SMTP_FROM_NAME", "")
        use_starttls = _as_bool(os.environ.get("SMTP_STARTTLS"), default=preset_tls)
        subject = os.environ.get("EMAIL_SUBJECT") or DEFAULT_SUBJECT
        proxy = os.environ.get("SMTP_PROXY", "")

        return cls(
            host=host,
            port=port,
            username=username,
            password=password,
            sender=sender,
            sender_name=sender_name,
            use_starttls=use_starttls,
            subject=subject,
            proxy=proxy,
        )

    def validate(self) -> list[str]:
        """Return a list of human-readable problems (empty means valid)."""
        problems: list[str] = []
        if not self.host:
            problems.append(
                "SMTP host is not set (use --provider gmail|outlook or SMTP_HOST)"
            )
        if not self.username:
            problems.append("SMTP_USERNAME is not set")
        if not self.password:
            problems.append("SMTP_PASSWORD is not set")
        if not self.sender:
            problems.append("SMTP_FROM / SMTP_USERNAME is not set")
        return problems

    def from_header(self) -> str:
        """Return the value for the email From header."""
        if self.sender_name:
            return f"{self.sender_name} <{self.sender}>"
        return self.sender
