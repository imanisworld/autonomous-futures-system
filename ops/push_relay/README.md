# AFSVP push relay

Standalone observer that turns futures-bot status changes into Web Push
notifications for app.afsvp.com. It never talks to a broker and never touches
the bot process; it only reads `/status/today`.

**Events:** position opened · position closed (WIN/LOSS + P&L) · trade resolved ·
daily loss lock · backend down / back online · test.

## Endpoints (proxied at `https://app.afsvp.com/push/`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/push/health` | subscriptions count, backend online flag |
| GET | `/push/vapid-public-key` | key the browser subscribes with |
| POST | `/push/subscribe` | `{subscription, ua}` from `PushManager.subscribe()` |
| POST | `/push/unsubscribe` | `{endpoint}` |
| POST | `/push/test` | `{endpoint}` → one test push to that device |

## Install on the box (one-off; no bot change)

```
mkdir -p /root/afs-shared/push-relay/src
rsync -a ops/ /root/afs-shared/push-relay/src/ops/     # from a clean main checkout
python3 -m venv /root/afs-shared/push-relay/venv
/root/afs-shared/push-relay/venv/bin/pip install -r ops/push_relay/requirements.txt
cp ops/push_relay/afs-push-relay.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now afs-push-relay
```

nginx (app vhost): `location ^~ /push/ { proxy_pass http://127.0.0.1:8020; }`.

State (`/root/afs-shared/push-relay/`): `vapid.json` (private key, 0600,
generated on first start — never commit) and `subscriptions.json`.

iOS: Web Push only works once the site is added to the Home Screen
(iOS 16.4+); the app's Notifications panel says so.
