# /futures-cancelled-audit

Purpose:
Verify whether ordinary CANCELLED outcomes are genuine no-fills, not just trusted labels. `docs/proof-operator-overrides.md`'s phantom-clear audit only re-verified CANCELLED rows produced by the reconciler bug. The much larger set of "plain" CANCELLED rows — produced by the normal IOC-limit-expired path, never touched by the reconciler — must be checked here. This is the missing bridge between `/futures-proof-baseline-audit` (is the count trustworthy) and `/futures-fill-audit` (are fills killing edge).

Core rule: No proof, no trust. A CANCELLED row is not confirmed no-fill because the journal label says so — only because the decision-bar close was independently checked against the live IOC cap and found genuinely unmarketable.

Required checks, via `ops/audit_plain_cancelled.py` (excludes rows already in `ops/build_honest_baseline.py`'s `OVERRIDES` map — those are the reconciler audit's domain, not this one):
- Trade timestamp, instrument, strategy, requested entry, direction
- Live IOC cap / marketability rule (`entry ± ENTRY_SLIPPAGE_TOLERANCE_TICKS_{MES,MNQ} * 0.25pt`, direction-aware)
- Decision-bar close (`TRADE.context.close`) vs. that cap
- Forward bars, if the row is flagged suspect and finer reconstruction is attempted
- Broker order id, if ever journaled (`order_ids` presence — absence is itself a finding, matching the known 2026-07-01 ORDER_IDS gap)
- Broker final status, if independently checkable (rare for historical dates — demo account history does not persist indefinitely)
- Partial-fill evidence
- Matching OUTCOME row (confirm pairing, not just presence)
- `no_fill_reason` / `order_type`, if the row postdates the no-fill taxonomy deploy (`3c7a6b044cc8`, 2026-07-07T18:35:33Z)
- `signal_to_submit_latency_seconds` (auto-computed by the tool from `signal_timestamp`/`submit_timestamp`, when both are present)
- `option_c_recurrence` (auto-flagged by the tool: a `MISLABELED_FILL_SUSPECT` row that postdates the taxonomy deploy AND lacks a credible explanation — `no_fill_reason` absent, `NO_FILL_UNKNOWN`, or `broker_status_raw` absent even if `no_fill_reason` names a specific bucket — the exact signature from the 2026-06-25 MNQ `pdh_reclaim` anomaly, which the operator is watching for rather than resolving historically. A specific bucket backed by a real `broker_status_raw` does NOT trip this flag.)

Classification (pick exactly one per row):
- `CONFIRMED_NO_FILL` — decision close was beyond the IOC cap; the CANCELLED label is honest
- `MISLABELED_FILL` — decision close was WITHIN the IOC cap (should have been marketable) but was logged CANCELLED anyway; not explained by the already-fixed reconciler bug; needs its own investigation before being explained away
- `PARTIAL_FILL_UNRESOLVED` — order_ids present but resolution to a filled/no-fill outcome is ambiguous
- `BROKER_STATUS_UNCLEAR` — no order_ids and no way to independently cross-check broker state for this date
- `DATA_GAP_EXCLUDED` — missing entry, decision-bar close, or direction; cannot be classified at all
- `PRE_TAXONOMY_UNVERIFIABLE` — row predates the no-fill taxonomy deploy AND marketability arithmetic alone cannot resolve it (e.g. a suspect/marginal case with no finer-resolution price data available for that date)

Forbidden actions:
- Do not place trades.
- Do not modify journals, execution code, or risk code.
- Do not silently reclassify a `MISLABELED_FILL` row as `CONFIRMED_NO_FILL` to close it out — if the arithmetic says marketable, it stays flagged until independently explained (finer-resolution data, a broker record, or a specific documented code-path reason).
- Do not treat cancelled/no-fill stats as final while unaudited plain CANCELLED rows remain material — report the gap instead of rounding it away.
- Do not commit any generated report containing strategy names or per-strategy figures to this public repo — write it to `private/` (gitignored).
- Do not treat a single `MISLABELED_FILL` finding as proof of a new bug without attempting the same reconstruction rigor used in the phantom-clear audit — but if that reconstruction hits a hard data wall, report `PRE_TAXONOMY_UNVERIFIABLE`, not "confirmed bug" or "confirmed fine."

Required output format:

Verdict:
APPROVE / REJECT / HOLD / AUDIT ONLY

Cancelled-Row Classification:
CLEAN / SUSPECT_FOUND / DATA_GAPS_ONLY

Why:
2-5 decisive reasons.

What I Verified:
- plain CANCELLED rows enumerated (excluding OVERRIDES-covered rows)
- marketability arithmetic checked per row
- order-id / broker-status availability checked
- no_fill_reason/order_type checked where post-taxonomy
- classification counts totaled and reconciled against the plain-CANCELLED total

Problems Found:
Separate:
- blockers (any MISLABELED_FILL or PARTIAL_FILL_UNRESOLVED row)
- warnings (BROKER_STATUS_UNCLEAR, PRE_TAXONOMY_UNVERIFIABLE rows)
- minor cleanup (DATA_GAP_EXCLUDED rows)

Required Fixes:
- must-fix before treating no-fill stats as final
- should-fix later
- do-not-touch items

Safe Next Step:
Smallest safe action only.

Safety gates:
- Any `MISLABELED_FILL` row caps the verdict at HOLD and the classification at `SUSPECT_FOUND`, regardless of how many other rows are clean — one real anomaly is not diluted by a large clean sample.
- A `MISLABELED_FILL` with margin ≤ 1 tick (0.25pt) must still be listed, with a note that it may be a rounding/comparison-operator artifact rather than a real anomaly — it is not dropped.
- Re-running this command after new journal data lands must re-scan everything not yet in the `OVERRIDES` map — a prior clean run does not grandfather in new rows.
- Any row with `option_c_recurrence: true` caps the verdict at HOLD regardless of sample size — this is the specific historical anomaly signature recurring post-taxonomy, not a generic suspect row, and it directly answers the open question in the MNQ 2026-06-25 `pdh_reclaim` thread. Report it explicitly, do not fold it into the generic `SUSPECT_FOUND` count without calling it out by name.

Safe next step:
If `CLEAN`, the safe next step is to feed the confirmed no-fill count into `/futures-proof-baseline-audit` and `/futures-fill-audit`. If `SUSPECT_FOUND`, the safe next step is the same reconstruction discipline used for the phantom-clear audit (finer-resolution data, broker order id check) — and if that hits a hard data wall, document it as `PRE_TAXONOMY_UNVERIFIABLE` in `private/` and surface the open question to the operator rather than resolving it either direction by assumption.
