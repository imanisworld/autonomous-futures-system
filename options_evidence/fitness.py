"""Strategy fitness / edge kill-switch (research authority layer).

Compares a strategy epoch's *valid prospective* R outcomes with the
preregistered, untouched OOS R distribution of that same epoch, and turns the
result into exactly one kind of authority action: **revocation**.

Authority asymmetry (the point of this module):

* ``apply_verdict`` can only ever move ``execution_authority`` True -> False.
  It never sets it True, never clears SUSPENDED, never RETIREs, and never
  changes ``observer_enabled``. A suspended strategy keeps collecting.
* Granting or restoring authority is ``human_grant`` -- a separate function
  that requires a named approver and an approval reference. Nothing in the
  evaluation path calls it (pinned by a test).
* Account safety caps (per-trade $ risk, aggregate open risk) live in the risk
  gates and are untouched here; a healthy fitness verdict cannot relax them.

Statistics are distribution-based, not profit factor: a seeded bootstrap of
the OOS R outcomes gives (a) the probability of a cumulative R at least this
bad over n trades and (b) the probability of a max R drawdown at least this
deep over n trades. Thresholds and review checkpoints are policy inputs
(``FitnessPolicy``) to be preregistered per epoch, not universal truths.
"""

from __future__ import annotations

import hashlib
import math
import random
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from .outcome import result_r_value
from .signal import IntegrityStatus
from .strategy_epochs import StrategyEpoch


class FitnessState(str, Enum):
    COLLECTING = "COLLECTING"
    WARNING = "WARNING"
    FAIL_CANDIDATE = "FAIL_CANDIDATE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


@dataclass(frozen=True)
class FitnessPolicy:
    """Review policy. Defaults are conservative placeholders, not statistical truth.

    ``review_checkpoints`` are the valid-observation counts at which a
    FAIL_CANDIDATE verdict may be issued; between and before them the worst
    verdict is WARNING. The operator should preregister the policy with the
    epoch (store it alongside the epoch's thresholds) before collection.
    """

    review_checkpoints: tuple[int, ...] = (10, 15, 20)
    warn_tail_probability: float = 0.10
    fail_tail_probability: float = 0.02
    max_invalid_share: float = 0.25
    bootstrap_samples: int = 4000

    def __post_init__(self) -> None:
        if not self.review_checkpoints or list(self.review_checkpoints) != sorted(set(self.review_checkpoints)):
            raise ValueError("review_checkpoints must be strictly increasing")
        if self.review_checkpoints[0] < 2:
            raise ValueError("first review checkpoint must be >= 2")
        if not 0 < self.fail_tail_probability < self.warn_tail_probability < 0.5:
            raise ValueError("require 0 < fail_tail_probability < warn_tail_probability < 0.5")
        if not 0 <= self.max_invalid_share < 1:
            raise ValueError("max_invalid_share must be in [0, 1)")
        if self.bootstrap_samples < 500:
            raise ValueError("bootstrap_samples must be >= 500")


@dataclass(frozen=True)
class Observation:
    """One prospective signal outcome, reduced to what fitness needs."""

    signal_id: str
    strategy: str
    strategy_epoch: str
    result_r: float | None
    executed: bool
    data_integrity: IntegrityStatus
    signal_integrity: IntegrityStatus
    execution_integrity: IntegrityStatus
    mae_r: float | None = None
    mfe_r: float | None = None
    gross_pnl: float | None = None
    net_pnl: float | None = None

    @staticmethod
    def from_records(signal_record: Mapping[str, Any], outcome_record: Mapping[str, Any]) -> "Observation":
        if signal_record.get("signal_id") != outcome_record.get("signal_id"):
            raise ValueError("signal/outcome records do not belong together")

        def known(name: str) -> float | None:
            item = outcome_record.get(name) or {}
            if item.get("status") in ("OBSERVED", "DERIVED") and isinstance(item.get("value"), (int, float)):
                value = float(item["value"])
                return value if math.isfinite(value) else None
            return None

        return Observation(
            signal_id=str(signal_record["signal_id"]),
            strategy=str(signal_record["strategy"]),
            strategy_epoch=str(signal_record["strategy_epoch"]),
            result_r=result_r_value(outcome_record),
            executed=bool(outcome_record.get("executed")),
            # The canonical signal is authoritative for signal integrity (a late
            # or gap capture stays excluded whatever the outcome row says);
            # data integrity is the worse of the two records.
            data_integrity=_worst(
                signal_record.get("data_integrity", "UNKNOWN"),
                outcome_record.get("data_integrity", "UNKNOWN"),
            ),
            signal_integrity=IntegrityStatus(signal_record.get("signal_integrity", "UNKNOWN")),
            execution_integrity=IntegrityStatus(outcome_record.get("execution_integrity", "NOT_APPLICABLE")),
            mae_r=known("mae_r"),
            mfe_r=known("mfe_r"),
            gross_pnl=known("gross_pnl"),
            net_pnl=known("net_pnl"),
        )


_INTEGRITY_RANK = {
    IntegrityStatus.VALID: 0,
    IntegrityStatus.NOT_APPLICABLE: 0,
    IntegrityStatus.DEGRADED: 1,
    IntegrityStatus.UNKNOWN: 2,
    IntegrityStatus.INVALID: 3,
}


def _worst(*values: Any) -> IntegrityStatus:
    statuses = [IntegrityStatus(v) for v in values]
    return max(statuses, key=lambda st: _INTEGRITY_RANK[st])


def classify(obs: Observation, epoch: StrategyEpoch) -> str:
    """'valid' or the exclusion reason. Only valid observations judge the strategy."""
    if obs.strategy != epoch.strategy or obs.strategy_epoch != epoch.epoch:
        return "other_epoch"
    if obs.data_integrity is not IntegrityStatus.VALID:
        return "data_integrity"
    if obs.signal_integrity is not IntegrityStatus.VALID:
        return "signal_integrity"
    if obs.executed and obs.execution_integrity is not IntegrityStatus.VALID:
        # A bad fill is an execution problem, not evidence about the edge.
        return "execution_integrity"
    if obs.result_r is None or not math.isfinite(obs.result_r):
        return "result_unavailable"
    return "valid"


def max_drawdown_r(outcomes: Sequence[float]) -> float:
    peak = 0.0
    equity = 0.0
    worst = 0.0
    for r in outcomes:
        equity += r
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _seed(epoch: StrategyEpoch, n: int) -> int:
    digest = hashlib.sha256(f"{epoch.definition_sha256}:{n}".encode()).hexdigest()
    return int(digest[:16], 16)


def bootstrap_tail_probabilities(
    oos: Sequence[float], observed: Sequence[float], *, samples: int, seed: int
) -> tuple[float, float]:
    """P(sum <= observed sum) and P(maxDD >= observed maxDD) under OOS resampling."""
    n = len(observed)
    observed_sum = sum(observed)
    observed_dd = max_drawdown_r(observed)
    rng = random.Random(seed)
    sum_hits = 0
    dd_hits = 0
    for _ in range(samples):
        draw = [oos[rng.randrange(len(oos))] for _ in range(n)]
        if sum(draw) <= observed_sum + 1e-12:
            sum_hits += 1
        if max_drawdown_r(draw) >= observed_dd - 1e-12:
            dd_hits += 1
    # +1 smoothing: a finite bootstrap never claims probability exactly 0.
    return (sum_hits + 1) / (samples + 1), (dd_hits + 1) / (samples + 1)


@dataclass(frozen=True)
class FitnessVerdict:
    strategy: str
    epoch: str
    state: FitnessState
    reasons: tuple[str, ...]
    valid_n: int
    excluded: Mapping[str, int]
    at_checkpoint: int | None
    prospective_mean_r: float | None
    oos_mean_r: float | None
    p_cumulative_r: float | None
    p_drawdown: float | None
    max_drawdown_r: float | None
    mean_mae_r: float | None = None
    mean_mfe_r: float | None = None
    executed_n: int = 0
    net_pnl_total: float | None = None

    @property
    def failed(self) -> bool:
        return self.state is FitnessState.FAIL_CANDIDATE

    @property
    def authority_unsupported(self) -> bool:
        """No preregistered OOS reference: nothing can justify holding authority."""
        return "no_oos_reference" in self.reasons


def evaluate_fitness(
    epoch: StrategyEpoch,
    observations: Iterable[Observation],
    policy: FitnessPolicy = FitnessPolicy(),
) -> FitnessVerdict:
    """Pure verdict for one epoch. Never returns SUSPENDED/RETIRED; those are authority states."""
    excluded: dict[str, int] = {}
    valid: list[Observation] = []
    in_epoch = 0
    for obs in observations:
        reason = classify(obs, epoch)
        if reason == "other_epoch":
            continue
        in_epoch += 1
        if reason == "valid":
            valid.append(obs)
        else:
            excluded[reason] = excluded.get(reason, 0) + 1

    outcomes = [float(o.result_r) for o in valid]  # type: ignore[arg-type]
    n = len(outcomes)
    reasons: list[str] = []
    state = FitnessState.COLLECTING

    def mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    maes = [o.mae_r for o in valid if o.mae_r is not None]
    mfes = [o.mfe_r for o in valid if o.mfe_r is not None]
    executed = [o for o in valid if o.executed]
    nets = [o.net_pnl for o in executed if o.net_pnl is not None]
    base = dict(
        strategy=epoch.strategy,
        epoch=epoch.epoch,
        valid_n=n,
        excluded=dict(sorted(excluded.items())),
        prospective_mean_r=mean(outcomes),
        max_drawdown_r=max_drawdown_r(outcomes) if outcomes else None,
        mean_mae_r=mean(maes),  # type: ignore[arg-type]
        mean_mfe_r=mean(mfes),  # type: ignore[arg-type]
        executed_n=len(executed),
        net_pnl_total=sum(nets) if nets else None,  # type: ignore[arg-type]
    )

    if epoch.oos_reference is None:
        return FitnessVerdict(
            state=FitnessState.COLLECTING,
            reasons=("no_oos_reference",),
            at_checkpoint=None,
            oos_mean_r=None,
            p_cumulative_r=None,
            p_drawdown=None,
            **base,  # type: ignore[arg-type]
        )

    if in_epoch and (in_epoch - n) / in_epoch > policy.max_invalid_share:
        state = FitnessState.WARNING
        reasons.append("evidence_quality: invalid share above policy")

    reached = [c for c in policy.review_checkpoints if c <= n]
    checkpoint = reached[-1] if reached else None
    p_sum = p_dd = None
    if n >= 2:
        p_sum, p_dd = bootstrap_tail_probabilities(
            epoch.oos_reference.r_outcomes,
            outcomes,
            samples=policy.bootstrap_samples,
            seed=_seed(epoch, n),
        )
        worst = min(p_sum, p_dd)
        if worst < policy.warn_tail_probability:
            state = FitnessState.WARNING
            reasons.append(
                f"prospective R in OOS lower tail (p_sum={p_sum:.4f}, p_drawdown={p_dd:.4f})"
            )
        if checkpoint is not None and worst < policy.fail_tail_probability:
            state = FitnessState.FAIL_CANDIDATE
            reasons.append(f"fail threshold breached at review checkpoint {checkpoint}")
        elif checkpoint is None and worst < policy.fail_tail_probability:
            reasons.append("fail-level tail before first checkpoint: WARNING only until checkpoint")
    if n < policy.review_checkpoints[0]:
        reasons.append(f"collecting: {n}/{policy.review_checkpoints[0]} valid observations")

    return FitnessVerdict(
        state=state,
        reasons=tuple(reasons),
        at_checkpoint=checkpoint,
        oos_mean_r=epoch.oos_reference.mean_r,
        p_cumulative_r=p_sum,
        p_drawdown=p_dd,
        **base,  # type: ignore[arg-type]
    )


# ── authority (revoke-only automation) ───────────────────────────────────────


@dataclass(frozen=True)
class AuthorityChange:
    at: datetime
    actor: str
    from_status: FitnessState
    to_status: FitnessState
    execution_authority: bool
    reason: str


@dataclass(frozen=True)
class AuthorityState:
    strategy: str
    epoch: str
    status: FitnessState = FitnessState.COLLECTING
    execution_authority: bool = False
    observer_enabled: bool = True
    approval_ref: str | None = None
    history: tuple[AuthorityChange, ...] = field(default_factory=tuple)


EVALUATOR_ACTOR = "fitness_evaluator"


def apply_verdict(state: AuthorityState, verdict: FitnessVerdict, *, at: datetime) -> AuthorityState:
    """Apply an evaluator verdict. May revoke; can never grant or restore.

    * FAIL_CANDIDATE, or authority held without an OOS reference ->
      status SUSPENDED, execution_authority False.
    * otherwise -> research status follows the verdict *only* while not
      SUSPENDED/RETIRED, and execution_authority is left exactly as it was
      (it can be False->False or True->True, never False->True).
    * observer_enabled is never touched.
    """
    if (state.strategy, state.epoch) != (verdict.strategy, verdict.epoch):
        raise ValueError("verdict is for a different strategy epoch")
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    if state.status in (FitnessState.SUSPENDED, FitnessState.RETIRED):
        # Sticky: only a human can move a suspended/retired strategy.
        return state
    if verdict.failed or (verdict.authority_unsupported and state.execution_authority):
        change = AuthorityChange(
            at=at,
            actor=EVALUATOR_ACTOR,
            from_status=state.status,
            to_status=FitnessState.SUSPENDED,
            execution_authority=False,
            reason="; ".join(verdict.reasons) or verdict.state.value,
        )
        return replace(
            state,
            status=FitnessState.SUSPENDED,
            execution_authority=False,
            history=(*state.history, change),
        )
    if verdict.state is state.status:
        return state
    change = AuthorityChange(
        at=at,
        actor=EVALUATOR_ACTOR,
        from_status=state.status,
        to_status=verdict.state,
        execution_authority=state.execution_authority,
        reason="; ".join(verdict.reasons) or verdict.state.value,
    )
    return replace(state, status=verdict.state, history=(*state.history, change))


def human_grant(
    state: AuthorityState,
    *,
    approved_by: str,
    approval_ref: str,
    at: datetime,
    restore_from_suspension: bool = False,
) -> AuthorityState:
    """Human-only path to grant/restore execution authority. Never called by the evaluator."""
    if not approved_by.strip() or approved_by.strip() == EVALUATOR_ACTOR:
        raise PermissionError("a named human approver is required")
    if not approval_ref.strip():
        raise PermissionError("an approval reference is required")
    if state.status is FitnessState.RETIRED:
        raise PermissionError("a RETIRED epoch cannot be re-granted; register a new epoch")
    if state.status is FitnessState.SUSPENDED and not restore_from_suspension:
        raise PermissionError("restoring a SUSPENDED strategy requires restore_from_suspension=True")
    if state.status is FitnessState.FAIL_CANDIDATE:
        raise PermissionError("cannot grant while FAIL_CANDIDATE")
    change = AuthorityChange(
        at=at,
        actor=approved_by.strip(),
        from_status=state.status,
        to_status=FitnessState.COLLECTING if state.status is FitnessState.SUSPENDED else state.status,
        execution_authority=True,
        reason=f"human grant {approval_ref}",
    )
    return replace(
        state,
        status=change.to_status,
        execution_authority=True,
        approval_ref=approval_ref,
        history=(*state.history, change),
    )


def human_retire(state: AuthorityState, *, approved_by: str, reason: str, at: datetime) -> AuthorityState:
    """Human-only retirement. Observer collection is left enabled."""
    if not approved_by.strip() or approved_by.strip() == EVALUATOR_ACTOR:
        raise PermissionError("a named human approver is required")
    change = AuthorityChange(at, approved_by.strip(), state.status, FitnessState.RETIRED, False, reason)
    return replace(state, status=FitnessState.RETIRED, execution_authority=False, history=(*state.history, change))
