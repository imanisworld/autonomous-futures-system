# M2K 15m feed-gap incident — 2026-09-17

Status: **CLOSED defect / DATA_GAP_CONTAMINATED evidence window**

This note records the verified 2026-09-17 M2K collection interruption. It does not grant any trading, broker, paper, or promotion authority.

## Verified facts

Runtime evidence was audited from the VPS service journal and persisted 15-minute BarHistory files.

- Comparison window: **2026-09-17 13:00Z–16:30Z**.
- MNQ, MES, MGC, MCL and MBT each persisted every expected 15-minute timestamp in that window.
- M2K persisted only **13:00, 13:15, 16:15 and 16:30Z**.
- Therefore the M2K BarHistory has **11 missing 15-minute bars, 13:30Z through 16:00Z**. The surrounding persisted-bar boundary is 13:15Z → 16:15Z.
- HTTP webhook cadence was normal at 13:00, 13:15 and 13:30Z (**12 successful POSTs** each).
- At 13:45, 14:00 and 14:15Z, the service saw **11 successful POSTs** each and no 500s. This proves one expected alert delivery path was absent/not arriving, but the server logs do not identify its payload, so the cause of this first phase is **unproven**.
- From **14:30Z through 16:15Z inclusive**, every 15-minute boundary had **11 successful POSTs plus 4 HTTP 500 retries**.
- The 500 traceback is deterministic: the re-created TradingView alert supplied a non-ASCII webhook secret, and the old release called `hmac.compare_digest(provided, secret)` on Python strings. Python raises `TypeError: comparing strings with non-ASCII characters is not supported`.
- Normal **12-success** webhook cadence resumed at **16:30Z**.
- The futures service remained up; the failure occurred during webhook authentication before the alert entered normal processing.
- M2K is collection-only. It cannot reach DecisionEngine, RiskEngine, PaperBroker or Tradovate through this route, so this incident did **not** create an execution path or trading-state mutation.

## Root-cause split

Two phases must remain separate:

1. **Pre-500 missing-delivery phase** — M2K evidence begins missing at 13:30Z, while the server does not begin returning 500s until 14:30Z. The 11-success cadence starting at 13:45Z proves an expected alert path was absent/not arriving. The available server-side evidence does **not** prove whether the cause was TradingView alert deletion/recreation, alert disablement, client-side configuration, or another upstream transport issue.
2. **Malformed-secret phase** — 14:30Z–16:15Z is proven. The re-created alert repeatedly reached the VPS with a non-ASCII secret, authentication raised before processing, TradingView retried four times per 15-minute delivery, and each attempt returned 500.

Do not collapse both phases into one root cause.

## Fix status

The webhook-authentication defect is **fixed** in current repository code and in the audited deployed release `94eb7d388c02b744eed5a3d3d36b14fa724f1781`:

- non-ASCII supplied credentials fail closed as HTTP **401**;
- `compare_digest` operates on UTF-8 bytes, so malformed input cannot raise the prior TypeError;
- regression coverage asserts that a non-ASCII body secret returns 401 and queues nothing.

No restart or deployment was required during this audit.

## Evidence ruling

The M2K interval containing the missing bars is **DATA_GAP_CONTAMINATED**.

Do not:

- invent or backfill bars from later OHLC;
- treat freshness after 16:30Z as proof that the missing interval was complete;
- use detector windows or hypothetical outcome paths that cross the missing interval for validation/promotion;
- attribute the entire 13:30Z–16:00Z gap to the malformed-secret bug.

Any future completeness gate should mark candidate/resolution windows crossing this interval as contaminated and exclude them from readiness counts.

## Evidence sources

Read-only VPS sources used:

- `/root/afs-shared/logs/bars_M2K_2026-09-17.jsonl`
- peer 15-minute BarHistory files for MNQ/MES/MGC/MCL/MBT
- `journalctl -u futures-bot.service` for 2026-09-17 13:00Z–16:30Z
- current/deployed `webhook/app.py` authentication guard

Repository regression reference:

- `tests/test_webhook_secret_non_ascii.py`

Rule: **No proof, no run.**
