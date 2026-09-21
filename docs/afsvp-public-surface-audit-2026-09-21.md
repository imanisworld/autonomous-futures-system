# AFSVP public surface audit — 2026-09-21 (read-only)

Scope: `afsvp.com`, `app.afsvp.com`, `hooks.afsvp.com` as seen by an
unauthenticated visitor. Method: `curl` from an external network plus a
read-only look at the nginx vhost files and the running process's env var
*names* (no values). No code, DNS, env, nginx, or trading system was changed.

Probe time: 2026-09-21 01:15–01:25Z. futures-bot release `c7798d49`,
alert_ranker release `a0c34818`.

## 1. What a normal visitor can see

| Host / path | Result |
|---|---|
| `afsvp.com/`, `/privacy/`, `/terms/`, `/status/` | static HTML, CSS, SVG mark; no login |
| `afsvp.com/health` | **JSON, no auth**: `paper_mode`, `live_trading_enabled`, `broker: "tradovate"`, `webhook_secret_required` |
| `afsvp.com/status/today` etc. | 401 (nginx basic auth) — correct |
| `afsvp.com/webhook/alert` | 405 on GET; POST requires secret — correct |
| `app.afsvp.com/` | Expo web bundle (4.2 MB JS), CSS, favicon |
| `app.afsvp.com/_expo/static/js/web/*.js.map` | **source map served (200)** |
| `app.afsvp.com/health` | same JSON as above, no auth |
| `app.afsvp.com/status/*` | **live operator JSON, no auth** (see blockers) |
| `app.afsvp.com/scanner/` | **options scanner UI + `/scanner/docs` + `/scanner/openapi.json`, no auth** (see blockers) |
| `app.afsvp.com/viewer` | 401 `Viewer access required.` — correct (VIEWER_TOKEN) |
| `hooks.afsvp.com` | TLS cert mismatch (cert only covers afsvp.com) — not usable over HTTPS yet |
| `robots.txt`, `sitemap.xml`, `/.well-known/security.txt` | absent on both hosts (SPA fallback returns index.html with 200) |
| `.env`, `.git/HEAD`, `/admin/`, `/openapi.json` on either host | SPA fallback → index.html, **not** the real file — fine, but 200-for-everything hides real 404s |

## 2. What a basic attacker can fingerprint

- `Server: nginx/1.28.3 (Ubuntu)` on every response.
- Framework: FastAPI (`{"detail": ...}` error bodies; `/scanner/docs` Swagger UI is live).
- Frontend: Expo / React Native Web; bundle hash reveals build; source map allows full frontend source recovery.
- Backend API route list from the bundle: `/status/today`, `/status/risk`, `/status/broker-account`, `/status/diagnostics`, `/status/latest-webhook`, `/status/adaptive`.
- Full scanner route list from `/scanner/openapi.json` including POST/PATCH mutators.
- Broker vendor (Tradovate), demo-account equity, account balance/peak, drawdown %, daily-loss cap, max trades/day, per-instrument decision history, and **VPS filesystem paths** (`/root/afs-shared/logs/...`, `/root/afs-backups/...`) from `/status/today` and `/status/diagnostics`.
- No secrets, tokens, keys, or Discord URLs found in the bundle (`grep` for `EXPO_PUBLIC_`, `token`, `secret`, `api_key` → only library internals).

## 3. Blockers — **B1, B2, B4 CLOSED 2026-09-21 01:25Z** (nginx re-gated + headers; see headers doc)

**B1 — `app.afsvp.com/status/**` is fully public.**
Cause: `/etc/nginx/sites-enabled/afsvp-app.conf` has `auth_basic off` on
`location ^~ /status/` (comment: "operator asked to drop the prompt on the
app host (2026-09-21)"), AND the in-app gate is off because
`SITE_ACCESS_CODE` is blank on the running process (`SITE_GATE_SCOPE` is set
but has no effect without a code). Result: `/status/broker-account`,
`/status/diagnostics`, `/status/risk`, `/status/today`, `/status/history`,
`/status/fill-realism` all return live JSON to anyone. `/status/broker-account`
is in the app's own `_SENSITIVE_PATHS` list — the code intends it gated.

**B2 — `app.afsvp.com/scanner/` proxies alert_ranker (port 8010) with no auth.**
Same file, `location /scanner/ { auth_basic off; ... }`. alert_ranker has no
auth of its own by design ("binds to localhost", relies on the proxy). Exposed
unauthenticated mutators: `POST /scanner/rh-options/kill-switch`,
`POST /scanner/rh-options/manage`, `POST /scanner/shadow-journal/reconcile`,
`PATCH /scanner/shadow-journal/{id}/outcome`, `POST /scanner/signa/context/ingest`,
`POST /scanner/webhook/alert`. None has broker order authority, but they can
write shadow-journal state and fire Discord notifications. Also serves Swagger
`/scanner/docs`.

**B3 — VPS paths in public JSON.** `/status/today.journal_path` and several
`/status/diagnostics` fields expose `/root/...` paths. Goes away with B1; also
worth stripping at the source.

**B4 — source map published.** `app.afsvp.com/_expo/static/js/web/<hash>.js.map`
returns 200. Exclude `*.map` from the rsync / add `location ~ \.map$ { return 404; }`.

Non-blocking:
- `/health` is intentionally public on both hosts but names the broker vendor and `webhook_secret_required`. Consider reducing to `{"ok": true}` on the public hosts.
- `hooks.afsvp.com` has no valid cert; TradingView is still posting to the primary host (200s observed), so nothing is broken, but the smoke-test doc's `hooks.` step is not yet true.
- `Server` header version disclosure (`server_tokens off;`).

## 4. Missing security headers

| Header | `afsvp.com` static | `app.afsvp.com` static | proxied API (`/health`, `/status/*`) | `/scanner/` |
|---|---|---|---|---|
| `Strict-Transport-Security` | missing | missing | present (from app) | missing |
| `Content-Security-Policy` (incl. `frame-ancestors`) | missing | missing | missing | missing |
| `X-Content-Type-Options: nosniff` | missing | missing | present | missing |
| `X-Frame-Options` | missing | missing | `DENY` | missing |
| `Referrer-Policy` | missing | missing | `no-referrer` | missing |
| `Permissions-Policy` | missing | missing | missing | missing |

The FastAPI app sets its own headers; nginx sets none, so the static site and
the scanner proxy get nothing. Fix belongs in nginx (`add_header` at the
`server` level of both vhosts, plus `always`). Suggested values are in
`docs/afsvp-security-headers-proposal-2026-09-21.md` — not applied.

## 5. Page wording — what can and cannot be claimed today

Can be claimed now (true):
- HTTPS is enforced (80 → 301 → 443) on both hosts.
- No public user accounts, no payment collection, no broker account linking for visitors, no analytics, no ad or marketing cookies on `afsvp.com`.
- Standard server access logs (IP, user agent, path, status, timestamp).
- Personal data is not sold.
- Secrets are not in the frontend bundle (verified 2026-09-21).
- TradingView webhook requires a shared secret and is rate-limited.
- Vulnerability reports accepted via GitHub private advisory.

**Do-not-publish until B1/B2 are fixed:**
- "Authentication is required for private app areas."
- "Internal dashboards and journals are not publicly reachable."

Never claim: "bank-level"/"military-grade" encryption, penetration-tested,
bug bounty, SOC 2, encryption at rest (unverified), or any uptime figure.

## 6. Retention (for the Privacy page)

Verified from the box: nginx access logs rotate via logrotate (default Ubuntu
14 days compressed); application journals under `/root/afs-shared/logs` are
retained indefinitely for audit/research. The Privacy page states retention
in those terms without naming paths.
