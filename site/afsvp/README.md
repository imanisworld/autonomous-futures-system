# AFSVP public site

Static public pages for `https://afsvp.com`.

## Intended server layout

Copy the contents of this directory to the nginx public web root currently used for the site (`/var/www/rsntl`).

Expected public routes:

- `/` — landing page
- `/privacy/` — privacy policy
- `/terms/` — terms and conditions
- `/status/` — public status-boundary explainer

Use `docs/afsvp-public-deployment-smoke-test.md` as the deployment and smoke-test checklist before routing or public-host changes.

## Safety boundary

This directory contains static public content only. It must not expose broker credentials, trading journals, execution controls, `/gate`, environment variables, or localhost application ports.

The domain routing should remain separated:

- `afsvp.com` and `www.afsvp.com` — static site only
- `hooks.afsvp.com` — TradingView webhook only, ultimately forwarding the approved webhook path to `127.0.0.1:8000`
- `status.afsvp.com` — approved read-only status routes only, such as the sanitized options-scanner `/public/status` endpoint

Do not expose ports `8000` or `8010` directly to the public internet.

## TradingView

The futures application accepts alerts at `POST /webhook/alert`. After `hooks.afsvp.com` is deployed over valid HTTPS and an authenticated paper/demo smoke test succeeds, the TradingView webhook URL can be changed to:

`https://hooks.afsvp.com/webhook/alert`

Preserve the existing webhook authentication. The application prefers `X-Webhook-Secret` or a `secret` field in the JSON body; query-string secrets are deprecated because they can appear in access logs.

## Security page and security.txt (added 2026-09-21)

- `/security/` — what the site actually does, vulnerability reporting, testing boundaries
- `/.well-known/security.txt` — RFC 9116 disclosure contact

nginx must serve dotfile directories for `/.well-known/` (the default `try_files`
does; do not add a `location ~ /\.` deny rule that would catch it). The Security
page's "authentication for private areas" line is only true once the
`app.afsvp.com` `/status/` and `/scanner/` locations are re-gated — see
`docs/afsvp-public-surface-audit-2026-09-21.md` blockers B1/B2.
