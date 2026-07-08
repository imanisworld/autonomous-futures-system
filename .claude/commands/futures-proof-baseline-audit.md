# /futures-proof-baseline-audit

Purpose:
Audit whether the futures proof/measurement pipeline itself can be trusted, before its numbers are used to judge strategy edge, fills, or risk. This is the trust layer underneath `/futures-strategy-audit` and `/futures-fill-audit` — if this audit is not clean, their conclusions inherit the same doubt.

Core rule: No proof, no trust. If the filled-trade count, net P&L, or reconciler-row accounting cannot be independently reproduced from `ops/build_honest_baseline.py` against the current journals, the verdict is HOLD, not APPROVE — a number that "sounds right" is not proof.

Required checks:
- `ops/build_honest_baseline.py` — re-run it against current journals, do not trust a cached/remembered number
- `ops/proof_30_mnq.py` — `classify_outcome`, `pair_resolved_trades`, `read_journal_entries` still behave as documented; no silent signature/behavior drift since the last audit
- `docs/proof-operator-overrides.md` — every override still has a resolved operator ruling (no `(pending)` placeholders); every override key matches an exact trade timestamp, not a date/strategy guess
- Filled W/L count and net P&L per instrument, reproduced fresh
- Cancelled/no-fill count per instrument, and whether it is fully accounted for or still contains unaudited "plain" CANCELLED rows (see `/futures-cancelled-audit`)
- Unresolved/excluded count and why each is excluded (a genuine data gap, not a guess)
- Reconciler-touched row count: confirm `still_unclassified_reconciler_touched_count` is 0 for both instruments, or list exactly which rows remain unclassified
- Unmatched OUTCOME rows (no preceding TRADE) — confirm each is either resolved or explicitly a known benign artifact (e.g. a startup test payload), not silently ignored
- Corrupt/unparseable journal rows (`READ_ERROR` entries from `read_journal_entries`)
- Pre-taxonomy vs post-taxonomy boundary: confirm the no-fill taxonomy deploy timestamp used (`3c7a6b044cc8`, 2026-07-07T18:35:33Z) is still correct, and report how many resolved pairs fall on each side
- Whether the current honest baseline (30 filled trades, 11W/19L, net -$67.85 as of 2026-07-07) has changed since new journal data landed, and by how much

Forbidden actions:
- Do not edit any journal file.
- Do not modify `ops/build_honest_baseline.py`, `ops/proof_30_mnq.py`, or `docs/proof-operator-overrides.md` as part of this audit — this is a read-only check, not a fix.
- Do not commit or push.
- Do not treat a cached/remembered baseline number as current without re-running the tool.
- Do not report a count as final while any reconciler-touched or plain-CANCELLED row remains unclassified — report the gap instead.
- Do not generate or commit a report containing strategy names or per-strategy P&L to this public repo — write it to `private/` (gitignored) per the established rule.

Required output format:

Verdict:
APPROVE / REJECT / HOLD / AUDIT ONLY

Proof State Classification:
TRUSTED / PARTIALLY TRUSTED / NOT TRUSTED / INCONCLUSIVE

Why:
2-5 decisive reasons.

What I Verified:
- ops/build_honest_baseline.py re-run against current journals
- docs/proof-operator-overrides.md rulings checked for pending placeholders
- reconciler-touched row accounting checked (0 unclassified or listed)
- plain-CANCELLED audit status checked (see /futures-cancelled-audit)
- corrupt/unmatched rows checked
- pre/post no-fill-taxonomy boundary checked

Problems Found:
Separate:
- blockers
- warnings
- minor cleanup

Required Fixes:
- must-fix before trusting the baseline
- should-fix later
- do-not-touch items

Safe Next Step:
Smallest safe action only.

Safety gates:
- Any `(pending)` operator-ruling placeholder in `docs/proof-operator-overrides.md` caps the verdict at HOLD.
- Any unclassified reconciler-touched row caps the verdict at HOLD.
- A materially outstanding plain-CANCELLED audit (per `/futures-cancelled-audit`) caps the Proof State Classification at PARTIALLY TRUSTED, not TRUSTED, even if the reconciler-row accounting is clean.
- Any corrupt/unparseable journal row caps the verdict at HOLD until explained.
- A number that cannot be reproduced by re-running the tool (only asserted from memory or a prior conversation) is INCONCLUSIVE, not APPROVE.

Safe next step:
If TRUSTED (or PARTIALLY TRUSTED with the gap explicitly named), the safe next step is to proceed to `/futures-strategy-audit` and `/futures-fill-audit` using this baseline, citing this audit's timestamp. If NOT TRUSTED or INCONCLUSIVE, the safe next step is always to close the specific gap named above — never to proceed to strategy/fill conclusions on an unverified baseline.
