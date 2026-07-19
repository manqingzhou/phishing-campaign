# Click Tracker for Authorized Phishing-Simulation Campaigns

A containerized web service for **authorized** phishing-simulation **click tracking**.
**nginx** (port `8084`) is the public entrypoint; a **FastAPI/Uvicorn** app records
each click into **SQLite** and serves the educational phishing landing page.

Opening a per-recipient link like:

```
http://localhost:8084/?email=user@test.ru&token=12345
```

records the click (email, token, client IP, user-agent, UTC timestamp) and
returns the landing page:

> *You have clicked on an educational phishing link. If this email has caused
> suspicion, please report it to the information security department.*

There is **no credential capture** anywhere in the system.

> Context: for authorized phishing-simulation / security-awareness testing only.

## Demo

- **Video walkthrough (30-60s):** [`docs/Screen Recording 2026-07-19 at 1.05.55 AM.mov`](docs/Screen%20Recording%202026-07-19%20at%201.05.55%20AM.mov)
- **Screenshots:** [`docs/screenshots/Screenshots.docx`](docs/screenshots/Screenshots.docx) — covers docker-compose ps, the landing page, the admin API, the SQLite query, the sent-log, and the nginx health endpoint.

## Stack

- **Deployment:** Docker Compose — `tracker` (FastAPI), `nginx` (public + loopback admin), `mailer` (Python CLI for sending campaigns)
- **Database:** SQLite (WAL mode), persisted to `tracker/data/clicks.db` on the host via a bind mount
- **Framework:** FastAPI + Uvicorn (stdlib-only mailer; SMTP tunneling through an optional HTTP/SOCKS proxy)
- **Tests:** pytest unit + e2e against the running stack

## Layout

```
.
├── docker-compose.yml          # tracker + nginx + mailer, bind mounts, healthchecks, ports
├── .env.example                # ADMIN_API_KEY, BASE_URL, SMTP_* (copy to .env)
├── nginx/
│   ├── nginx.conf              # public :8084 (only / + /nginx-health) + admin :8085 (loopback)
│   └── logs/                   # access.log + error.log (bind-mounted from the container)
├── tracker/
│   ├── Dockerfile              # python:3.12-slim (mirrored), non-root, uvicorn
│   ├── requirements.txt
│   ├── main.py                 # FastAPI routes (/, /clicks, /health)
│   ├── database.py             # SQLite helpers (WAL, short-lived connections)
│   ├── models.py               # Pydantic models (ClickQuery, ClickRecord)
│   ├── data/                   # clicks.db — bind-mounted SQLite file (host-readable)
│   ├── templates/index.html    # Educational phishing landing page
│   └── tests/                  # unit + e2e tests (test_database.py, test_app.py, test_tracker.py)
├── mailer/                     # stdlib-only SMTP CLI
│   ├── send_campaign.py        # CSV -> tokens -> tracking links -> SMTP
│   ├── send_test.py            # one-off SMTP diagnostic
│   ├── generate_links.py       # CSV -> URL+token CSV (no sending)
│   ├── config.py               # .env loader + Gmail/Outlook/163 presets + SMTP_PROXY
│   ├── proxy.py                # stdlib SOCKS5 / HTTP-CONNECT proxy tunnel
│   └── templates/              # email.html + email.txt
├── scripts/
│   └── tail_clicks.py          # sqlite "tail" — streams NEW rows from clicks.db as they appear
└── docs/
    ├── design.md               # Architecture + security rationale
    ├── screenshots/            # Screenshots.docx with all demo captures
    └── Screen Recording ...mov # Video walkthrough
```

## Run

```bash
# 1. Configure secrets (edit ADMIN_API_KEY to a strong value)
cp .env.example .env

# 2. Build & start the full stack (tracker + nginx + mailer)
docker compose up --build -d

# 3. Open a tracking link (records the click, serves the landing page)
curl -i "http://localhost:8084/?email=user@test.ru&token=12345"
#   ... or open it in a browser.
```

Ports:
- `http://localhost:8084/` — **public** tracker (only `GET /`; all other paths → 404)
- `http://127.0.0.1:8085/admin/clicks` — **admin** read API, host-loopback only

## Inspect recorded clicks (admin)

The admin API is reachable only from the host loopback **and** requires the
`X-Admin-Key` header:

```bash
KEY=$(grep '^ADMIN_API_KEY=' .env | cut -d= -f2)

curl -s -H "X-Admin-Key: $KEY" http://127.0.0.1:8085/admin/clicks | python3 -m json.tool
```

Expected checks:
```bash
curl -i "http://localhost:8084/clicks"                                # -> 404 (not public)
curl -i http://127.0.0.1:8085/admin/clicks                            # -> 401 (no key)
curl -i -H "X-Admin-Key: wrong" http://127.0.0.1:8085/admin/clicks    # -> 401
```

The admin response is serialized through the `ClickRecord` model, so only its
declared fields (`id, email, token, ip_address, user_agent, clicked_at`) are ever
returned — extra DB columns cannot leak through this API.

### Read the SQLite file directly

The DB is bind-mounted to `tracker/data/clicks.db`, so you can query it on the
host without touching the container:

```bash
sqlite3 -header -column tracker/data/clicks.db \
    "SELECT id,email,token,ip_address,clicked_at FROM clicks ORDER BY id DESC LIMIT 10;"
```

### Watch clicks stream in (live)

`scripts/tail_clicks.py` polls `clicks.db` and prints only the rows that
appeared since the last poll — useful for live demos and for showing that
the recording is happening in real time, not at request time:

```bash
python3 scripts/tail_clicks.py
# [04:54:41] #4362  davide@test.ru    4bapd8oR1…   172.21.0.1  2026-07-19T04:54:40+00:00
# [04:54:42] #4363  davide@example.ru 3IoYZyZXu…   172.21.0.1  2026-07-19T04:54:41+00:00
```

## Generate & send per-recipient links

```bash
# Generate tracking URLs (no SMTP):
python mailer/generate_links.py recipients.csv \
    --base-url http://localhost:8084/ \
    --output campaign_links.csv

# Preview a campaign without sending:
python mailer/send_campaign.py recipients.csv --dry-run

# Send via Gmail (credentials + optional SMTP_PROXY in .env):
python mailer/send_campaign.py recipients.csv --provider gmail

# Send via the in-stack mailer container (when 127.0.0.1:15236 is the host proxy):
docker compose exec -T -e SMTP_PROXY=http://host.docker.internal:15236 \
    mailer python mailer/send_campaign.py recipients.csv --provider gmail
```

Each recipient gets a unique `secrets.token_urlsafe(16)` token; the tool writes
`mailer/sent_log.csv` (email, token, url, status, detail, sent_at).

`mailer/proxy.py` tunnels SMTP through an HTTP-CONNECT or SOCKS5 proxy using
only the standard library — no PySocks required.

## Logs & health

- nginx access/error logs are written both to the container stdout
  (`docker compose logs -f nginx`) **and** persisted to the host at
  `nginx/logs/access.log` / `nginx/logs/error.log`.
- Both services define a Docker healthcheck; `docker compose ps` shows
  `healthy` / `unhealthy` so you can tell when nginx or the app stops serving.
- nginx's healthcheck hits an **internal** `/nginx-health` endpoint served
  directly by nginx (not the tracker), so self-pings do not pollute the click
  log or write to `clicks.db`. The tracker's own `/health` (every 10s, in
  container) is the only legitimate periodic traffic in the app log.

## Tests

A local virtualenv is used for the fast unit tests; the e2e tests run against the
compose stack.

```bash
python3 -m venv .venv
.venv/bin/pip install -r tracker/requirements.txt

# Unit tests (no Docker needed)
.venv/bin/pytest tracker/tests/test_database.py tracker/tests/test_app.py -v

# Full suite incl. end-to-end (requires `docker compose up -d`)
.venv/bin/pytest tracker/tests/ -v
```

The e2e suite (`test_tracker.py`) auto-skips when the stack is not reachable.
It reads `ADMIN_API_KEY` from the environment, falling back to `.env`.

## Stop

```bash
docker compose down          # stops & removes containers
```

Records and logs live in host bind mounts (`tracker/data/`, `nginx/logs/`), so
they persist across `down`/`up` and are **not** removed by `docker compose down -v`.
To wipe records, delete `tracker/data/clicks.db*` on the host.

## Security model (summary)

- Public `:8084` exposes **only** `GET /` (and the in-nginx `/nginx-health`
  healthcheck). `/clicks`, `/health`, and any other path return 404.
- Admin `/admin/clicks` is bound to `127.0.0.1:8085` (host-only) **and** gated by
  `X-Admin-Key` (defense in depth).
- `/health` is internal — used only by the container healthcheck.
- nginx forwards `X-Real-IP` / `X-Forwarded-For` so the app records the true
  client IP.
- Admin output is bounded by `response_model=list[ClickRecord]`, so added DB
  columns cannot leak through the API.

See `docs/design.md` for the full rationale (including why loopback binding is
used instead of an nginx `allow 127.0.0.1` rule on a published Docker port).
