# Design & Security Notes — nginx + FastAPI Click Tracker

## Purpose
Authorized email-campaign / phishing-simulation **click tracking** with a
benign "Hello World" landing page. Each recipient receives a unique link of the
form:

```
http://localhost:8084/?email=<addr>&token=<unique-token>
```

Opening the link records the event (email, token, client IP, user-agent,
UTC timestamp) into SQLite and serves the landing page. There is **no
credential capture** — the page is purely informational.

## Architecture

```
Public  Browser ──▶ nginx :8084 ─proxy(only /)─▶ FastAPI app :8000 ─▶ SQLite (volume)
Admin   Host    ──▶ nginx 127.0.0.1:8085 ─proxy(/admin/*, X-Admin-Key)─▶ same app
Health  compose ──▶ app container: python urllib localhost:8000/health (internal)
```

- **nginx** is the only network-facing component. It terminates HTTP and
  reverse-proxies to the app over the internal compose network.
- **FastAPI/Uvicorn** app (`tracker`) handles the three routes and owns the DB.
- **SQLite** is stored via a host **bind mount** (`tracker/data/clicks.db`), so
  records persist across `docker compose down`/`up` and are directly readable on
  the host with `sqlite3`. nginx logs are likewise bind-mounted to `nginx/logs/`.

## Components

| File | Role |
|------|------|
| `nginx/nginx.conf` | Two server blocks: public `:8084` (only `GET /`), admin `:8085` (`/admin/clicks`). Sets real-IP headers. |
| `docker-compose.yml` | Wires `tracker` + `nginx`, bind mounts, healthchecks, port bindings, `.env`. |
| `tracker/main.py` | FastAPI routes: `/`, `/clicks`, `/health`; client-IP resolution; logging. |
| `tracker/database.py` | SQLite schema, WAL, short-lived connections, `init_db`/`insert_click`/`get_clicks`. |
| `tracker/models.py` | Pydantic `ClickQuery` (inbound params) and `ClickRecord` (outbound row; used as the `/clicks` `response_model`). |
| `tracker/templates/index.html` | Standalone Hello World landing page. |
| `mailer/generate_links.py` | Stdlib CLI: emails → unique tokens → tracking URLs + `campaign_links.csv`. |

## Security Model

1. **Public surface is minimal.** On `:8084`, only `location = /` is proxied.
   Every other path (`/clicks`, `/health`, anything) returns `404`. This keeps
   the admin/read API and the internal healthcheck off the public port.

2. **Admin read API is host-loopback only.** The admin server listens on
   `:8085` inside nginx, but compose publishes it as `127.0.0.1:8085:8085`, so
   it is reachable only from the Docker host's loopback interface — not from the
   LAN or the internet.
   - *Why loopback binding instead of `allow 127.0.0.1;` in nginx?* On a
     published Docker port, Docker source-NATs the client, so the packet
     arrives at nginx with a container-bridge source IP. An nginx
     `allow 127.0.0.1; deny all;` rule would therefore never see the real
     `127.0.0.1` and would misbehave. Binding the published port to the host
     loopback is the reliable way to enforce "host-only".

3. **Defense in depth: API key.** The app requires header
   `X-Admin-Key: <ADMIN_API_KEY>` on `/clicks` and returns `401` otherwise.
   So even if the admin port were exposed, records are not readable without the
   secret. The key is provided via `.env` (`ADMIN_API_KEY`).

4. **Health endpoint is internal.** `/health` is used only by the compose
   healthcheck (executed *inside* the app container via Python stdlib), and is
   not exposed through either public path.

5. **True client IP.** nginx forwards `X-Real-IP` and appends to
   `X-Forwarded-For`. The app resolves the IP as: first hop of
   `X-Forwarded-For` → `X-Real-IP` → socket peer.

6. **Bounded admin output.** `/clicks` declares `response_model=list[ClickRecord]`,
   so FastAPI serializes only the model's fields. Columns added to the `clicks`
   table (or an accidental `SELECT *`) will not leak through the API — the output
   contract lives in `ClickRecord`, not in the SQL.

## Data Model

Table `clicks`:

| column | type | notes |
|--------|------|-------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `email` | TEXT | may be NULL (partial/malformed link) |
| `token` | TEXT | **indexed**; unique per recipient via the generator |
| `ip_address` | TEXT | resolved client IP |
| `user_agent` | TEXT | request UA |
| `clicked_at` | TEXT NOT NULL | ISO-8601 UTC |

- **Every click is a new row** — full history, including repeat clicks. `token`
  is treated as a per-recipient identifier via the link generator + index.
- To collapse to one row per token, switch to `UNIQUE(token)` + an upsert.
- **WAL** journal mode improves read/write concurrency. Connections are
  short-lived (opened/closed per call) so no connection is shared across
  coroutines (async-safe).

## Behavior Notes

- `GET /` **always** serves the landing page, even for missing/invalid
  `email`/`token`. Malformed input is still recorded and flagged in the log line
  (`flags=missing_email,invalid_email,...`).
- Log format per click: `click email=.. token=.. ip=.. date=.. [flags=..]`.

## Operational Notes (this environment)

- Docker Hub and Debian apt repositories were unreachable on the build network.
  Base images (`python:3.13-slim`, `nginx:alpine`) were pulled via the
  `docker.m.daocloud.io` mirror and re-tagged to their canonical names.
- Because `apt-get` could not install `curl`, the container healthcheck uses
  Python's stdlib (`urllib`) instead of `curl` — no extra OS packages needed.
- Python deps install from the Tsinghua PyPI mirror
  (`https://pypi.tuna.tsinghua.edu.cn/simple`) because the default PyPI index was
  slow on the build network.
- Both services define a Docker healthcheck (tracker via stdlib `urllib`, nginx
  via `wget` to `127.0.0.1:8084`), so `docker compose ps` reports `healthy`/
  `unhealthy`. nginx access/error logs are written to container stdout **and**
  bind-mounted to `nginx/logs/` on the host.

## Testing Strategy

- **Unit (no Docker):** `test_database.py` (DB layer, temp file),
  `test_app.py` (FastAPI `TestClient`, in-process, temp DB).
- **End-to-end (running stack):** `test_tracker.py` against `:8084`/`:8085`;
  auto-skips if the stack is not up. Covers page display, HTTP handling, DB
  persistence via admin, real-IP forwarding, admin lockdown (404/401), and edge
  cases (missing params, repeat token).
