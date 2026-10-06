# Options setup-capture observer (`capture-v0.2`)

Observation-only collector that persists **WATCHING** from completed Strat
structure **before** the next RTH open, then classifies the first RTH break
from IEX trades (equities) or Public INDEX 1-minute bars (SPX).

This is not a trade lane. It does not select a contract, does not alert, does
not consume risk, does not enable SPXW, and does not change Strat / GEX /
Signa / rating / timeframe-authority / sizing / execution rules.

## Why the scanner was late on Oct. 5

Oct. 5 SPY shadow 9925 / 9933 were `H1_222_CONTINUATION` AHEAD after trigger
`770.0768` / invalidation `769.17`. They are **not** prospective catches.

- Structure was knowable Friday Oct 2 16:00 ET (usable 16:16 after the 960 s
  SIP buffer). Trigger/invalidation are the 15:30-16:00 30m stub treated as a
  1H candle with 9:30 anchoring.
- `evaluate_daily_setup` only emits TRIGGERED after the current candle has
  already broken. Only 3-2 has a WATCH state. Same-direction 2-2-2 is a
  continuation in `daily_strat` and is **CANCELLED** by
  `trigger_time._family_for_break` — that helper is not reused.
- The 5-minute full scan (Signa / GEX / chain) was the first persist path.
  Production first sight was 10:16:45. The RTH pre-trigger window was ≤59 s
  (open 769.69, first 1m high 770.09).
- `options_122_prospective_collect` arms only at/after 09:30:00, so a
  09:30:0x cross fails `no_proven_pretrigger_arm`.
- 1H episode buckets use the **scan clock** while candles use `now-960s`, so
  every 1H setup straddles two buckets (9925 and 9933). Replay: 2.00x on 1H,
  1.75x on 4H_RTH. This watcher keys
  `ticker|timeframe|structure_close_ts|pattern` (levels are attributes /
  fingerprint only, so a bar revision emits `SOURCE_DRIFT` instead of a
  duplicate key) and adds
  direction only at TRIGGERED. Existing shadow rows are not rewritten.
- With the 960 s cutoff the current 1H candle only ever contains its first
  30m bar. Second-half-hour crosses and 15:30/12:30 stub **watch** candles are
  never evaluated. This watcher covers the full next hour and those stubs.
- Overnight: 10/12 (1m window) and 309/432 (730d, 71.5%) gapped through at
  the open. Those resolve as `GAP_THROUGH_OPEN`, not a clean trigger catch.
- 22/43 intraday crosses came <16 minutes after structure-ready, so SIP-delayed
  bars cannot arm them. Arming uses completed Public bars with no 960 s delay.

## Architecture

A oneshot systemd timer (`options-setup-capture.timer`), same cadence as
`options-122-prospective` (Mon–Fri 09..16:*:00 America/New_York, including
pre-open 09:00–09:29 and 16:xx). **Not** an APScheduler job inside the
scanner event loop (Signa `POST /signa/context/pull` already blocks that
loop; Oct 5 13:31:46Z missed a coalesced run).

```
completed Public 30m → rebuild 1H (9:30 + stub) / Daily
        → WATCHING (two-sided pending 2-2) persisted before next RTH open
        → watch window = next RTH candle (AH / premarket ignored)
        → IEX first-boundary (equities) or INDEX 1m high/low (SPX)
        → SIP reconcile: lag vs SIP cross; IEX miss + SIP hit = MISSED_LATE
        → GAP_THROUGH_OPEN if the opening print is already through
```

Journal: append-only fsynced JSONL at
`/root/afs-shared/logs/options_setup_capture.jsonl` (absolute, outside the
release tree, `ProtectSystem=strict` / `ReadWritePaths=/root/afs-shared/logs`).
Replay via `_load_state`, flock, torn-tail repair, version-prefix tolerant.

**Integrity flag:** the 122 unit `WorkingDirectory` is
`/root/autonomous-futures-system`, not the integrity-pinned
`/root/afs-releases/<sha>` path. This watcher follows that checkout for code
but keeps state on the shared log volume.

## Classification

| Status | Meaning | Catch? |
|---|---|---|
| WATCHING → TRIGGERED, persisted_at < SIP cross, lag ≤ 120 s | Prospective plumbing catch | Yes (plumbing only) |
| TRIGGERED + capture_late | Had WATCHING; detection lag vs SIP > 120 s | No |
| MISSED_LATE | First sight after the trigger, or IEX no-cross + SIP cross | No |
| GAP_THROUGH_OPEN | Opening print already through; first print + geometry stored | No |
| EXPIRED / NO_TRIGGER | Window ended with no IEX and no SIP cross | No |
| DATA_BLOCKED | Clock skew, unknown condition, source error (journalled, not stdout) | No |

`AHEAD` in `paper_v1` still only means between stop and target. Late first
sight is `MISSED_LATE` here.

## SPX / SPXW

- Watcher universe is **SPY / QQQ / SPX** in code. Never
  `OPTIONS_SCANNER_WATCHLIST` (that path is paper_v1 + Discord + shadow/marks).
- Structure bars: Public `historicdata/INDEX/SPX` via the guarded GET helper
  (historicdata prefix allowlisted; not a raw `_ensure_client` bearer call).
- Trigger: Public INDEX `DAY/ONE_MINUTE` high/low, resolution `BAR`
  (`crossed_window=[bar_start, bar_start+60s)`). Never IEX. Never Alpaca
  stocks (unknown symbols are silently dropped). Real-time vs delayed is
  **unverified**; if newest complete bar age > 120 s the row is labeled
  `data_delayed` and is not a prospective catch.
- SPXW execution remains impossible (`LIVE_OPTIONS_TRADING_ENABLED` lock,
  `FORBIDDEN_PATH_PARTS`, `order_supported=False`, RH `submit_order` stub,
  `OPTIONS_COMPANION_ENABLED=false`, #1069 unmerged).

## Known holes (out of scope)

- `POST /webhook/alert` accepts any ticker into `scan_ticker` (shadow, marks,
  Discord eligibility) with no allowlist. Flagged; not fixed here.
- Scanner logs show outside IPs getting 200 on `GET /shadow-journal` (2026-10-06).
  Ops should confirm the nginx auth gate from the 2026-09-21 public-surface
  audit is still in place. Not used by this watcher.
- Signa symbol map still sends SPX → SPY. Watcher rows carry no Signa fields.
- `_public_chart` in the 122/212r collectors still bypasses the read-only path
  guard. This watcher does not use that helper.

## Operator surfaces

- `GET /setup-capture` — journal counts (scanner-embedded = false); surfaces
  `clock_unsynced` when the collector is blocked by clock skew
- `python -m scripts.options_setup_capture_status`
- systemd `options-setup-capture.timer`

## Timer-install prerequisites (separate operator GO after merge)

- **Clock sync with a measurable offset:** prefer `systemd-timesyncd` so
  `timedatectl timesync-status` reports `Offset:`. On hosts that use chrony
  instead, `chronyc tracking` (`Last offset`) is accepted. If neither yields
  an offset, every oneshot run ends `DATA_BLOCKED clock_unsynced` and status
  counts alone will not explain empty watches until `/setup-capture` is read.
- Public INDEX real-time vs delayed entitlement for SPX remains UNVERIFIED.
- Do not enable the timer without an explicit install GO.

Merge and deploy remain **operator GO**. Do not fabricate a live trigger if
RTH has no setup.
