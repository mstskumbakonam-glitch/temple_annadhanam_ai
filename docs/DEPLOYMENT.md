# Security and safe deployment

## Route protection

| Routes | Without keys configured | With keys |
|---|---|---|
| `GET /api/health` (liveness) | public | public (no secrets in it) |
| All other `GET` (dashboard, cameras, alerts, history, preview, AI status, seats, visitors, DB health) | open | viewer **or** admin key |
| Seat status, sessions, attendance register | open | **operator** or admin key |
| Other `POST` / `PUT` / `DELETE` (temples, halls, seat layouts, staff, cameras, RTSP URLs, lines/zones, alert acknowledge) | open | **admin** key only |
| Staff records (personal data) | open | read: operator or admin (phone numbers admin only); write: admin |
| Camera stream address (`rtsp_url_masked`) | shown | shown to admins only |
| `/docs`, `/redoc`, `/openapi.json` | on | off when `APP_ENV=production` (override with `DOCS_ENABLED`) |

Keys go in `Authorization: Bearer <key>` (or `X-API-Key`). They are compared in
constant time and never logged. The dashboard keeps the key in the browser tab's
session storage only; it is never compiled into the JavaScript bundle.

Startup refuses `APP_ENV=production` when: no `API_ADMIN_KEYS`; any key shorter than
24 characters; `CORS_ORIGINS=*`.

## Environment variables

All settings live in `backend/.env` (template: `backend/.env.example`, every variable
documented there). The ones that matter for safety:

| Variable | Production value |
|---|---|
| `APP_ENV` | `production` |
| `API_ADMIN_KEYS`, `API_OPERATOR_KEYS`, `API_VIEWER_KEYS` | long random values (`secrets.token_urlsafe(32)`), different per person or role, rotated when someone leaves |
| `CORS_ORIGINS` | exact dashboard origin, e.g. `https://crowd.temple.example` |
| `DATABASE_URL` | dedicated DB user with a strong password, not `postgres` |
| `DEMO_MODE` | `false` |
| `PREVIEW_ENABLED` | `false` unless operators need it; if `true`, keep `PREVIEW_ANONYMIZE=true` |
| `TRUSTED_PROXY_COUNT` | `1` behind one reverse proxy, else `0` |
| `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_WRITES_PER_MINUTE` | defaults (600 / 60) are fine for a few operators |
| `AI_AUTOSTART` | `true`, with exactly **one** uvicorn worker process |

Never commit `.env`. RTSP passwords are stored in the `cameras` table only and are
redacted from logs, events, alert messages and API responses.

## Recommended topology for the temple

```
cameras (VLAN, no internet) ──► AI PC (backend + PostgreSQL) ──► reverse proxy (HTTPS) ──► operators' browsers
```

1. Keep cameras and the AI PC on a separate VLAN; the cameras need no internet access.
2. PostgreSQL listens on localhost only (the provided `docker-compose.yml` already binds
   to `127.0.0.1`).
3. Run uvicorn on `127.0.0.1:8000` and put Caddy or nginx in front with HTTPS
   (Caddy obtains certificates automatically). Serve the built dashboard
   (`frontend/dist`) from the same proxy, and proxy `/api` to uvicorn.
4. Set `TRUSTED_PROXY_COUNT=1` so rate limiting sees real client addresses.
5. Operators reach the dashboard over the temple LAN or a VPN (WireGuard/Tailscale).
   **Do not port-forward the API to the internet.** If remote access is unavoidable,
   use the VPN, not a public URL.
6. Back up PostgreSQL daily (`pg_dump`), encrypted, and test a restore.

## Demo deployments

For a demo on a laptop: bind to `127.0.0.1`, `DEMO_MODE=true`, keys optional. For a
demo others will open from their devices: keys **required**, HTTPS via the reverse
proxy, `DOCS_ENABLED=false`, and only recorded footage you are permitted to show.

## Privacy

* No face detection or recognition; visitors are anonymous; tracker ids are temporary.
* Stored analytics are counts, zone ids and anonymous track ids — no images.
* The preview frame exists in memory only and pixelates heads by default.
* Display CCTV / AI notice boards at entrances; follow India's DPDP Act 2023 obligations
  if any personal data (e.g. staff records) is processed.

## Remaining gaps before a public production launch

* Per-person accounts with audit trail (currently shared role keys). Replace
  `app.security.authorize` with an OIDC/OAuth2 dependency when needed.
* Rate limits are per process and in memory (fine for one worker; use the proxy's
  limiter or Redis if you scale out).
* No automated dependency/CVE scanning yet (add `pip-audit` and `npm audit` to CI).
* Not tested against real RTSP cameras, a GPU, or a cloud host in this work.
