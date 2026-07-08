# /futures-risk-gate-audit

Purpose:
Audit whether futures risk controls actually fired correctly historically — not just whether the code exists. `/futures-execution-safety-audit` checks that the gates are correctly wired in code; this command checks the journal for evidence they behaved as configured. A bot can lose small and safely, or lose because risk is loose — those are different outcomes with the same P&L sign, and only journal evidence distinguishes them.

Core rule: No proof, no trust. No alert without risk proof. A risk gate that exists in code but has never been observed firing (no `RISK_REJECTED` journal rows exercising it) is UNPROVEN, not VALIDATED, regardless of how correct the code looks on read-through.

Important — read `risk_rules.yaml` fresh before auditing, do not assume the gate set from a prior audit or from general futures-risk-engine knowledge:
Several gates were deliberately disabled by an explicit 2026-06-17 operator decision (documented inline in `risk_rules.yaml` as "RIPPED" / "was X — now unlimited/off", reasoning "algo, not human"): `max_trades_per_day` (9999, effectively unlimited), `max_consecutive_losses` (9999), `circuit_breaker_losses` (0, off), `early_session_loss_floor` (0, off), `per_session_limits` ({}, off), `session_cutoffs_et` ({}, off), `news_blackout_mode` ("off"). **These are intentional, not defects.** Reporting them as "missing" or "broken" without checking `risk_rules.yaml`'s own comments first is a false finding.

Required checks:
- `risk_rules.yaml` read fresh for current values — do not rely on a remembered gate list
- `max_daily_loss` (currently 150, the survival floor) — `risk/risk_engine.py::_check_daily_loss_limit` — confirm it has fired in the journal when daily P&L approached the limit, or confirm it has simply never needed to (state which)
- `max_drawdown_percent` (currently 0.20) — `_check_max_drawdown` — same evidence standard
- Max contracts / position sizing — `_check_max_contracts`, `_check_position_sizing` — confirm actual order sizes in the journal never exceeded the configured cap
- Bracket completeness — `_check_bracket_completeness`, `order_rules.require_stop` — confirm every filled trade in the honest baseline has both a stop and a target, no unbracketed fill
- Stop distance sanity — `_check_max_stop_distance`, `_check_min_target_distance` — confirm no trade slipped through with a stop so tight it was structurally doomed (see the historical 6-8 tick stop bug this system has already hit once)
- No-open-position guard — `_check_no_open_position` — confirm no evidence of two simultaneous positions in the same instrument
- Instrument allowlist — `_check_instrument` — confirm only MNQ/MES ever reached a risk-approved decision
- Session gating — `_check_session`, `_check_session_window` — confirm trades occurred only in allowed sessions per current config, not a stale one
- Deliberately-disabled gates — confirm each one listed above is disabled BY CONFIGURATION matching the documented operator decision, not disabled by an accidental code regression; flag anything disabled that is NOT documented as an intentional choice
- `RISK_REJECTED` journal rows — confirm rejection reasons are human-readable (`failed_rule` field or equivalent), and sample a handful to confirm the stated reason matches the actual data that triggered it
- Live trading lock — `_check_live_trading_disabled`, `config/settings.py`'s `LiveTradingBlockedError` — confirm still enforced (this overlaps `/futures-execution-safety-audit`; cite that audit's result rather than re-deriving it from scratch if it was run this session)
- Whether the bot could revenge-trade or overtrade given the CURRENT (mostly-unlimited) trade-count/loss-streak configuration — this is a real, accepted risk given the 2026-06-17 decision, not a hidden defect; report it as a known, accepted risk posture, not a surprise finding

Classification (pick exactly one, for the risk gate set as a whole):
- `VALIDATED` — every active (non-deliberately-disabled) gate has fired at least once in the journal with a correct, human-readable reason, and no bracket/sizing/session violation was ever found
- `PROMISING BUT UNPROVEN` — gates exist and read correctly in code, but have not yet been observed firing (insufficient adverse events in the sample to exercise them) — this is the expected state for a small sample and is not itself a red flag
- `BROKEN` — a gate that should have fired did not, or fired with an incorrect/unreadable reason
- `OVERFIT` — not typically applicable to risk gates; use only if a gate's threshold was tuned to fit historical data rather than a stated risk tolerance
- `UNSAFE` — any evidence of an unbracketed order, a position-sizing violation, two simultaneous positions, or the live trading lock failing
- `WAIT` — too little adverse-event data to classify the untested gates either way

Forbidden actions:
- Do not modify `risk_rules.yaml` or `risk/risk_engine.py`.
- Do not enable, disable, or re-tune any gate (including the deliberately-disabled ones) as part of this audit.
- Do not commit or push.
- Do not report a deliberately-disabled gate as broken or missing without first checking whether `risk_rules.yaml` documents it as an intentional 2026-06-17 decision.
- Do not classify VALIDATED a gate that has never been observed firing — that is PROMISING BUT UNPROVEN at best.
- Do not generate or commit a report containing strategy-level detail to this public repo — write it to `private/` (gitignored) if any is produced.

Required output format:

Verdict:
APPROVE / REJECT / HOLD / AUDIT ONLY

Risk Gate Classification:
VALIDATED / PROMISING BUT UNPROVEN / BROKEN / OVERFIT / UNSAFE / WAIT

Why:
2-5 decisive reasons.

What I Verified:
- risk_rules.yaml read fresh, deliberately-disabled gates identified and cross-checked against documented rationale
- active gates checked against journal evidence (fired or not-yet-exercised)
- bracket completeness checked across the honest baseline's filled trades
- sizing/instrument/session checks verified
- RISK_REJECTED reason readability sampled
- live trading lock status cited (from this run or a recent /futures-execution-safety-audit)

Problems Found:
Separate:
- blockers (UNSAFE or BROKEN findings)
- warnings (a gate reported broken that turned out to be intentionally disabled — a process finding, not a code finding — or an active gate with unreadable rejection reasons)
- minor cleanup

Required Fixes:
- must-fix before trusting risk protection
- should-fix later
- do-not-touch items (explicitly list the intentionally-disabled gates here so they are not mistakenly "fixed" by a future pass)

Safe Next Step:
Smallest safe action only.

Safety gates:
- Any UNSAFE finding is an automatic REJECT regardless of P&L.
- A gate reported broken without first checking `risk_rules.yaml`'s own inline documentation is not a valid finding — re-check before reporting.
- An active gate with unreadable or missing rejection reasons caps the classification at HOLD, not VALIDATED.
- The current mostly-unlimited trade-count/loss-streak configuration is a known, accepted risk posture (2026-06-17 operator decision) — do not classify the system UNSAFE for this alone; report it as an explicit, named risk the operator has already accepted, distinct from an unnoticed defect.

Safe next step:
If VALIDATED or PROMISING BUT UNPROVEN with no blockers, the safe next step is to continue observation under the current config — this audit never authorizes loosening or tightening a gate. If BROKEN or UNSAFE, name the exact gate and the exact journal evidence (or absence of evidence) that shows it, and treat it as a blocker on any future live-readiness discussion.
