# External VPS heartbeat

Status: **repo-only / not deployed**

This is a provider-neutral dead-man check for total VPS, network, or watcher
failure. It is deliberately separate from strategy, broker, feed, and trading
health.

## What it proves

Every five minutes, `afs-external-heartbeat.timer` runs
`ops/external_heartbeat.py`.

The helper:

1. reads `/tmp/afs_watcher/latest_tick.json`;
2. requires the watcher tick to be fresh (default maximum age: 12 minutes);
3. sends one HTTPS GET to the configured external heartbeat URL;
4. sends nothing when the watcher state is missing, malformed, stale, or too
   far in the future.

The watcher's own verdict is intentionally ignored. A fresh `WARN` or
`BLOCKED` tick still sends the external heartbeat because the external check
answers only whether the host + watcher + outbound network path are alive.
Existing watcher/Discord alerts remain responsible for runtime problems.

## Configuration

The feature is disabled unless this exists in the host's private shared env:

```text
AFS_EXTERNAL_HEARTBEAT_URL=https://<provider-issued-heartbeat-url>
```

Optional:

```text
AFS_EXTERNAL_HEARTBEAT_MAX_AGE_SECONDS=720
AFS_EXTERNAL_HEARTBEAT_TIMEOUT_SECONDS=10
```

The URL is treated as secret-like operational configuration. Do not commit it,
paste it into issues/PRs, or include it in logs. The script never prints the
configured URL, including on request failure.

Only HTTPS endpoints are accepted. Any external monitor that treats a simple
HTTPS GET as a successful heartbeat can be used.

## Systemd units

- `deploy/systemd/afs-external-heartbeat.service` — read-only oneshot request.
- `deploy/systemd/afs-external-heartbeat.timer` — starts eight minutes after
  boot and then every five minutes.

The service has no restart/deploy/order/broker authority and uses systemd
hardening (`NoNewPrivileges`, read-only system/home views, no private devices,
restricted address families). `PrivateTmp=false` is intentional because the
authoritative watcher state is under the host's `/tmp/afs_watcher`.

## Operator deployment gate

Do not deploy merely because these files exist in the repository. Deployment
requires an explicit operator action after choosing/configuring the external
provider.

Before enabling the timer, validate the configured host state without sending a
heartbeat:

```bash
/root/autonomous-futures-system/.venv/bin/python \
  /root/autonomous-futures-system/ops/external_heartbeat.py --dry-run
```

Then install the reviewed unit files under `/etc/systemd/system/`, run
`systemctl daemon-reload`, and enable the timer only under an explicit
operator GO. Those host actions are intentionally not automated by this PR.

## Failure semantics

- URL absent: exit 0, feature disabled.
- Invalid configuration: exit 2, no request.
- Watcher stale/missing/malformed: exit 3, no request.
- External request failure: exit 4.
- Fresh watcher + successful HTTPS response: exit 0.

A missed external heartbeat is therefore meaningful: either the VPS/network is
down, the watcher stopped producing fresh ticks, or the outbound heartbeat
request is failing. The external provider's grace period should be longer than
the five-minute timer interval and should account for the 12-minute watcher
freshness window.
