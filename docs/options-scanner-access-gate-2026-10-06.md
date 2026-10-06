# Options scanner API exposure — finding and in-app gate (2026-10-06)

Status: **source fix prepared, NOT deployed.** No nginx, env, service, or box
change was made. No route was exercised against production.

## Finding

Grok reported outside IPs receiving successful responses from scanner
shadow-journal read routes. Source and repository history explain why:

| Fact | Evidence |
|---|---|
| `alert_ranker` (options scanner, port 8010) has **no in-app auth**. It was designed to bind to localhost and rely on the reverse proxy. | `alert_ranker/app.py` (pre-fix module docstring and every route) |
| nginx `location /scanner/` proxies to `127.0.0.1:8010`. | `docs/afsvp-public-surface-audit-2026-09-21.md` §3 B2 |
| 2026-09-21 01:25Z basic auth was restored on `/status/` and `/scanner/`. | `docs/afsvp-security-headers-proposal-2026-09-21.md` |
| **2026-09-21 01:33Z the operator removed basic auth on ALL locations of both vhosts** ("single-operator posture, will secure another way"). No later re-gating is recorded in the repo. | same doc, "01:33Z update" |

Classification: **not a code regression in the scanner** — it never had auth.
It is an **operational regression of the 09-21 auth gate**: once the nginx
gate was removed, the scanner's whole surface became public. Grok's
observation is consistent with this.

What that exposes (from source; production reachability **not re-probed** in
this session — the session's network policy denies `afsvp.com` /
`app.afsvp.com`, and no `claude-audit` VPS access was available):

- **Reads:** `/shadow-journal`, `/shadow-journal/summary`, `/shadow-journal/{id}`,
  `/terminal`, `/status`, `/signa/context/*`, `/rh-options/recent`, `/docs`,
  `/openapi.json`.
- **Writes (no broker/order authority, but they mutate research evidence or
  send Discord):** `PATCH /shadow-journal/{id}/outcome` (rewrites outcome
  status — evidence integrity risk), `POST /shadow-journal/reconcile`,
  `POST /webhook/alert` (triggers scans and Discord alerts),
  `POST /signa/context/ingest`, `POST /signa/context/pull` (spends Signa API
  quota), `POST /rh-options/kill-switch|morning-check|check-positions*`
  (Discord sends), `POST /rh-options/manage|evaluate*`.

Destructive routes were **not** exercised. Severity: the PATCH outcome route
is the highest risk because an outsider could silently relabel shadow-journal
outcomes that research and the Epoch-3 audit read.

Futures side (not changed here, recorded for the operator): with nginx auth
removed, `app.afsvp.com/status/*` depends on the futures `SITE_ACCESS_CODE`
site gate (`webhook/app.py`), which is **off when blank** (the 09-21 audit
found it blank). Verify on the box.

## Fix in this PR

`alert_ranker/access_gate.py`, installed as middleware in `create_app`:

- Public allowlist only: `/health`, `/public/status`, `/login`, `/logout`.
- Everything else requires `OPTIONS_SCANNER_ACCESS_TOKEN` via
  `X-AFS-Scanner-Token` / `Authorization: Bearer`, or an HttpOnly + Secure +
  SameSite=Strict session cookie set by `POST /login` (cookie value is an HMAC
  of the token, never the token; rotating the token revokes sessions).
- With **no token configured**, only a *direct* loopback request is served:
  loopback peer **and** no `X-Forwarded-For` / `X-Real-IP` / `Forwarded` /
  `X-Forwarded-Host` / `X-Forwarded-Proto`. A request proxied by nginx is
  refused.
- `/health` reports `access_gate: token | direct_loopback_only`.
- No change to scanning, scoring, alerts, risk, contract selection, or any
  execution authority.

Regression tests: `tests/test_alert_ranker_access_gate.py` (every registered
route is classified; remote reads and every mutator 401 before the handler;
proxied-loopback refused; token, bearer, cookie, rotation, login failure).
Existing scanner tests now use an explicit loopback peer.

## Deploy notes (operator decision — not done)

1. **Set `OPTIONS_SCANNER_ACCESS_TOKEN`** in the scanner env before or with
   this release. Without it the fallback is fail-closed *only if* nginx sends a
   forwarding header on `/scanner/`. If the vhost uses neither
   `proxy_set_header X-Forwarded-For` nor `X-Real-IP`, proxied requests are
   indistinguishable from local ones and the fallback cannot help. Confirm the
   vhost headers on the box.
2. Independently restore nginx `auth_basic` on `/scanner/` (and `/status/`) —
   defense in depth; the in-app gate is not a reason to leave nginx open.
3. Post-deploy, from an outside network: `GET /scanner/shadow-journal/summary`
   → 401; `GET /scanner/public/status` → 200; `GET /scanner/health` → 200 with
   `access_gate: "token"`.
4. Known UI limitation (pre-existing): the dashboard uses absolute `fetch('/…')`
   paths, so it does not work behind the `/scanner/` prefix regardless of auth.
   Out of scope here.

Non-blocking: `/health` still returns the SQLite path; trim later if desired.
