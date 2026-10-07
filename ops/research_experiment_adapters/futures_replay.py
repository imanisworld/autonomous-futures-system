"""Futures replay execution adapter for the canonical Experiment Runner (U3).

Runs a frozen futures replay dataset through the existing offline replay path
(``replay.replay_engine.ReplayEngine`` -> decision -> risk -> ``PaperBroker``
-> journal) and translates the journaled TRADE / RISK_REJECTED decisions and
their OUTCOME rows into canonical U1 ``trade_execution`` rows.

The adapter does not implement strategy, risk, or fill logic of its own. Every
entry/stop/target, fill, exit, and gross P&L value comes from the replay
journal. The only values derived here are mechanical: deterministic candidate
identity, timestamps from bar labels, frozen-assumption costs, net P&L, R
multiple, and bar-granular MAE/MFE.

Fail-closed contract (any violation raises ``FuturesReplayAdapterError``; the
runner then produces no metrics, no evidence bundle, and no OOS receipt):

* ``evidence_type`` must be ``trade_execution``.
* Both arms must declare the exact commit SHA of the code executing this
  adapter, and that checkout must have no tracked modifications. Cross-commit
  arms are not supported; arms may differ only through ``changed_variables``
  applied as ``SystemConfig`` overrides.
* ``execution_assumptions`` must use the closed machine-readable vocabulary in
  ``parse_execution_assumptions``. Fill/cost behavior comes only from that
  frozen bundle, never from environment or YAML defaults.
* ``data.dataset_id`` must be a repo-relative replay manifest whose SHA-256
  equals ``data.dataset_hash`` and whose every day file carries a matching
  ``sha256``.
* With an active U2 partition, no candle at or after the window end is
  replayed, and only candidates whose ``signal_ts`` falls in ``[start, end)``
  become members. A candidate left unresolved by the data fails the run.
* The paper-to-broker mirror hook must be disabled.
* Strategies resolved through the 2-1-2 / 1-2-2 intrabar restore path are not
  supported (their causal order timestamps are not representable here).
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import subprocess
import tempfile
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from ops import evidence_row as evidence_contract
from ops import experiment_partitions as partition_contract
from ops.research_experiment_runner import ArmRawResult, ExperimentContext

SETUP_TYPE = "futures_replay"
ADAPTER_VERSION = "1.0.0"
ARMS = ("baseline", "candidate")

FILLED_RESULTS = frozenset({"WIN", "LOSS", "BREAKEVEN"})
NO_FILL_RESULTS = frozenset({"CANCELLED"})

# SystemConfig fields owned by the frozen execution-model bundle or by the
# adapter itself. They can never be a changed variable.
ADAPTER_OWNED_CONFIG_FIELDS = frozenset(
    {
        "fill_slippage_ticks",
        "fill_pessimistic_both_hit",
        "breakeven_at_1r",
        "runner_mode",
        "exit_mode",
        "runner_activation_r",
        "runner_trail_r",
        "entry_fill_model",
        "entry_tolerance_ticks_by_root",
        "live_trading_enabled",
        "paper_mode",
        "log_dir",
    }
)

UNSUPPORTED_STRATEGIES = frozenset({"strat_212", "strat_122"})


class FuturesReplayAdapterError(RuntimeError):
    """The futures replay experiment cannot be executed reproducibly."""


# ─── Code identity ───────────────────────────────────────────────────────────


def _code_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def executing_code_sha() -> str:
    """Exact SHA of the checkout running this adapter; refuse a dirty tree."""
    root = _code_root()
    head = _git(root, "rev-parse", "HEAD")
    sha = head.stdout.strip().lower()
    if head.returncode != 0 or len(sha) != 40:
        raise FuturesReplayAdapterError(
            "cannot resolve the executing code SHA (git rev-parse HEAD failed)"
        )
    dirty = _git(root, "status", "--porcelain", "--untracked-files=no")
    if dirty.returncode != 0 or dirty.stdout.strip():
        raise FuturesReplayAdapterError(
            "executing checkout has tracked modifications; replay evidence must "
            "come from an exact committed SHA"
        )
    return sha


def _load_base_config():
    from config.settings import load_config

    return load_config(str(_code_root() / "risk_rules.yaml"))


# Replaceable for tests only. Production uses the risk_rules.yaml committed at
# the executing SHA; the resolved snapshot hash is recorded per arm.
BASE_CONFIG_PROVIDER: Callable[[], Any] = _load_base_config
CODE_SHA_PROVIDER: Callable[[], str] = executing_code_sha


# ─── Frozen execution assumptions ───────────────────────────────────────────


@dataclass(frozen=True)
class ReplayExecutionModel:
    entry_fill_model: str
    pessimistic_both_hit: bool
    breakeven_at_1r: bool
    slippage_ticks: float
    commission_per_contract_per_side: float
    fees_per_contract_per_side: float
    sizing: str


def _token(assumptions: Mapping[str, Any], key: str) -> str:
    value = assumptions.get(key)
    if not isinstance(value, str) or not value.strip():
        raise FuturesReplayAdapterError(f"execution_assumptions.{key} must be a non-empty string")
    return value.strip()


def _keyed_amount(value: str, *, key: str, prefix: str) -> float:
    if not value.startswith(prefix):
        raise FuturesReplayAdapterError(
            f"execution_assumptions.{key} must be '{prefix}<non-negative decimal>'; got {value!r}"
        )
    raw = value[len(prefix):]
    try:
        amount = Decimal(raw)
    except InvalidOperation as exc:
        raise FuturesReplayAdapterError(
            f"execution_assumptions.{key} amount is not a decimal: {raw!r}"
        ) from exc
    if not amount.is_finite() or amount < 0:
        raise FuturesReplayAdapterError(
            f"execution_assumptions.{key} amount must be finite and >= 0; got {raw!r}"
        )
    return float(amount)


def parse_execution_assumptions(assumptions: Any) -> ReplayExecutionModel:
    """Map the frozen assumption bundle onto replay settings. Closed vocabulary.

    entry_fill_model        market | stop_market
    same_bar_ambiguity_rule stop_first            (pessimistic only)
    stop_handling           fixed_stop | breakeven_at_1r
    target_handling         fixed_limit           (runner exits unsupported)
    slippage_assumption     adverse_ticks=<n>     (market entry + stop exits)
    commission              usd_per_contract_per_side=<x>
    exchange_broker_fees    usd_per_contract_per_side=<x>
    sizing_assumptions      replay_risk_engine
    """
    if not isinstance(assumptions, Mapping):
        raise FuturesReplayAdapterError("trade_execution requires execution_assumptions")
    # U1 completeness/unknown-key rules first.
    try:
        evidence_contract.freeze_execution_assumptions(assumptions)
    except evidence_contract.EvidenceContractError as exc:
        raise FuturesReplayAdapterError(str(exc)) from exc

    entry_model = _token(assumptions, "entry_fill_model")
    if entry_model not in ("market", "stop_market"):
        raise FuturesReplayAdapterError(
            f"entry_fill_model {entry_model!r} unsupported by futures_replay "
            "(market | stop_market)"
        )
    ambiguity = _token(assumptions, "same_bar_ambiguity_rule")
    if ambiguity != "stop_first":
        raise FuturesReplayAdapterError(
            f"same_bar_ambiguity_rule {ambiguity!r} refused; only pessimistic "
            "'stop_first' is allowed"
        )
    stop_handling = _token(assumptions, "stop_handling")
    if stop_handling not in ("fixed_stop", "breakeven_at_1r"):
        raise FuturesReplayAdapterError(
            f"stop_handling {stop_handling!r} unsupported (fixed_stop | breakeven_at_1r)"
        )
    target_handling = _token(assumptions, "target_handling")
    if target_handling != "fixed_limit":
        raise FuturesReplayAdapterError(
            f"target_handling {target_handling!r} unsupported (fixed_limit)"
        )
    sizing = _token(assumptions, "sizing_assumptions")
    if sizing != "replay_risk_engine":
        raise FuturesReplayAdapterError(
            f"sizing_assumptions {sizing!r} unsupported (replay_risk_engine)"
        )
    return ReplayExecutionModel(
        entry_fill_model=entry_model,
        pessimistic_both_hit=True,
        breakeven_at_1r=stop_handling == "breakeven_at_1r",
        slippage_ticks=_keyed_amount(
            _token(assumptions, "slippage_assumption"),
            key="slippage_assumption",
            prefix="adverse_ticks=",
        ),
        commission_per_contract_per_side=_keyed_amount(
            _token(assumptions, "commission"),
            key="commission",
            prefix="usd_per_contract_per_side=",
        ),
        fees_per_contract_per_side=_keyed_amount(
            _token(assumptions, "exchange_broker_fees"),
            key="exchange_broker_fees",
            prefix="usd_per_contract_per_side=",
        ),
        sizing=sizing,
    )


# ─── Spec / arm configuration ───────────────────────────────────────────────


def _arm(ctx: ExperimentContext) -> str:
    arm = str(ctx.spec.get("_runner_arm") or "")
    if arm not in ARMS:
        raise FuturesReplayAdapterError(f"unsupported runner arm {arm!r}")
    return arm


def _require_code_identity(spec: Mapping[str, Any]) -> str:
    executing = str(CODE_SHA_PROVIDER()).strip().lower()
    for arm in ARMS:
        declared = str((spec.get(arm) or {}).get("commit_sha") or "").strip().lower()
        if declared != executing:
            raise FuturesReplayAdapterError(
                f"{arm}.commit_sha {declared or None!r} != executing code SHA {executing!r}; "
                "futures_replay runs both arms on the executing commit only"
            )
    return executing


def _config_snapshot_sha256(config: Any) -> str:
    payload = json.dumps(asdict(config), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _same_kind(current: Any, new: Any) -> bool:
    if isinstance(current, bool) or isinstance(new, bool):
        return isinstance(current, bool) and isinstance(new, bool)
    if isinstance(current, (int, float)) and isinstance(new, (int, float)):
        return not (isinstance(current, int) and isinstance(new, float) and not new.is_integer())
    for kind in (str, list, dict):
        if isinstance(current, kind):
            return isinstance(new, kind)
    return current is None and new is None


def arm_overrides(spec: Mapping[str, Any], arm: str, base_config: Any) -> dict[str, Any]:
    """Return SystemConfig overrides for one arm from ``changed_variables``."""
    changed = spec.get("changed_variables")
    if not isinstance(changed, list) or not changed:
        raise FuturesReplayAdapterError("changed_variables must be a non-empty list")
    known = {f.name for f in fields(base_config)}
    overrides: dict[str, Any] = {}
    for index, item in enumerate(changed):
        if not isinstance(item, Mapping):
            raise FuturesReplayAdapterError(f"changed_variables[{index}] must be an object")
        name = str(item.get("name") or "")
        if name in ADAPTER_OWNED_CONFIG_FIELDS:
            raise FuturesReplayAdapterError(
                f"changed_variables[{index}] {name!r} is owned by the frozen execution "
                "model and cannot differ between arms"
            )
        if name not in known:
            raise FuturesReplayAdapterError(
                f"changed_variables[{index}] {name!r} is not a SystemConfig field"
            )
        if name in overrides:
            raise FuturesReplayAdapterError(f"changed_variables repeats {name!r}")
        value_key = "baseline_value" if arm == "baseline" else "candidate_value"
        if value_key not in item:
            raise FuturesReplayAdapterError(f"changed_variables[{index}] missing {value_key}")
        value = item[value_key]
        current = getattr(base_config, name)
        if not _same_kind(current, value):
            raise FuturesReplayAdapterError(
                f"changed_variables[{index}] {name!r} {value_key} type "
                f"{type(value).__name__} does not match SystemConfig "
                f"{type(current).__name__}"
            )
        if isinstance(current, float) and isinstance(value, int):
            value = float(value)
        overrides[name] = value
    return overrides


def build_arm_config(
    base_config: Any,
    model: ReplayExecutionModel,
    overrides: Mapping[str, Any],
    *,
    log_dir: str,
) -> Any:
    frozen = {
        "fill_slippage_ticks": model.slippage_ticks,
        "fill_pessimistic_both_hit": model.pessimistic_both_hit,
        "breakeven_at_1r": model.breakeven_at_1r,
        "runner_mode": False,
        "exit_mode": "static",
        "entry_fill_model": model.entry_fill_model,
        "entry_tolerance_ticks_by_root": {},
        "live_trading_enabled": False,
        "paper_mode": True,
        "log_dir": log_dir,
    }
    return replace(base_config, **frozen, **dict(overrides))


# ─── Dataset binding ────────────────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class BoundDataset:
    manifest_path: Path
    manifest_sha256: str
    days: list[dict[str, Any]]
    htf: list[dict[str, Any]]


def bind_dataset(root: Path, spec: Mapping[str, Any]) -> BoundDataset:
    data = spec.get("data") if isinstance(spec.get("data"), Mapping) else {}
    dataset_id = str(data.get("dataset_id") or "").strip()
    declared = str(data.get("dataset_hash") or "").strip().lower()
    if not dataset_id or Path(dataset_id).is_absolute() or ".." in Path(dataset_id).parts:
        raise FuturesReplayAdapterError(
            "data.dataset_id must be a repo-relative replay manifest path"
        )
    if len(declared) != 64:
        raise FuturesReplayAdapterError("data.dataset_hash (manifest SHA-256) is required")
    manifest_path = (root / dataset_id).resolve()
    if not manifest_path.is_file():
        raise FuturesReplayAdapterError(f"replay manifest missing: {dataset_id}")
    actual = _sha256_file(manifest_path)
    if actual != declared:
        raise FuturesReplayAdapterError(
            f"replay manifest SHA-256 mismatch: actual={actual} expected={declared}"
        )
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FuturesReplayAdapterError(f"replay manifest unreadable: {exc}") from exc
    days = payload.get("days") if isinstance(payload, dict) else None
    if not isinstance(days, list) or not days:
        raise FuturesReplayAdapterError("replay manifest must contain a non-empty days[]")

    bound_days: list[dict[str, Any]] = []
    for index, raw in enumerate(days):
        if not isinstance(raw, dict):
            raise FuturesReplayAdapterError(f"manifest days[{index}] must be an object")
        missing = {"path", "instrument", "session", "expected_behavior", "sha256"} - set(raw)
        if missing:
            raise FuturesReplayAdapterError(
                f"manifest days[{index}] missing fields: {sorted(missing)}"
            )
        path = Path(str(raw["path"]))
        resolved = path if path.is_absolute() else manifest_path.parent / path
        if not resolved.is_file():
            raise FuturesReplayAdapterError(f"manifest days[{index}] file missing: {resolved}")
        digest = _sha256_file(resolved)
        if digest != str(raw["sha256"]).strip().lower():
            raise FuturesReplayAdapterError(
                f"manifest days[{index}] sha256 mismatch for {raw['path']}: "
                f"actual={digest} expected={raw['sha256']}"
            )
        bound_days.append({**raw, "resolved_path": resolved, "sha256": digest})

    verify_manifest_chronology(bound_days)

    bound_htf: list[dict[str, Any]] = []
    for index, raw in enumerate(payload.get("htf") or []):
        if not isinstance(raw, dict) or not {"timeframe", "path", "sha256"} <= set(raw):
            raise FuturesReplayAdapterError(
                f"manifest htf[{index}] requires timeframe, path, sha256"
            )
        path = Path(str(raw["path"]))
        resolved = path if path.is_absolute() else manifest_path.parent / path
        if not resolved.is_file():
            raise FuturesReplayAdapterError(f"manifest htf[{index}] file missing: {resolved}")
        digest = _sha256_file(resolved)
        if digest != str(raw["sha256"]).strip().lower():
            raise FuturesReplayAdapterError(f"manifest htf[{index}] sha256 mismatch")
        bound_htf.append({**raw, "resolved_path": resolved, "sha256": digest})

    return BoundDataset(
        manifest_path=manifest_path,
        manifest_sha256=actual,
        days=bound_days,
        htf=bound_htf,
    )


def _parse_utc(value: Any, *, field_name: str) -> datetime:
    try:
        return evidence_contract.parse_ts(value, field_name=field_name)
    except evidence_contract.EvidenceContractError as exc:
        raise FuturesReplayAdapterError(str(exc)) from exc


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _file_time_span(day: Mapping[str, Any]) -> Optional[tuple[datetime, datetime]]:
    first: Optional[datetime] = None
    last: Optional[datetime] = None
    for line_no, line in enumerate(
        day["resolved_path"].read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            raise FuturesReplayAdapterError(f"{day['path']}:{line_no} is not valid JSON") from exc
        if not isinstance(row, dict):
            raise FuturesReplayAdapterError(f"{day['path']}:{line_no} must be a JSON object")
        ts = _parse_utc(row.get("timestamp"), field_name=f"{day['path']}:{line_no} timestamp")
        if last is not None and ts < last:
            raise FuturesReplayAdapterError(
                f"{day['path']}:{line_no} timestamp {_iso(ts)} precedes prior candle {_iso(last)}"
            )
        first = ts if first is None else first
        last = ts
    if first is None or last is None:
        return None
    return first, last


def verify_manifest_chronology(days: list[Mapping[str, Any]]) -> None:
    """Fail closed unless manifest day files are globally chronological.

    ReplayEngine.run_manifest() executes files in manifest order and carries
    rolling balance and open positions across files. Each file must therefore
    start strictly after every earlier file has finished, regardless of
    instrument, or later-dated state could leak into earlier-dated replay.
    """
    prior_end: Optional[datetime] = None
    prior_path: Optional[str] = None
    for index, day in enumerate(days):
        span = _file_time_span(day)
        if span is None:
            raise FuturesReplayAdapterError(f"manifest days[{index}] {day['path']} has no candles")
        start, end = span
        if prior_end is not None and start <= prior_end:
            raise FuturesReplayAdapterError(
                f"manifest days[{index}] {day['path']} starts {_iso(start)} at/before "
                f"{prior_path} ends {_iso(prior_end)}; manifest days must be globally "
                "chronological and non-overlapping"
            )
        prior_end = end
        prior_path = str(day["path"])


def stage_candles(
    dataset: BoundDataset,
    workdir: Path,
    *,
    replay_end: Optional[datetime],
) -> tuple[Path, list[dict[str, Any]]]:
    """Write a staged manifest; drop every candle at/after ``replay_end``."""
    staged_days: list[dict[str, Any]] = []
    file_stats: list[dict[str, Any]] = []
    for index, day in enumerate(dataset.days):
        kept: list[str] = []
        total = 0
        for line_no, line in enumerate(
            day["resolved_path"].read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            total += 1
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise FuturesReplayAdapterError(
                    f"{day['path']}:{line_no} is not valid JSON"
                ) from exc
            ts = _parse_utc(row.get("timestamp"), field_name=f"{day['path']}:{line_no} timestamp")
            if replay_end is not None and ts >= replay_end:
                continue
            kept.append(line)
        file_stats.append(
            {
                "path": str(day["path"]),
                "sha256": day["sha256"],
                "candles": total,
                "candles_replayed": len(kept),
            }
        )
        if not kept:
            continue
        staged = workdir / f"day_{index:04d}.jsonl"
        staged.write_text("\n".join(kept) + "\n", encoding="utf-8")
        entry = {
            key: day[key]
            for key in ("instrument", "session", "expected_behavior", "notes", "allow_mixed_instruments")
            if key in day
        }
        entry["path"] = staged.name
        staged_days.append(entry)
    if not staged_days:
        raise FuturesReplayAdapterError("no candles remain to replay for this partition")
    manifest = workdir / "manifest.json"
    manifest.write_text(json.dumps({"days": staged_days}, indent=2) + "\n", encoding="utf-8")
    return manifest, file_stats


# ─── Journal translation ────────────────────────────────────────────────────


def _read_journal(log_dir: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    decisions: list[dict[str, Any]] = []
    outcomes: dict[str, dict[str, Any]] = {}
    for path in sorted(log_dir.glob("journal_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("type") == "OUTCOME":
                outcome = row.get("outcome") or {}
                order_id = outcome.get("paper_order_id")
                if not order_id:
                    raise FuturesReplayAdapterError("OUTCOME row without paper_order_id")
                if order_id in outcomes:
                    raise FuturesReplayAdapterError(f"duplicate OUTCOME for {order_id}")
                outcomes[order_id] = {**outcome, "instrument": row.get("instrument")}
            elif row.get("decision") in ("TRADE", "RISK_REJECTED") and row.get("paper_order_id"):
                decisions.append(row)
    return decisions, outcomes


def candidate_signal_id(
    *, instrument: str, strategy: str, signal_ts: str, direction: str,
    entry: float, stop: float, target: float,
) -> str:
    material = "|".join(
        [instrument, strategy, signal_ts, direction, repr(float(entry)), repr(float(stop)), repr(float(target))]
    )
    return "cand-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


class _CandleIndex:
    def __init__(self, dataset: BoundDataset) -> None:
        from replay.candle_loader import ReplayCandleLoader

        by_instrument: dict[str, dict[datetime, Any]] = {}
        for day in dataset.days:
            candles = ReplayCandleLoader().load_jsonl(
                day["resolved_path"],
                allow_mixed_instruments=bool(day.get("allow_mixed_instruments", False)),
            )
            for candle in candles:
                ts = _parse_utc(candle.timestamp, field_name="candle timestamp")
                bars = by_instrument.setdefault(candle.instrument, {})
                if ts in bars:
                    raise FuturesReplayAdapterError(
                        f"duplicate {candle.instrument} candle at {_iso(ts)} across manifest days"
                    )
                bars[ts] = candle
        self._bars = by_instrument
        self._order = {inst: sorted(bars) for inst, bars in by_instrument.items()}

    def bar(self, instrument: str, ts: datetime) -> Any:
        candle = self._bars.get(instrument, {}).get(ts)
        if candle is None:
            raise FuturesReplayAdapterError(f"no {instrument} candle at {_iso(ts)}")
        return candle

    def span(self, instrument: str, start: datetime, end: datetime, *, include_start: bool) -> list[Any]:
        order = self._order.get(instrument, [])
        lo = bisect.bisect_left(order, start) if include_start else bisect.bisect_right(order, start)
        hi = bisect.bisect_right(order, end)
        bars = self._bars[instrument] if order else {}
        return [bars[ts] for ts in order[lo:hi]]


def _bar_minutes(candle: Any) -> int:
    from webhook.runner import normalize_timeframe_minutes

    minutes = normalize_timeframe_minutes(getattr(candle, "timeframe", None))
    if not minutes or minutes <= 0:
        raise FuturesReplayAdapterError(
            f"cannot resolve bar duration for timeframe {getattr(candle, 'timeframe', None)!r}"
        )
    return int(minutes)


def _finite(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise FuturesReplayAdapterError(f"{field_name} must be a finite number; got {value!r}")
    return float(value)


def translate_journal(
    *,
    decisions: list[dict[str, Any]],
    outcomes: dict[str, dict[str, Any]],
    candles: _CandleIndex,
    model: ReplayExecutionModel,
    execution_model_id: str,
    data_fingerprint: str,
    arm: str,
) -> list[dict[str, Any]]:
    """Translate replay journal decisions + outcomes into trade_execution rows."""
    from config.futures_contracts import contract_economics

    rows: list[dict[str, Any]] = []
    used: set[str] = set()
    for decision in decisions:
        order_id = str(decision["paper_order_id"])
        setup = decision.get("setup") or {}
        instrument = str(decision.get("instrument") or "")
        strategy = str(setup.get("strategy") or "")
        if not instrument or not strategy:
            raise FuturesReplayAdapterError(f"{order_id}: decision missing instrument/strategy")
        if strategy in UNSUPPORTED_STRATEGIES:
            raise FuturesReplayAdapterError(
                f"{order_id}: strategy {strategy!r} resolves through the intrabar "
                "2-1-2/1-2-2 restore path, which futures_replay does not support"
            )
        direction = str(setup.get("direction") or "").upper()
        entry = _finite(setup.get("entry"), field_name=f"{order_id} setup.entry")
        stop = _finite(setup.get("stop"), field_name=f"{order_id} setup.stop")
        target = _finite(setup.get("target"), field_name=f"{order_id} setup.target")
        signal_dt = _parse_utc(decision.get("bar_ts"), field_name=f"{order_id} bar_ts")
        signal_bar = candles.bar(instrument, signal_dt)
        decision_dt = signal_dt + timedelta(minutes=_bar_minutes(signal_bar))
        signal_ts = _iso(signal_dt)
        base = {
            "evidence_type": evidence_contract.EVIDENCE_TYPE_TRADE_EXECUTION,
            "arm": arm,
            "instrument": instrument,
            "strategy_identity": strategy,
            "candidate_signal_id": candidate_signal_id(
                instrument=instrument,
                strategy=strategy,
                signal_ts=signal_ts,
                direction=direction,
                entry=entry,
                stop=stop,
                target=target,
            ),
            "signal_ts": signal_ts,
            "decision_ts": _iso(decision_dt),
            "earliest_legal_order_ts": _iso(decision_dt),
            "intended_entry": entry,
            "stop": stop,
            "target": target,
            "data_fingerprint": data_fingerprint,
            "execution_model_id": execution_model_id,
            "session": decision.get("session"),
        }
        outcome = outcomes.get(order_id)

        if decision.get("decision") == "RISK_REJECTED":
            if outcome is not None:
                raise FuturesReplayAdapterError(f"{order_id}: RISK_REJECTED decision has an OUTCOME")
            risk = decision.get("risk_check") or {}
            rows.append(
                {
                    **base,
                    "direction": direction or None,
                    "fill_state": evidence_contract.FILL_STATE_NO_FILL,
                    "reject_reason": str(
                        risk.get("failed_rule") or risk.get("reason") or decision.get("reason") or "RISK_REJECTED"
                    ),
                }
            )
            continue

        if outcome is None:
            raise FuturesReplayAdapterError(
                f"{order_id}: {strategy} {direction} signal at {signal_ts} has no OUTCOME "
                "(position unresolved by the replayed data)"
            )
        used.add(order_id)
        audit = outcome.get("execution_audit") or {}
        if audit.get("source") == "strat_212_122_same_bar_resolution":
            raise FuturesReplayAdapterError(
                f"{order_id}: same-bar 2-1-2/1-2-2 resolution is not supported"
            )
        if str(outcome.get("signal_timestamp") or "") and _parse_utc(
            outcome.get("signal_timestamp"), field_name=f"{order_id} signal_timestamp"
        ) != signal_dt:
            raise FuturesReplayAdapterError(f"{order_id}: OUTCOME signal_timestamp != decision bar_ts")
        result = str(outcome.get("result") or "").upper()

        if result in NO_FILL_RESULTS:
            rows.append(
                {
                    **base,
                    "direction": direction or None,
                    "fill_state": evidence_contract.FILL_STATE_NO_FILL,
                    "no_fill_reason": str(outcome.get("exit_reason") or result),
                }
            )
            continue
        if result not in FILLED_RESULTS:
            raise FuturesReplayAdapterError(f"{order_id}: unsupported OUTCOME result {result!r}")

        contracts = int(outcome.get("contracts") or 0)
        if contracts <= 0:
            raise FuturesReplayAdapterError(f"{order_id}: contracts must be positive")
        fill_price = _finite(outcome.get("entry_price"), field_name=f"{order_id} entry_price")
        exit_price = _finite(outcome.get("exit_price"), field_name=f"{order_id} exit_price")
        gross = _finite(outcome.get("pnl_dollars"), field_name=f"{order_id} pnl_dollars")
        tick, tick_value = contract_economics(instrument)
        sign = 1.0 if direction == "LONG" else -1.0 if direction == "SHORT" else 0.0
        if sign == 0.0:
            raise FuturesReplayAdapterError(f"{order_id}: direction {direction!r} unsupported")
        recomputed = sign * (exit_price - fill_price) / tick * tick_value * contracts
        if abs(recomputed - gross) > 0.01 + 1e-9:
            raise FuturesReplayAdapterError(
                f"{order_id}: journal pnl_dollars {gross} != price-derived {round(recomputed, 2)}"
            )

        resolution_raw = audit.get("historical_resolution_bar_ts")
        if not resolution_raw:
            raise FuturesReplayAdapterError(f"{order_id}: OUTCOME missing historical_resolution_bar_ts")
        resolution_dt = _parse_utc(resolution_raw, field_name=f"{order_id} resolution bar")
        resolution_bar = candles.bar(instrument, resolution_dt)
        exit_dt = resolution_dt + timedelta(minutes=_bar_minutes(resolution_bar))

        if model.entry_fill_model == "stop_market":
            entry_raw = audit.get("historical_entry_bar_ts")
            if not entry_raw:
                raise FuturesReplayAdapterError(f"{order_id}: stop_market fill without entry bar")
            fill_dt = _parse_utc(entry_raw, field_name=f"{order_id} entry bar")
            path = candles.span(instrument, fill_dt, resolution_dt, include_start=True)
        else:
            fill_dt = decision_dt
            path = candles.span(instrument, signal_dt, resolution_dt, include_start=False)
        if fill_dt < decision_dt:
            raise FuturesReplayAdapterError(f"{order_id}: fill bar precedes decision time")
        if not path:
            raise FuturesReplayAdapterError(f"{order_id}: no bars between fill and resolution")
        if sign > 0:
            favorable = max(c.high - fill_price for c in path)
            adverse = min(c.low - fill_price for c in path)
        else:
            favorable = max(fill_price - c.low for c in path)
            adverse = min(fill_price - c.high for c in path)

        costs = round(
            (model.commission_per_contract_per_side + model.fees_per_contract_per_side) * 2 * contracts,
            2,
        )
        net = round(gross - costs, 2)
        risk_dollars = abs(fill_price - stop) / tick * tick_value * contracts
        if risk_dollars <= 0:
            raise FuturesReplayAdapterError(f"{order_id}: zero initial risk; R multiple undefined")
        rows.append(
            {
                **base,
                "direction": direction,
                "fill_state": evidence_contract.FILL_STATE_FILLED,
                "fill_price": fill_price,
                "fill_ts": _iso(fill_dt),
                "exit_price": exit_price,
                "exit_ts": _iso(exit_dt),
                "exit_reason": str(outcome.get("exit_reason") or ""),
                "replay_result": result,
                "contracts": contracts,
                "mfe": round(max(0.0, favorable), 6),
                "mae": round(min(0.0, adverse), 6),
                "excursion_unit": "price_points_bar_granular",
                "gross_pnl": round(gross, 2),
                "costs_fees": costs,
                "net_pnl": net,
                "r_multiple": round(net / risk_dollars, 6),
            }
        )

    orphans = sorted(set(outcomes) - used)
    if orphans:
        raise FuturesReplayAdapterError(f"OUTCOME rows without a TRADE decision: {orphans[:3]}")
    return rows


# ─── Adapter entry point ────────────────────────────────────────────────────


def run_futures_replay(ctx: ExperimentContext) -> ArmRawResult:
    from context.htf_loader import HTFLookup
    from execution import paper_mirror_hook
    from replay.replay_engine import ReplayEngine

    spec = ctx.spec
    arm = _arm(ctx)
    try:
        evidence_type = evidence_contract.resolve_evidence_type(spec)
    except evidence_contract.EvidenceContractError as exc:
        raise FuturesReplayAdapterError(str(exc)) from exc
    if evidence_type != evidence_contract.EVIDENCE_TYPE_TRADE_EXECUTION:
        raise FuturesReplayAdapterError("futures_replay requires evidence_type=trade_execution")
    if paper_mirror_hook.mirror_enabled():
        raise FuturesReplayAdapterError(
            "paper broker mirror is enabled; replay evidence must never mirror to a broker"
        )
    code_sha = _require_code_identity(spec)
    assumptions = spec.get("execution_assumptions")
    model = parse_execution_assumptions(assumptions)
    model_id = evidence_contract.execution_model_id(assumptions)
    fingerprint = evidence_contract.data_identity_from_spec(spec)
    dataset = bind_dataset(ctx.root, spec)

    window = ctx.partition_window
    if (ctx.evaluation_partition is None) != (window is None):
        raise FuturesReplayAdapterError("evaluation partition and window must be set together")
    replay_end = None
    window_bounds = None
    if window is not None:
        window_bounds = partition_contract.window_bounds(window)
        replay_end = window_bounds[1]

    base_config = BASE_CONFIG_PROVIDER()
    overrides = arm_overrides(spec, arm, base_config)

    with tempfile.TemporaryDirectory(prefix="afs-futures-replay-") as tmp:
        work = Path(tmp)
        log_dir = work / "journal"
        log_dir.mkdir()
        staging = work / "candles"
        staging.mkdir()
        staged_manifest, file_stats = stage_candles(dataset, staging, replay_end=replay_end)
        config = build_arm_config(base_config, model, overrides, log_dir=str(log_dir))
        htf = HTFLookup()
        for item in dataset.htf:
            htf.load(item["resolved_path"], timeframe=str(item["timeframe"]))
        engine = ReplayEngine(config=config, log_dir=str(log_dir), htf_lookup=htf)
        engine.run_manifest(staged_manifest)
        decisions, outcomes = _read_journal(log_dir)

    rows = translate_journal(
        decisions=decisions,
        outcomes=outcomes,
        candles=_CandleIndex(dataset),
        model=model,
        execution_model_id=model_id,
        data_fingerprint=fingerprint,
        arm=arm,
    )
    if window_bounds is not None:
        start, end = window_bounds
        members = [
            row for row in rows
            if start <= _parse_utc(row["signal_ts"], field_name="signal_ts") < end
        ]
    else:
        members = rows

    return ArmRawResult(
        arm=arm,
        commit_sha=code_sha,
        members=members,
        raw={
            "adapter": SETUP_TYPE,
            "adapter_version": ADAPTER_VERSION,
            "arm": arm,
            "code_sha": code_sha,
            "dataset_manifest": str(spec["data"]["dataset_id"]),
            "dataset_manifest_sha256": dataset.manifest_sha256,
            "candle_files": file_stats,
            "htf_files": [
                {"timeframe": item["timeframe"], "path": str(item["path"]), "sha256": item["sha256"]}
                for item in dataset.htf
            ],
            "evaluation_partition": ctx.evaluation_partition,
            "partition_window": dict(window) if window is not None else None,
            "replay_truncated_at": _iso(replay_end) if replay_end is not None else None,
            "execution_model": asdict(model),
            "execution_model_id": model_id,
            "base_config_sha256": _config_snapshot_sha256(base_config),
            "arm_config_overrides": overrides,
            "arm_config_sha256": _config_snapshot_sha256(config),
            "candidates_total": len(rows),
            "members_in_window": len(members),
            "candidates_outside_window": len(rows) - len(members),
        },
        warnings=[
            "Strategy configuration outside changed_variables and the frozen execution "
            "model resolves from risk_rules.yaml at the executing SHA plus process "
            "environment; base_config_sha256 records the resolved snapshot."
        ],
    )
