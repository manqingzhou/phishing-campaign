#!/usr/bin/env python3
"""Send a single diagnostic test email to verify SMTP settings.

Standalone helper for the mailer. It connects to the configured SMTP server
and sends ONE short, plain test message so you can confirm your provider
credentials / host / port work before running anything else.

It deliberately does NOT use the campaign templates or tracking links — the
body is just an "SMTP configuration test" note. Send it to an address you
control (e.g. yourself).

Standard library only (smtplib, email, ssl).

Examples:
    # Send a test to yourself via 163 (credentials from .env)
    python mailer/send_test.py you@163.com --provider 163

    # Use whatever SMTP_HOST/PORT are in .env
    python mailer/send_test.py you@example.com
"""

from __future__ import annotations

import argparse
import smtplib
import ssl
import sys
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from config import SMTPConfig, load_env_file
from send_campaign import connect

MAILER_DIR = Path(__file__).resolve().parent
REPO_ROOT = MAILER_DIR.parent


def build_test_message(config: SMTPConfig, recipient: str) -> EmailMessage:
    """Render a short plain-text diagnostic message."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    msg = EmailMessage()
    msg["Subject"] = "SMTP configuration test"
    msg["From"] = config.from_header()
    msg["To"] = recipient
    msg.set_content(
        "This is a test message from the mailer SMTP configuration check.\n"
        f"Sent at {now} via {config.host}:{config.port} as {config.username}.\n\n"
        "If you received this, your SMTP settings are working."
    )
    return msg


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("recipient", help="Address to send the test email to")
    parser.add_argument(
        "--provider",
        choices=sorted(("gmail", "outlook", "163")),
        help="SMTP provider preset (host/port). Overridden by SMTP_HOST/SMTP_PORT.",
    )
    parser.add_argument(
        "--env-file",
        default=str(REPO_ROOT / ".env"),
        help="Path to a .env file to seed configuration (default: repo .env)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    load_env_file(args.env_file)
    config = SMTPConfig.from_env(args.provider)

    problems = config.validate()
    if problems:
        print("SMTP configuration incomplete:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nSet the values in .env (see .env.example) or pass --provider, "
            "then retry.",
            file=sys.stderr,
        )
        return 2

    print(
        f"Connecting to {config.host}:{config.port} as {config.username} ...",
        file=sys.stderr,
    )
    server: smtplib.SMTP | None = None
    try:
        server = connect(config)
        server.send_message(build_test_message(config, args.recipient))
    except (smtplib.SMTPException, ssl.SSLError, OSError) as exc:
        print(f"SMTP send failed: {exc}", file=sys.stderr)
        return 3
    finally:
        if server is not None:
            try:
                server.quit()
            except smtplib.SMTPException:
                pass

    print(f"Test email sent to {args.recipient}.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
