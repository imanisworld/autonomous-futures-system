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

from .outcome import PNL_BASES, SCHEMA as OUTCOME_SCHEMA, result_r_value
from .signal import SCHEMA as SIGNAL_SCHEMA, IntegrityStatus, default_registry, verify_record
from .strategy_epochs import (
    EpochRegistry,
    EpochStatus,
    OOSReference,
    StrategyEpoch,
    assert_observation_only,
    definition_hash,
)

# Verdict states the evaluator may emit; SUSPENDED/RETIRED are authority states.
EVALUATOR_STATES = frozenset({"COLLECTING", "WARNING", "FAIL_CANDIDATE"})


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
    """One canonical signal outcome reduced to what fitness needs.

    canonical_provenance is intentionally init=False. Direct construction is
    useful for diagnostics/tests but cannot create evidence that judges
    fitness; only from_records may mark a row verified after the canonical
    signal record passes #1151 verification.
    """

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
    prospective_catch: bool = False
    pnl_basis: str | None = None
    canonical_provenance: bool = field(default=False, init=False, repr=False)
    # Definition hash of the epoch the signal record was verified under
    # (set only by from_records); fitness judges only against that definition.
    epoch_definition_sha256: str | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        for name in ("signal_id", "strategy", "strategy_epoch"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.executed, bool):
            raise ValueError("executed must be an exact bool")
        if not isinstance(self.prospective_catch, bool):
            raise ValueError("prospective_catch must be an exact bool")
        if self.pnl_basis is not None and self.pnl_basis not in PNL_BASES:
            raise ValueError(f"pnl_basis must be one of {PNL_BASES}")
        if self.executed and self.pnl_basis != "executed":
            raise ValueError("executed observation must use pnl_basis=executed")
        if not self.executed and self.pnl_basis == "executed":
            raise ValueError("non-executed observation cannot use pnl_basis=executed")
        for name in ("data_integrity", "signal_integrity", "execution_integrity"):
            if not isinstance(getattr(self, name), IntegrityStatus):
                raise ValueError(f"{name} must be an IntegrityStatus")
        for name in ("result_r", "mae_r", "mfe_r", "gross_pnl", "net_pnl"):
            value = getattr(self, name)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a finite number or None")
            try:
                number = float(value)
            except OverflowError as exc:
                raise ValueError(f"{name} is out of range") from exc
            if not math.isfinite(number):
                raise ValueError(f"{name} must be finite")

    @staticmethod
    def from_records(
        signal_record: Mapping[str, Any],
        outcome_record: Mapping[str, Any],
        *,
        registry: EpochRegistry | None = None,
    ) -> "Observation":
        if not isinstance(signal_record, Mapping) or not isinstance(outcome_record, Mapping):
            raise ValueError("signal_record and outcome_record must be mappings")
        if signal_record.get("schema") != SIGNAL_SCHEMA:
            raise ValueError(f"signal record schema must be {SIGNAL_SCHEMA}")
        active_registry = registry if registry is not None else default_registry()
        problems = verify_record(signal_record, registry=active_registry)
        if problems:
            raise ValueError("invalid canonical signal record: " + "; ".join(problems))
        if signal_record.get("signal_id") != outcome_record.get("signal_id"):
            raise ValueError("signal/outcome records do not belong together")
        if outcome_record.get("schema") != OUTCOME_SCHEMA:
            raise ValueError(f"outcome record schema must be {OUTCOME_SCHEMA}")
        if outcome_record.get("strategy_epoch") != signal_record.get("strategy_epoch"):
            raise ValueError("outcome strategy_epoch does not match signal")
        if outcome_record.get("structure_id") != signal_record.get("structure_id"):
            raise ValueError("outcome structure_id does not match signal")
        if outcome_record.get("resolution_state") != signal_record.get("resolution_state"):
            raise ValueError("outcome resolution_state does not match signal")
        if outcome_record.get("prospective_catch") is not signal_record.get("prospective_catch"):
            raise ValueError("outcome prospective_catch does not match signal")
        executed = outcome_record.get("executed")
        if not isinstance(executed, bool):
            raise ValueError("outcome executed must be an exact bool")
        pnl_basis = outcome_record.get("pnl_basis")
        if pnl_basis not in PNL_BASES:
            raise ValueError(f"outcome pnl_basis must be one of {PNL_BASES}")
        if executed and pnl_basis != "executed":
            raise ValueError("executed outcome must use pnl_basis=executed")
        if not executed and pnl_basis == "executed":
            raise ValueError("non-executed outcome cannot use pnl_basis=executed")

        def known(name: str) -> float | None:
            item = outcome_record.get(name)
            if not isinstance(item, Mapping):
                raise ValueError(f"outcome {name} must be an evidence mapping")
            if item.get("status") not in ("OBSERVED", "DERIVED"):
                return None
            value = item.get("value")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"outcome {name}.value must be a finite number")
            try:
                number = float(value)
            except OverflowError as exc:
                raise ValueError(f"outcome {name}.value is out of range") from exc
            if not math.isfinite(number):
                raise ValueError(f"outcome {name}.value must be finite")
            return number

        observation = Observation(
            signal_id=signal_record["signal_id"],
            strategy=signal_record["strategy"],
            strategy_epoch=signal_record["strategy_epoch"],
            result_r=result_r_value(outcome_record),
            executed=executed,
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
            prospective_catch=signal_record.get("prospective_catch") is True,
            pnl_basis=pnl_basis,
        )
        object.__setattr__(observation, "canonical_provenance", True)
        object.__setattr__(observation, "epoch_definition_sha256", signal_record.get("epoch_definition_sha256"))
        return observation

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
    """'valid' or the exclusion reason. Only verified prospective catches judge fitness."""
    if obs.strategy != epoch.strategy or obs.strategy_epoch != epoch.epoch:
        return "other_epoch"
    if not obs.canonical_provenance:
        return "provenance_unverified"
    if obs.epoch_definition_sha256 != epoch.definition_sha256:
        # Verified under a different definition (another registry): not this epoch's evidence.
        return "epoch_definition_mismatch"
    if not obs.prospective_catch:
        return "not_prospective_catch"
    if obs.pnl_basis not in ("executed", "paper_equivalent"):
        return "non_trade_basis"
    if obs.data_integrity is not IntegrityStatus.VALID:
        return "data_integrity"
    if obs.signal_integrity is not IntegrityStatus.VALID:
        return "signal_integrity"
    if obs.executed and obs.execution_integrity is not IntegrityStatus.VALID:
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
    # The epoch definition the verdict was computed against (audit trail).
    epoch_definition_sha256: str | None = None

    @property
    def failed(self) -> bool:
        return self.state is FitnessState.FAIL_CANDIDATE

    @property
    def authority_unsupported(self) -> bool:
        """No preregistered OOS reference: nothing can justify holding authority."""
        return "no_oos_reference" in self.reasons


def _validate_oos(oos: Any) -> None:
    if oos is None:
        return
    if not isinstance(oos, OOSReference):
        raise ValueError("oos_reference must be an OOSReference")
    if not isinstance(oos.r_outcomes, tuple) or not oos.r_outcomes:
        raise ValueError("oos_reference.r_outcomes must be a non-empty tuple")
    for value in oos.r_outcomes:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("oos_reference.r_outcomes must be finite numbers")
        try:
            number = float(value)
        except OverflowError as exc:
            raise ValueError("oos_reference.r_outcomes value is out of range") from exc
        if not math.isfinite(number):
            raise ValueError("oos_reference.r_outcomes must be finite numbers")


def evaluate_fitness(
    epoch: StrategyEpoch,
    observations: Iterable[Observation],
    policy: FitnessPolicy = FitnessPolicy(),
    *,
    registry: EpochRegistry | None = None,
) -> FitnessVerdict:
    """Pure verdict for one registered epoch.

    The evaluator fails closed unless epoch is the exact object held by the
    supplied registry (default: committed registry). This prevents a hand-built
    epoch/OOS distribution from silently judging production evidence.
    """
    assert_observation_only(epoch)
    active_registry = registry if registry is not None else default_registry()
    registered = active_registry.get(epoch.strategy, epoch.epoch)
    if registered is not epoch:
        raise ValueError("fitness epoch must be the exact registered epoch object")
    if epoch.status not in (EpochStatus.FROZEN, EpochStatus.RETIRED):
        raise ValueError("fitness requires a FROZEN or RETIRED epoch")
    # A caller-built registry skips the registry file's validation, so the
    # epoch's own integrity is re-checked here at use time.
    if epoch.definition_sha256 != definition_hash(epoch.definition, epoch.thresholds):
        raise ValueError("fitness epoch definition_sha256 does not match its definition and thresholds")
    _validate_oos(epoch.oos_reference)
    excluded: dict[str, int] = {}
    valid: list[Observation] = []
    in_epoch = 0
    seen: set[str] = set()
    for obs in observations:
        if not isinstance(obs, Observation):
            raise ValueError(f"observations must be Observation, not {type(obs).__name__}")
        # One prospective signal is one observation: a replayed or duplicated
        # row must never count as independent evidence.
        if obs.signal_id in seen:
            raise ValueError(f"duplicate observation for signal {obs.signal_id!r}")
        seen.add(obs.signal_id)
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
        epoch_definition_sha256=epoch.definition_sha256,
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

    def __post_init__(self) -> None:
        if not isinstance(self.status, FitnessState):
            raise ValueError("status must be a FitnessState")
        for name in ("execution_authority", "observer_enabled"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} must be an exact bool")
        if self.execution_authority and self.status in (FitnessState.SUSPENDED, FitnessState.RETIRED):
            raise ValueError(f"a {self.status.value} strategy cannot hold execution authority")


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
    if not isinstance(verdict, FitnessVerdict) or not isinstance(verdict.state, FitnessState):
        raise ValueError("verdict must be a FitnessVerdict")
    if verdict.state.value not in EVALUATOR_STATES:
        # SUSPENDED/RETIRED are authority states, never evaluator verdicts.
        raise ValueError(f"the evaluator cannot emit {verdict.state.value}")
    if state.status in (FitnessState.SUSPENDED, FitnessState.RETIRED):
        # Sticky: only a human can move a suspended/retired strategy, and it
        # never holds authority while there.
        if state.execution_authority:
            change = AuthorityChange(at, EVALUATOR_ACTOR, state.status, state.status, False,
                                     "authority held while suspended/retired")
            return replace(state, execution_authority=False, history=(*state.history, change))
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
