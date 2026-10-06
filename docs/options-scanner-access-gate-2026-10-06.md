# Options scanner in-app access gate (2026-10-06)

Status: **source change prepared, NOT deployed.** No nginx, env, service, or
box change was made. No route was exercised against production.

## Why

The scanner was built to bind to localhost and rely on reverse-proxy auth for
everything except `/public/status`. Repository records show the proxy auth on
the scanner location was removed on 2026-09-21
(`docs/afsvp-security-headers-proposal-2026-09-21.md`), so access control for
the scanner rested on nothing in-app. This is an operational regression of
the 09-21 auth gate, not a scanner code regression: the scanner never had
in-app auth. Current production reachability was **not re-probed** in this
session (no network route to the hosts, no `claude-audit` box access). Details
of the report that prompted this belong in the private security channel per
`SECURITY.md`, not in this public repo.

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
