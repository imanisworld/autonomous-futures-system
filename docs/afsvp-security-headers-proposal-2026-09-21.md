# AFSVP nginx hardening — 2026-09-21 (APPLIED 01:25Z, operator GO)

**Status:** sections A, B, C applied to both vhosts 2026-09-21 01:25Z via
`nginx -t && systemctl reload nginx` (no app restart). Pre-change backup:
`/root/afs-shared/backups/nginx_20260921T012517Z/`. Verify block below
returned the expected codes; TradingView posts continued 200 after reload.
App host CSP is report-only. Section D (hooks cert) NOT done.

**01:33Z update:** operator removed basic auth on ALL locations of both vhosts
(single-operator posture, "will secure another way"). Headers/CSP/map-block
stay. Backup before removal: `/root/afs-shared/backups/nginx_20260921T013202Z/`.
The `vp` htpasswd entry still exists (reset 01:30Z) and can be re-attached
by restoring `auth_basic`/`auth_basic_user_file` on `/status/` and `/scanner/`.

Rollback: `cp /root/afs-shared/backups/nginx_20260921T012517Z/*.conf /etc/nginx/sites-enabled/ && nginx -t && systemctl reload nginx`.

Companion to `docs/afsvp-public-surface-audit-2026-09-21.md`. Server config,
not page copy. Requires operator GO; changes `/etc/nginx/sites-enabled/*` on
the box, then `nginx -t && systemctl reload nginx`. Not a release, not a
restart of futures-bot.

## A. Re-gate app.afsvp.com (blockers B1, B2)

In `afsvp-app.conf`, restore auth on the two operator locations. Either
nginx basic auth (fastest, matches `afsvp.com`):

```nginx
location ^~ /status/ {
    auth_basic "AFS Operator Status";
    auth_basic_user_file /etc/nginx/.scanner_htpasswd;
    proxy_pass http://127.0.0.1:8000;
    ...
}
location /scanner/ {
    auth_basic "Scanner";
    auth_basic_user_file /etc/nginx/.scanner_htpasswd;
    proxy_pass http://127.0.0.1:8010/;
    ...
}
```

or set `SITE_ACCESS_CODE` in the futures-bot env with `SITE_GATE_SCOPE=full`
(that is an env change → release/restart → post-09-30 freeze unless ruled
tooling repair). Basic auth in nginx needs no app restart. Keep
`location = /status/public { auth_basic off; }` for the sanitized route.

The Expo app calls `/status/*` from the browser; with basic auth the browser
will prompt once per session. That is the behaviour the 09-21 comment removed;
the trade-off is: prompt, or public balances.

## B. Security headers (both vhosts, `server {}` level, `always` so 4xx/5xx get them too)

```nginx
server_tokens off;
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()" always;
add_header X-Frame-Options "DENY" always;
```

CSP — static site (`rsntl.conf`), verified against what the pages load
(self, Google Fonts CSS + font files, inline `style=` attributes on `h1`):

```nginx
add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
```

CSP — app host (`afsvp-app.conf`). The Expo bundle uses inline styles and
loads JetBrains Mono from `fonts.gstatic.com`; scanner UI is inline-script.
Start in report-only, tighten after reading violations:

```nginx
add_header Content-Security-Policy-Report-Only "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'" always;
```

Note nginx `add_header` in a `location` block *replaces* all server-level
`add_header`s — put them at `server` level only, or repeat them in every
location that has its own `add_header`.

## C. Small fixes

```nginx
# B4: never serve source maps
location ~ \.map$ { return 404; }
# real 404s for well-known probes instead of SPA fallback
location = /robots.txt  { try_files $uri =404; }
location = /sitemap.xml { try_files $uri =404; }
location ^~ /.well-known/ { try_files $uri =404; }
```

Also exclude `*.map` from the Expo export rsync to `/var/www/afsvp-app`.

## D. hooks.afsvp.com

Certificate does not cover the name. Either add a SAN via certbot
(`certbot --expand -d afsvp.com -d www.afsvp.com -d hooks.afsvp.com`) or
remove the name from DNS/nginx until it is needed. TradingView is currently
posting to the primary host and getting 200s; nothing is broken.

## Verify after apply

```bash
curl -sI https://afsvp.com/ | grep -iE "strict|content-security|x-content|referrer|permissions|x-frame"
curl -s -o /dev/null -w '%{http_code}\n' https://app.afsvp.com/status/broker-account   # expect 401
curl -s -o /dev/null -w '%{http_code}\n' https://app.afsvp.com/scanner/openapi.json   # expect 401
curl -s -o /dev/null -w '%{http_code}\n' https://afsvp.com/.well-known/security.txt   # expect 200
curl -s -o /dev/null -w '%{http_code}\n' "https://app.afsvp.com/_expo/static/js/web/x.js.map"  # expect 404
```
