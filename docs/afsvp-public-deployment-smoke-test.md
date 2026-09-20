# AFSVP public deployment and smoke-test checklist

This checklist is for the public AFSVP static-site deployment and the public route boundary only.

It does not authorize any trading behavior, broker access, webhook behavior changes, strategy changes, risk-rule changes, scanner changes, scheduler changes, or execution-code changes.

## Scope

Allowed in this lane:

- Copy `site/afsvp/` static files to the nginx public web root currently used for the public site: `/var/www/rsntl`.
- Validate that `afsvp.com` and `www.afsvp.com` serve static public pages only.
- Validate that `hooks.afsvp.com` only forwards the approved TradingView webhook path to the local futures app.
- Validate that `status.afsvp.com` exposes only approved read-only status routes.
- Validate that local application ports are not directly public.

Not allowed in this lane:

- Branding, palette, logo, or design changes.
- Trading logic, strategy, risk, sizing, scanner, scheduler, broker, or execution changes.
- New public forms, visitor accounts, order controls, account access, raw webhook views, or dashboard-gate exposure.

## Public route model

| Host | Intended exposure | Boundary |
| --- | --- | --- |
| `afsvp.com` | Static public site | Serves files copied from `site/afsvp/` only. |
| `www.afsvp.com` | Static public site | Same as `afsvp.com`. |
| `hooks.afsvp.com` | TradingView webhook ingress | Only forwards the approved webhook path to `127.0.0.1:8000`. |
| `status.afsvp.com` | Read-only status | Only exposes approved sanitized status routes, such as the options scanner `/public/status`. |

## Static-site deployment

From a clean deployed checkout, copy the static site directory contents, not the repository root:

```bash
sudo rsync -av --delete site/afsvp/ /var/www/rsntl/
```

Expected public static files after sync:

```text
/var/www/rsntl/index.html
/var/www/rsntl/styles.css
/var/www/rsntl/privacy/index.html
/var/www/rsntl/terms/index.html
/var/www/rsntl/status/index.html
```

Do not copy private repository files, logs, `.env` files, journals, SQLite files, raw alert payloads, screenshots, local artifacts, or backend source directories into the public web root.

## Pre-deploy safety checks

Run these checks before changing DNS, nginx routing, or TradingView webhook settings:

```bash
sudo nginx -t
sudo find /var/www/rsntl -maxdepth 3 -type f | sort
sudo ss -ltnp | grep -E ':8000|:8010' || true
```

Expected results:

- `nginx -t` passes.
- `/var/www/rsntl` contains only intended static site files and assets.
- Ports `8000` and `8010` may be listening locally, but they must not be directly exposed to the public internet.

## Public smoke tests

Run from a machine outside the VPS/network path when possible.

```bash
curl -I https://afsvp.com/
curl -I https://www.afsvp.com/
curl -I https://afsvp.com/privacy/
curl -I https://afsvp.com/terms/
curl -I https://afsvp.com/status/
```

Expected result: each static public route returns a normal public response such as `200` or the intended redirect to the canonical host.

Check blocked/private paths:

```bash
curl -I https://afsvp.com/gate
curl -I https://afsvp.com/webhook/alert
curl -I https://afsvp.com/status/today
curl -I https://afsvp.com/status/public
curl -I https://afsvp.com/terminal
```

Expected result: the static public site must not expose backend-gated routes, raw webhook routes, operator dashboards, terminal state, journals, account endpoints, or execution controls.

## Webhook boundary smoke test

The public static site is not the webhook host. The webhook host is `hooks.afsvp.com`.

Before changing TradingView, verify that only the approved webhook path is reachable and that authentication is preserved:

```bash
curl -I https://hooks.afsvp.com/webhook/alert
curl -I https://hooks.afsvp.com/
curl -I https://hooks.afsvp.com/status/today
curl -I https://hooks.afsvp.com/gate
```

Expected result:

- The approved webhook path reaches the application boundary but still requires the configured webhook secret for real alert handling.
- Non-webhook paths do not expose dashboard, account, status, journal, or control surfaces.
- `X-Webhook-Secret` or a JSON-body `secret` remains the preferred authentication method; query-string secrets remain deprecated.

## Status boundary smoke test

The status host is separate from the static public site and the webhook host.

```bash
curl -sS https://status.afsvp.com/public/status | python3 -m json.tool
curl -I https://status.afsvp.com/status
curl -I https://status.afsvp.com/watchlist
curl -I https://status.afsvp.com/terminal
curl -I https://status.afsvp.com/signa/context/recent
```

Expected result:

- Approved public status responses are sanitized and read-only.
- Public status responses do not include tickers, contracts, raw scan rows, Signa payloads, database paths, provider errors, aggregate active risk, account state, broker identifiers, execution controls, or order controls unless a route has been separately approved for public exposure.
- Non-approved operator or detail routes are blocked or unavailable publicly.

## Direct-port exposure check

Check from outside the VPS/network path:

```bash
curl -I --connect-timeout 5 http://afsvp.com:8000/ || true
curl -I --connect-timeout 5 http://afsvp.com:8010/ || true
curl -I --connect-timeout 5 http://status.afsvp.com:8000/ || true
curl -I --connect-timeout 5 http://status.afsvp.com:8010/ || true
```

Expected result: direct public access to ports `8000` and `8010` fails or is blocked. Public access should go through nginx only.

## Fail conditions

Stop deployment and revert/repair routing if any of these occur:

- `/gate` is publicly reachable from the static site host.
- Broker credentials, environment variables, journals, database paths, raw webhook payloads, account state, or execution controls are exposed.
- `:8000` or `:8010` is directly reachable from the public internet.
- `afsvp.com` proxies backend application routes instead of static files.
- `hooks.afsvp.com` exposes anything beyond the approved webhook ingress boundary.
- `status.afsvp.com` exposes unsanitized operator data or private route details.

## Post-deploy record

Record the deployment outcome in the project notes with:

- deployed commit SHA;
- date/time of deployment;
- hostnames checked;
- smoke-test result summary;
- any failing route and remediation;
- confirmation that no public execution, broker, account, journal, raw payload, or localhost-port exposure was found.
