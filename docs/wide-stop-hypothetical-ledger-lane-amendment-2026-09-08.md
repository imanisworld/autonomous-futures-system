# Wide-stop hypothetical-ledger lane — 4HR risk amendment

Status: **APPROVED PAPER-LANE AMENDMENT — 2026-09-08**

This document amends only the active `wide_stop_4k` / 4HR MNQ risk values in
`docs/wide-stop-hypothetical-ledger-lane-spec-2026-09-07.md`. Where that spec
still states 400 ticks, $200 worst-case risk, a $400 lane daily-loss floor, or
the 36-trade 400-tick evidence cell for the active 4HR configuration, this
amendment supersedes those values. The old numbers remain historical evidence,
not the active paper-lane contract.

No global or live-book risk rule is changed. `risk_rules.yaml`, the real
$1,500 book's `max_daily_loss`, broker routing, execution mode, strategy logic,
and the `wide_stop_6k` ledger are unchanged. The lane remains hypothetical,
paper-only, one contract, with no promotion path.

## Active 4HR paper-lane contract

| Control | Active value |
|---|---:|
| Ledger | `wide_stop_4k` |
| Instrument / strategy | MNQ / `strat_4hr_retrigger` |
| Contracts | 1 |
| Max stop | **300 ticks** |
| Max dollars at stop | **$150** |
| Minimum R:R | **1.0** |
| Lane daily-loss floor | **$300** |
| Max drawdown | 20% |
| IOC marketable tolerance | 8 ticks |
| Entry reference | completed decision-bar close |
| Exit | documented static bracket; no runner |

The $300 daily-loss floor preserves the approved D2 invariant of two
worst-case stopped trades before the lane halts: 2 × $150 = $300. It is a
lane-scoped hypothetical control only. The global real-book daily-loss rule
remains unchanged.

## Replacement historical evidence cell

The replacement cell was recomputed from the committed
`scripts/edge_decomposition_audit_results_candidates.jsonl.gz` artifact. The
same calculation reproduced the prior 400-tick baseline before the 300-tick
result was accepted.

| Metric | Prior 400-tick cell | Active 300-tick cell |
|---|---:|---:|
| Admitted / resolved | 36 | **32** |
| Net P&L | +$3,076.72 | **+$1,901.14** |
| Net profit factor | 3.125276 | **2.313232** |
| Wins / losses | 20 / 16 | **16 / 16** |
| H1 net | +$1,433.86 | **+$1,164.82** |
| H2 net | +$1,642.86 | **+$736.32** |
| Losses worse than $150 | 2 | **2** |

The tighter cap reduces the historical edge but does not erase it: both
chronological halves remain positive. This is historical bracket evidence,
not forward validation.

## 8-tick IOC admission at the active cap

Using the production `ioc_limit` entry model at the frozen 8-tick contract:

- 32 candidates admitted by the 300-tick / R:R >= 1.0 cell;
- **11 valid IOC fills**;
- **19 unmarketable**;
- **2 invalid-at-fill rejections**, on 2026-01-08 and 2026-04-06;
- valid-fill rate **34.4%**;
- median detachment 51 ticks; maximum detachment 247 ticks.

The invalid-at-fill rows stay separate and are not blended into valid fills or
forward P&L.

## Disposition

**PROMISING BUT UNPROVEN — PAPER ONLY.**

The 300-tick cap is approved as a risk reduction for the isolated 4HR
hypothetical ledger. It does not validate the strategy for the real book and
does not authorize live execution. Forward IOC-real results must still prove
the strategy under this amended contract.
