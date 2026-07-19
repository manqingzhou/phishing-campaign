#!/usr/bin/env python3
"""Generate per-recipient tracking links for a click campaign.

Reads a list of email addresses (one per line, or the first column of a CSV),
assigns each a unique URL-safe token, and writes a mapping CSV plus prints the
full tracking URLs.

Stdlib only — no third-party dependencies.

Usage:
    python generate_links.py recipients.txt
    python generate_links.py recipients.csv --base-url http://localhost:8084/ \
        --output campaign_links.csv

Output CSV columns: email,token,url
"""

from __future__ import annotations

import argparse
import csv
import secrets
import sys
from urllib.parse import urlencode, urlsplit, urlunsplit


def read_emails(path: str) -> list[str]:
    """Read emails from a file: newline list or first column of a CSV.

    Blank lines and a leading `email` header (if present) are skipped.
    Order is preserved; duplicates are removed (first occurrence wins).
    """
    emails: list[str] = []
    seen: set[str] = set()
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if not row:
                continue
            value = row[0].strip()
            if not value:
                continue
            if value.lower() == "email":  # skip header
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
    # Keep any existing path (default "/"); replace the query string.
    path = parts.path or "/"
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def generate(emails: list[str], base_url: str) -> list[tuple[str, str, str]]:
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


def write_csv(rows: list[tuple[str, str, str]], output: str) -> None:
    with open(output, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["email", "token", "url"])
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("emails_file", help="Path to emails (newline list or CSV)")
    parser.add_argument(
        "--base-url",
        default="http://localhost:8084/",
        help="Base tracking URL (default: http://localhost:8084/)",
    )
    parser.add_argument(
        "--output",
        default="campaign_links.csv",
        help="Output mapping CSV (default: campaign_links.csv)",
    )
    args = parser.parse_args(argv)

    emails = read_emails(args.emails_file)
    if not emails:
        print("No emails found in input.", file=sys.stderr)
        return 1

    rows = generate(emails, args.base_url)
    write_csv(rows, args.output)

    for _email, _token, url in rows:
        print(url)
    print(
        f"\nWrote {len(rows)} links to {args.output}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
