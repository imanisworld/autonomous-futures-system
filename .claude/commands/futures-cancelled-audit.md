# /futures-cancelled-audit

Purpose:
Independently verify plain (non-reconciler-touched) CANCELLED no-fill rows in the live journal. The 2026-07-07 phantom-clear audit and `docs/proof-operator-overrides.md` only re-verified CANCELLED rows produced by the reconciler bug path. The much larger set of CANCELLED rows produced by the normal IOC-limit-expired path has never been independently checked — they are trusted purely because the journal labels them CANCELLED. This command closes that gap.

Core rule: No proof, no run. A CANCELLED row is not "confirmed no-fill" because the label says so — it is confirmed no-fill because the decision-bar close was independently checked against the live IOC cap and found genuinely unmarketable.

Required inputs:
- A journal directory (local copy or live box `logs/` dir over SSH — read-only)
- `ops/build_honest_baseline.py`'s `OVERRIDES` map, to exclude rows already independently reconstructed by the phantom-clear audit (do not re-audit those here)

Required checks, run via `ops/audit_plain_cancelled.py`:
- For every resolved TRADE↔OUTCOME pair classified `cancelled_nofill` by `ops.proof_30_mnq.classify_outcome` that is NOT a key in `OVERRIDES`:
  - Decision-bar close (`TRADE.context.close`) vs. the live IOC cap (`entry ± ENTRY_SLIPPAGE_TOLERANCE_TICKS_{MES,MNQ} * 0.25pt`, direction-aware)
  - Whether `order_ids` were ever journaled (partial-fill evidence, if present)
  - `no_fill_reason` / `order_type` fields, if the row postdates the no-fill taxonomy deploy (`3c7a6b044cc8`, 2026-07-07T18:35:33Z)

Classification (pick exactly one per row):
- `CONFIRMED_NO_FILL` — decision close was beyond the IOC cap by more than 2 ticks (0.5pt); the CANCELLED label is honest
- `CONFIRMED_NO_FILL_MARGINAL` — beyond the cap, but by 2 ticks or less; correct classification, low margin for error
- `MISLABELED_FILL_SUSPECT` — decision close was WITHIN the IOC cap (should have been marketable) but was logged CANCELLED anyway; this is NOT explained by the already-fixed reconciler bug and needs its own investigation before being explained away
- `PARTIAL_FILL_UNRESOLVED` — order_ids present but resolution to a filled/no-fill outcome is ambiguous
- `BROKER_STATUS_UNCLEAR` — no order_ids and no way to cross-check broker state for this date
- `DATA_GAP_EXCLUDED` — missing entry, decision-bar close, or direction; cannot be classified at all
- `PRE_TAXONOMY_UNVERIFIABLE` — row predates the no-fill taxonomy deploy AND the marketability arithmetic alone cannot resolve it (e.g. a suspect/marginal case with no finer-resolution price data available for that date)

Forbidden actions:
- Do not place trades.
- Do not modify journals, execution code, or risk code.
- Do not silently reclassify a `MISLABELED_FILL_SUSPECT` row as `CONFIRMED_NO_FILL` to close it out — if the arithmetic says marketable, it stays flagged until independently explained (e.g. finer-resolution price data, a broker record, or a specific documented code-path reason).
- Do not commit any generated report containing strategy names or per-strategy figures to this public repo — write it to `private/` (gitignored) instead, per the same rule applied to `docs/honest-baseline-2026-07-07.md`.
- Do not treat a single `MISLABELED_FILL_SUSPECT` finding as proof of a new bug without attempting the same reconstruction rigor used in the original phantom-clear audit (forward bar walk, order-id cross-check, finer-resolution data if available) — but if that reconstruction hits a hard data wall (no finer data, no broker record), report it as `PRE_TAXONOMY_UNVERIFIABLE`, not as either "confirmed bug" or "confirmed fine."

Required output format:

VERDICT: CLEAN / SUSPECT_FOUND / DATA_GAPS_ONLY
INSTRUMENT:
PLAIN CANCELLED TOTAL:
CONFIRMED NO-FILL:
CONFIRMED NO-FILL (MARGINAL):
MISLABELED FILL SUSPECT: (list each with trade_ts, strategy, margin_points)
PARTIAL FILL UNRESOLVED:
BROKER STATUS UNCLEAR:
DATA GAP EXCLUDED:
PRE-TAXONOMY UNVERIFIABLE:
NEXT STEP:

Safety gates:
- Any `MISLABELED_FILL_SUSPECT` row caps the overall VERDICT at `SUSPECT_FOUND` regardless of how many other rows are clean — one real anomaly is not diluted by a large clean sample.
- A `MISLABELED_FILL_SUSPECT` with margin ≤ 1 tick (0.25pt) should be reported with a note that it may be a rounding/comparison-operator artifact rather than a real anomaly, but must still be listed, not dropped.
- Zero prior findings from a previous run of this command do not retroactively clear a newly-added row from a fresh journal pull — this command has no memory of "already checked" beyond the `OVERRIDES` map; re-running it after new trades land should re-scan everything not yet in that map.

Safe next step:
If VERDICT is `SUSPECT_FOUND`, the next step is the same reconstruction discipline used for the 2026-07-07 phantom-clear audit — pull finer-resolution data if available, check for a broker order id, and only add a ruling to `docs/proof-operator-overrides.md` once the case is genuinely resolved. Do not fold an unresolved suspect row into the honest baseline as either a win, a loss, or a confirmed no-fill.
