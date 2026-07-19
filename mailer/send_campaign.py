#!/usr/bin/env python3
"""Send per-recipient tracking links for an authorized phishing simulation.

Reads a recipient CSV (one email per line, or the first column of a CSV),
assigns each recipient a unique URL-safe token, builds a tracking link of the
form:

    http://localhost:8084/?email=<email>&token=<token>

renders an HTML + plain-text email from the templates in ``mailer/templates``,
and delivers it over SMTP (Gmail or Outlook). A ``sent_log.csv`` records the
outcome for every recipient.

Standard library only (smtplib, email, csv, secrets, ssl).

Context: for authorized security-awareness / phishing-simulation testing only.
The landing page served by the tracker is a benign "Hello World"; no
credentials are captured.

Examples:
    # Preview without sending (no SMTP connection made)
    python mailer/send_campaign.py mailer/recipients.example.csv --dry-run

    # Send via Gmail (credentials from .env or environment)
    python mailer/send_campaign.py recipients.csv --provider gmail

    # Send via Outlook with an explicit base URL
    python mailer/send_campaign.py recipients.csv --provider outlook \
        --base-url http://localhost:8084/
"""

from __future__ import annotations

import argparse
import csv
import os
import secrets
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from string import Template
from urllib.parse import urlencode, urlsplit, urlunsplit

from config import SMTPConfig, load_env_file
from proxy import Proxy

# Repo root is the parent of the mailer/ directory.
MAILER_DIR = Path(__file__).resolve().parent
REPO_ROOT = MAILER_DIR.parent
TEMPLATE_DIR = MAILER_DIR / "templates"


def read_emails(path: str) -> list[str]:
    """Read emails: newline list or first column of a CSV.

    Blank lines and a leading ``email`` header are skipped; order is preserved
    and duplicates removed (first occurrence wins).
    """
    emails: list[str] = []
    seen: set[str] = set()
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row:
                continue
            value = row[0].strip()
            if not value or value.lower() == "email":
                continue
            if value in seen:
                continue
            seen.add(value)
            emails.append(value)
    return emails


def build_url(base_url: str, email: str, token: str) -> str:
    """Build a tracking URL with email + token query params on base_url."""
    parts = urlsplit(base_url)
    query = urlencode({"email": email, "token": token})
    path = parts.path or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def generate_tokens(emails: list[str], base_url: str) -> list[tuple[str, str, str]]:
    """Return (email, token, url) triples with a unique token per email."""
    rows: list[tuple[str, str, str]] = []
    tokens: set[str] = set()
    for email in emails:
        token = secrets.token_urlsafe(16)
        while token in tokens:  # astronomically unlikely, but be safe
            token = secrets.token_urlsafe(16)
        tokens.add(token)
        rows.append((email, token, build_url(base_url, email, token)))
    return rows


def load_templates() -> tuple[Template, Template]:
    """Load the HTML and plain-text email templates."""
    html = (TEMPLATE_DIR / "email.html").read_text(encoding="utf-8")
    text = (TEMPLATE_DIR / "email.txt").read_text(encoding="utf-8")
    return Template(html), Template(text)


def build_message(
    config: SMTPConfig,
    recipient: str,
    link: str,
    html_tpl: Template,
    text_tpl: Template,
) -> EmailMessage:
    """Render a multipart (text + html) email for a single recipient."""
    fields = {"email": recipient, "link": link}
    msg = EmailMessage()
    msg["Subject"] = config.subject
    msg["From"] = config.from_header()
    msg["To"] = recipient
    msg.set_content(text_tpl.safe_substitute(fields))
    msg.add_alternative(html_tpl.safe_substitute(fields), subtype="html")
    return msg


def _make_proxy_smtp(proxy: Proxy) -> type:
    """Build an smtplib.SMTP subclass that dials out through ``proxy``."""

    class _ProxySMTP(smtplib.SMTP):
        def _get_socket(self, host, port, timeout):  # type: ignore[override]
            return proxy.open(host, port, timeout if timeout else 30)

    return _ProxySMTP


def _make_proxy_smtp_ssl(proxy: Proxy) -> type:
    """SMTP_SSL subclass: tunnel through ``proxy``, then wrap in TLS."""

    class _ProxySMTP_SSL(smtplib.SMTP_SSL):
        def _get_socket(self, host, port, timeout):  # type: ignore[override]
            raw = proxy.open(host, port, timeout if timeout else 30)
            return self.context.wrap_socket(raw, server_hostname=self._host)

    return _ProxySMTP_SSL


def connect(config: SMTPConfig) -> smtplib.SMTP:
    """Open an authenticated SMTP connection (STARTTLS or implicit SSL).

    If ``config.proxy`` is set, the TCP connection to the SMTP server is
    tunnelled through that proxy (SOCKS5 or HTTP CONNECT).
    """
    context = ssl.create_default_context()
    proxy = Proxy.from_url(config.proxy)

    if config.use_starttls:
        if proxy is not None:
            server: smtplib.SMTP = _make_proxy_smtp(proxy)(
                config.host, config.port, timeout=30
            )
        else:
            server = smtplib.SMTP(config.host, config.port, timeout=30)
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()
    else:
        if proxy is not None:
            server = _make_proxy_smtp_ssl(proxy)(
                config.host, config.port, context=context, timeout=30
            )
        else:
            server = smtplib.SMTP_SSL(config.host, config.port, context=context, timeout=30)
    server.login(config.username, config.password)
    return server


def write_sent_log(path: str, records: list[dict[str, str]]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["email", "token", "url", "status", "detail", "sent_at"]
        )
        writer.writeheader()
        writer.writerows(records)


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "recipients_file", help="Path to recipients (newline list or CSV)"
    )
    parser.add_argument(
        "--provider",
        choices=sorted(("gmail", "outlook")),
        help="SMTP provider preset (host/port). Overridden by SMTP_HOST/SMTP_PORT.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Base tracking URL (default: BASE_URL env or http://localhost:8084/)",
    )
    parser.add_argument(
        "--output",
        default=str(MAILER_DIR / "sent_log.csv"),
        help="Path for the send-outcome CSV (default: mailer/sent_log.csv)",
    )
    parser.add_argument(
        "--env-file",
        default=str(REPO_ROOT / ".env"),
        help="Path to a .env file to seed configuration (default: repo .env)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render and print emails without connecting to SMTP",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    load_env_file(args.env_file)
    base_url = args.base_url or os.environ.get("BASE_URL") or "http://localhost:8084/"

    emails = read_emails(args.recipients_file)
    if not emails:
        print("No emails found in input.", file=sys.stderr)
        return 1

    rows = generate_tokens(emails, base_url)
    html_tpl, text_tpl = load_templates()
    config = SMTPConfig.from_env(args.provider)

    if args.dry_run:
        for email, token, url in rows:
            print(f"--- {email} ---")
            print(f"token: {token}")
            print(f"link:  {url}")
        print(
            f"\n[dry-run] {len(rows)} message(s) prepared; nothing sent.",
            file=sys.stderr,
        )
        return 0

    problems = config.validate()
    if problems:
        print("SMTP configuration incomplete:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nSet the values in .env (see .env.example) or pass --provider, "
            "then retry. Use --dry-run to preview without sending.",
            file=sys.stderr,
        )
        return 2

    records: list[dict[str, str]] = []
    sent = 0
    server: smtplib.SMTP | None = None
    try:
        print(
            f"Connecting to {config.host}:{config.port} as {config.username} ...",
            file=sys.stderr,
        )
        server = connect(config)
        for email, token, url in rows:
            now = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                msg = build_message(config, email, url, html_tpl, text_tpl)
                server.send_message(msg)
                sent += 1
                status, detail = "sent", ""
                print(f"sent -> {email}", file=sys.stderr)
            except smtplib.SMTPException as exc:  # per-recipient failure
                status, detail = "error", str(exc)
                print(f"FAILED -> {email}: {exc}", file=sys.stderr)
            records.append(
                {
                    "email": email,
                    "token": token,
                    "url": url,
                    "status": status,
                    "detail": detail,
                    "sent_at": now,
                }
            )
    except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
        print(f"SMTP connection/login failed: {exc}", file=sys.stderr)
        # Record any recipients not yet attempted as skipped.
        attempted = {r["email"] for r in records}
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for email, token, url in rows:
            if email not in attempted:
                records.append(
                    {
                        "email": email,
                        "token": token,
                        "url": url,
                        "status": "skipped",
                        "detail": "connection failed",
                        "sent_at": now,
                    }
                )
        write_sent_log(args.output, records)
        return 3
    finally:
        if server is not None:
            try:
                server.quit()
            except smtplib.SMTPException:
                pass

    write_sent_log(args.output, records)
    print(
        f"\nDone: {sent}/{len(rows)} sent. Log written to {args.output}",
        file=sys.stderr,
    )
    return 0 if sent == len(rows) else 4


if __name__ == "__main__":
    raise SystemExit(main())
