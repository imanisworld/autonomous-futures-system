# 3-2-2 #1057-corrected EOD resolver rerun — 2026-10-04

Status: **CORRECTED EVIDENCE / PROMISING BUT UNPROVEN / AUDIT ONLY**

Trial: `T-2026-10-04-prereg-322-corrected-eod-rerun-2026-10-04-01`
Prereg: `docs/prereg-322-corrected-eod-rerun-2026-10-04.md`
Result artifact:
`docs/research-evidence/T-2026-10-04-prereg-322-corrected-eod-rerun-2026-10-04-01/result.json`
Supersedes as current headline: the 2026-09-18 pre-armed aggregate in
`docs/322-trigger-timing-ab-2026-09-18.md`, which `docs/322-eod-resolver-erratum-2026-09-28.md`
placed under erratum. Both earlier documents are retained unchanged as provenance.

No runtime, strategy, risk, broker, collector, env, deployment, or VPS state was
touched. No Polygon/Massive fetch. The frozen harness file was not edited.

## What was run

- `scripts/322_trigger_timing_ab_2026_09_18.py` exactly as on `main f38acde`
  (content commit `6d05a62`), importing `run_bracket_stage` →
  `resolve_bracket` from `scripts/edge_decomposition_audit.py` at the #1057 fix
  (`264d522`).
- Same 34 candidates, re-derived by the harness's research-detector vs
  state-machine cross-check (17 LONG / 17 SHORT, 2024-08-02 → 2026-06-11,
  walk-forward boundary 2025-05-01).
- Same corpora, byte-identical to the archived run:
  `data/replay_corpus_v1_5m_4hr_audit/MNQ` tree SHA-256 `7f09a7f8…06afd35`,
  `data/replay_polygon/MNQ` tree SHA-256 `a8ec026d…bc93a21`.
- Same accounting: 1 contract, $2.00/pt, $1.48 round trip, 1/2/3 adverse
  ticks on entry and exit, stop-first on same-bar ambiguity, exact 15:55 ET bar
  or `UNRESOLVED / EOD_BAR_MISSING`.
- Environment: `MAX_CONTRACTS_HARD_CAP=1` (required by PaperBroker since
  #1053; harness trades `contracts=1`). Plumbing only.
- The harness's `check_repro` gate is pinned to the pre-#1057 plan/IOC 1-tick
  totals and therefore trips on the corrected resolver (`plan net 2293.64 !=
  2532.66`). The rerun recorded the gate delta instead of raising, through a
  throwaway wrapper that replaced only that function. The population-parity
  abort was left in force and passed.

## Which rows changed because of #1057

Exactly **one** candidate changed, in every model and at every slippage level:

| Date | Dir | Trigger | Stop | Target | Archived outcome | Corrected outcome |
|---|---|---:|---:|---:|---|---|
| 2025-01-20 (MLK Day, 13:00 ET early close) | SHORT | 21658.50 | 21780.00 | 21538.00 | `TARGET_HIT` at **19:50 ET** (`2025-01-21T00:50Z`), net +$238.02 at 3 ticks | `UNRESOLVED / EOD_BAR_MISSING` |

Corpus proof: `MNQ_2025-01-20.jsonl` has no 15:55 ET bar; last bar before
16:00 ET is 12:55 ET; next bar is 19:00 ET. The archived resolver walked into
the same-date evening Globex session and found the target there. That is the
defect #1057 closes.

All other 33 candidates × 3 models × 3 slippage levels are byte-identical to
the archived ledger (status, fill, exit, gross, net, bars held). This also
proves nothing else in the PaperBroker / state-machine / corpus chain drifted
between 2026-09-18 and this rerun.

Bound on what the fail-closed exclusion hides (informational, not part of the
frozen contract): at the actual 12:55 ET close the position was 23 pts
underwater with neither stop nor target touched in RTH; flattened there at
3 ticks it would have been **−$48.48**. The defect converted a small
day-only loss into a +$238 evening-session win.

## Corrected results — causal pre-armed First Live (model C)

| Slippage | Filled | Resolved | W-L | Net | PF | Exp/fill | H1 (n=16) | H2 (n=16) | Max DD |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 tick | 33 | 32 | 32-0 | **+$2,503.64** | ∞ | $78.24 | +$1,144.32 | +$1,359.32 | $0 |
| 2 ticks | 33 | 32 | 32-0 | **+$2,487.64** | ∞ | $77.74 | +$1,136.32 | +$1,351.32 | $0 |
| 3 ticks | 33 | 32 | 32-0 | **+$2,471.64** | ∞ | $77.24 | +$1,128.32 | +$1,343.32 | $0 |

At 3 ticks:
- candidates 34; attempted 34; filled 33; no-fill 1
  (`ENTRY_BRACKET_INVALID_AT_FILL`, 2026-05-12 SHORT, unchanged);
  `EOD_BAR_MISSING` 1 (2025-01-20, new); resolved 32;
- wins 32 / losses 0 / flats 0; exits: 31 `TARGET_HIT`, 1 `DAY_ONLY_FLATTEN`
  (positive), 0 `STOP_HIT`;
- expectancy per candidate $72.70; top-5 concentration 42.2%;
- LONG 17 resolved +$1,754.34 (unchanged); SHORT 15 resolved +$717.30
  (archived 16 / +$955.32);
- same-trigger-bar resolutions 12, zero touching both stop and target
  (unchanged);
- both chronological halves positive.

Exact delta from the invalidated archived headline (3 ticks):
net **+$2,709.66 → +$2,471.64 (−$238.02)**; resolved 33 → 32; wins 33 → 32;
H1 +$1,366.34 → +$1,128.32 (−$238.02); H2 unchanged +$1,343.32;
expectancy/fill $82.11 → $77.24; PF ∞ → ∞; max DD $0 → $0.

Conservative reading that counts 2025-01-20 as the −$48.48 flatten loss instead
of excluding it: 3-tick net +$2,423.16, 32W / 1L. Still positive in both
halves.

## Corrected legacy models (provenance only)

| Model | Slip | Filled | Resolved | W-L | Net | PF | H1 | H2 | Max DD |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Plan/backfill | 1 | 33 | 32 | 31-1 | +$2,293.64 | 12.38 | +$1,144.32 | +$1,149.32 | $201.48 |
| Plan/backfill | 3 | 33 | 32 | 31-1 | +$2,260.64 | 12.11 | +$1,128.32 | +$1,132.32 | $203.48 |
| Completed-5m IOC32 | 1 | 20 | 19 | 18-1 | +$1,622.38 | 10.75 | +$831.66 | +$790.72 | $166.48 |
| Completed-5m IOC32 | 3 | 20 | 19 | 18-1 | +$1,602.38 | 10.51 | +$823.66 | +$778.72 | $168.48 |

The 2026-09-18 reproduction pins (+$2,532.66 / +$1,859.40) are therefore
superseded by +$2,293.64 / +$1,622.38 at 1 tick. The frozen harness still
carries the old pins; it was deliberately not edited under this trial.

## Answer to the authorized question

**Does 3-2-2 remain positive after correcting #1057, and does that positivity
survive 3-tick adverse execution stress?**

Yes on both counts, on this frozen 34-candidate population: +$2,471.64 at
3 ticks, H1 and H2 both positive, 32 resolved wins / 0 losses, 0 stop hits.
The entire correction is one row worth −$238.02 and lands in H1. The
2026-09-18 timing conclusion (causal pre-armed First Live ≥ completed-5m IOC)
is unchanged in direction.

## Classification

**PROMISING BUT UNPROVEN.**

Not `VALIDATED`, and the result does not move toward it:
- n = 34 historical candidates, 32 resolved, consumed evidence;
- 32-0 resolved with zero stop hits is an extreme small-sample flag, not a
  trust signal; the only historical loss in the family appears under the
  less faithful legacy models;
- every candidate still violates the real-account stop-width / R:R
  architecture; the $6,000 real-equity restriction, `RISK_GATES_REMOVE_EDGE`,
  and the no-promotion posture are unchanged;
- prospective confirmation is still zero: the 1m 3-2-2 observer has 0 arms /
  0 touches as of 2026-10-02.

## Operational ruling

- Strategy evidence classification: **PROMISING BUT UNPROVEN** (unchanged).
- Current-account execution status: **HOLD / PARKED** (unchanged).
- Live, demo, or paper-fill authority: **NOT CREATED**.
- Global system HOLD: **unchanged**; only the research-rerun HOLD was lifted,
  and it is now consumed.

## Proposed authoritative-record update (not applied by this trial)

For the reconciliation role to apply to
`docs/strategy-rules/Strategy_Inventory.md` row "60M 3-2-2 First Live" and the
family-2 note: replace "exact corrected aggregate pending rerun" with
"corrected 2026-10-04 (T-2026-10-04-prereg-322-corrected-eod-rerun-2026-10-04-01):
pre-armed +$2,471.64 at 3 ticks, 32 resolved / 32-0, H1 +$1,128.32 / H2
+$1,343.32, one row (2025-01-20) moved to EOD_BAR_MISSING". Verdict column
unchanged.

NEXT ACTION: none under this trial. The only thing that follows directly from
this result is for the reconciliation role to accept or reject the proposed
Inventory wording above; 3-2-2 then returns to its existing WAIT state for
prospective 1m-observer evidence (0 arms / 0 touches) and the $6k equity gate.
No second rerun, no parameter work, no 4HR or Polygon work under this trial.
