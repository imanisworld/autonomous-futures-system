# Futures Operator Reader

Read this first when picking up the futures system.

## Current source of truth

1. `docs/futures-current-status-2026-09-20.md` — concise current operator status.
2. `docs/futures-current-state-handoff.md` — long provenance handoff; historical sections are retained, but the current-status pointer at the top governs when older text conflicts.
3. `docs/strategy-rules/Strategy_Inventory.md` — strategy classifications and evidence status.
4. Deployment state must still be verified on the box. Repository `main` is not proof of what the VPS is running.

## Current runtime posture

- Live execution is disabled.
- Tradovate is DEMO.
- Schedule mode is `always_on_shadow`.
- Max contracts hard cap is 1.
- Current deployed release after the 2026-09-20 Signa v2 pass is `a02320268e26a05f56b159f03d3cf441e776ef46`.
- The system is collecting paper/shadow/guarded-demo evidence only.

## Important 2026-09-20 changes

### 4HR continuation treatment tag

The system records a read-only `4hr_prearmed_4h22_continuation_v1` treatment tag on natural 4HR 1m observer events. It does not trade or paper-fill the treatment.

Use it later to compare:

- broad 4HR control;
- 4HR subset whose latest completed ET-wall-clock 4H sequence is `strat_22_continuation`.

Do not treat this as a promoted strategy. Historical concentration is still too high.

### Shared Signa snapshot path

Raw Signa responses are stored in `signa_snapshots` and can be referenced by both options and futures using the same `snapshot_id`.

Current flow:

```text
Options Signa pull
→ signa_snapshots
→ options_signa_context references snapshot_id
→ futures context can reuse same snapshot_id
```

This is evidence infrastructure only. It does not create trade authority.

### Futures Signa Context Lane v2

The system records `context.signa_futures_context` on futures journal rows.

Use it later to segment trades by context:

- aligned;
- conflicting;
- mixed;
- missing;
- stale;
- error/partial;
- shared `snapshot_id` when a fresh snapshot exists.

Do not use Signa to enter, block, rank, resize, or route futures trades.

## Current safe next action

Seed or verify the shared snapshot store with a controlled read-only Signa pull for:

```text
QQQ, SPY, IWM, DIA, TLT, VIX, GLD, USO, XLE
```

Then audit the first future natural futures setup that includes both:

- the 4H continuation treatment metadata when applicable;
- the Signa futures context v2 record with `snapshot_ids`, `snapshot_refs`, and `snapshot_status` when fresh shared snapshots exist.

The audit question is not “did it win?” The question is whether the record proves causal timing, proper metadata, and zero execution-authority leakage.

## Standing rule

No proof, no run.
