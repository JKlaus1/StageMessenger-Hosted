# Stage Messenger — Hosted

Multi-gig version of Stage Messenger served from Joseph's home server at
**https://stage-messenger.com**. Bands use it from their own phones; no Pi on site.
Separate from the Pi build (`JKlaus1/StageMessenger`), which is unchanged.

## Roles

| Role | Where | How they get in |
|---|---|---|
| Admin (Joseph) | `/admin` | Password set on the server with `manage.py`; Cloudflare Access in front |
| Leader | `/login` → `/dashboard` | Account created by the admin. Creates gigs (cap per leader, default 5) |
| Member | `/g/<join token>` | The gig's join link. Registers a name, unique within that gig only |
| Stage display | `/g/<join token>/display?monitor=N&name=STAGE+L` | Same link plus monitor number |
| Control | `/c/<gig id>` | The leader's signed-in session only, never a URL alone |

## Isolation and safety (as built)

- Join tokens are 128-bit random (`secrets.token_urlsafe(16)`).
- Every Socket.IO room is `g:<gigId>:<room>`. A socket's gig is fixed at connect time.
- Client-supplied targets are validated (`mon-N`, `personal-NAME`, `all`) and re-prefixed. Anything else is dropped.
- Device lists, logs and messages only go to that gig's sockets.
- Each registered device gets a **device token** (rejoin credential, in memory):
  - a phone that drops and reconnects keeps its name, even after the leader rotates the join link;
  - **Kick** revokes that token.
- **Leader controls:**
  - **Rotate link** — old link stops admitting new devices; devices already in are unaffected.
  - **Kick** — remove one device.
  - **End gig** — disconnects everyone and kills the link.
- Gigs idle for 48 h end automatically. Ended gigs are purged after 30 days.
- **Rate limits:**
  - leader login: 10 failures per 15 min per IP and per username;
  - admin login: 5 failures per 15 min;
  - bad join links: 20 per 10 min per IP;
  - messages: 30 per 10 s per device.
- **HTTP hardening:**
  - JSON-only, same-origin POSTs;
  - `Referrer-Policy: no-referrer`, so tokens never leak via Referer;
  - no access log, since paths contain tokens;
  - Socket.IO accepts only the `PUBLIC_URL` origin.
- Live state (connections, message logs) is in memory, as on the Pi. A restart drops connections; clients reconnect.

## Layout

```
server.py   Flask + Flask-SocketIO app (routes, APIs, socket events)
store.py    SQLite: admin password, leaders, gigs
live.py     in-memory per-gig state, rate limiter
manage.py   server CLI: set-admin-password, list-leaders, reset-leader-password
public/     pages + assets (socket.io 4.7.5 client, qrcode, PWA icons)
deploy/     docker-compose.yml (copied to ~/services/stage-messenger/) and setup.sh
tests/      test_isolation.py (67 checks), test_live_server.py (gunicorn + real WebSockets)
```

## Server (see the home-server access handoff)

| Item | Value |
|---|---|
| Code | `~/src/StageMessenger-Hosted` |
| Service | `~/services/stage-messenger/` (compose file, `data/` SQLite, gitignored) |
| Secret | `/etc/stage-messenger/env` — `SECRET_KEY`, chmod 600, created by `setup.sh` |
| Container | `stage-messenger` on `caddy_default`, port 8000 internal, no host ports, uid 1000 |
| Process | gunicorn `-w 1 --threads 100` (one worker — live state is in memory) |
| Public path | Cloudflare (existing tunnel) → `http://caddy:80` → `reverse_proxy stage-messenger:8000` |

Install or update (from the phone):

```
ssh joseph@192.168.1.121 'bash ~/src/StageMessenger-Hosted/deploy/setup.sh'
```

`setup.sh` pulls, builds, restarts and health-checks. It also confirms Caddy can reach the container
and that Ledger and ledger-backup are still healthy. It never touches Caddy, cloudflared or Ledger.
A rebuild drops live connections, so don't deploy during someone's gig.

## Tests

```
python3 tests/test_isolation.py
python3 tests/test_live_server.py
```
