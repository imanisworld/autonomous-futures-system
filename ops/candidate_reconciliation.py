"""Standing same-candidate reconciliation: replay ↔ paper ↔ demo (U9).

Read-only. Never mutates journals or broker state, never submits orders, never
"repairs" a divergence. It compares the SAME canonical candidate across every
mode supplied and reports machine-readable PASS / DIVERGED / INCOMPLETE.

Candidate identity: ``instrument | strategy | signal bar timestamp (UTC)``.

Sources per mode (each optional; at least two required):

* ``journal`` — a directory of ``journal_YYYY-MM-DD.jsonl`` files written by
  the replay engine, the paper runner, or the Tradovate demo runner. TRADE /
  RISK_REJECTED decisions are joined to OUTCOME rows by ``paper_order_id``.
* ``bundle`` — a canonical U1–U4 evidence bundle (candidate-arm
  ``trade_execution`` members), e.g. from the U3 ``futures_replay`` adapter.

Compared fields:

* core (every mode must supply them, else INCOMPLETE): ``direction``,
  ``intended_entry``, ``stop``, ``target``, ``fill_state``, and — for filled
  candidates — ``outcome``;
* optional (compared only when two modes both supply them; otherwise listed
  as not comparable): ``fill_price``, ``exit_price``, ``exit_reason``,
  ``earliest_legal_order_ts``, ``risk_rejection``, ``costs_fees``.

Prices compare on the contract's tick grid (``config/futures_contracts.py``)
with an explicit tolerance in ticks (default 0). Any compared difference is
DIVERGED; a candidate absent from a supplied mode, a missing core field, an
unknown contract or a duplicated identity is INCOMPLETE. Overall status is the
worst candidate status (DIVERGED > INCOMPLETE > PASS); no candidates at all is
INCOMPLETE.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SCHEMA_VERSION = "1.0.0"
PASS, DIVERGED, INCOMPLETE = "PASS", "DIVERGED", "INCOMPLETE"
_RANK = {PASS: 0, INCOMPLETE: 1, DIVERGED: 2}
MODES = ("replay", "paper", "demo")
FILLED, NO_FILL, REJECTED = "FILLED", "NO_FILL", "RISK_REJECTED"
NO_FILL_RESULTS = frozenset({"CANCELLED", "VOID"})
CORE_FIELDS = ("direction", "intended_entry", "stop", "target", "fill_state")
OPTIONAL_FIELDS = (
    "fill_price", "exit_price", "exit_reason", "earliest_legal_order_ts",
    "risk_rejection", "costs_fees",
)
PRICE_FIELDS = frozenset({"intended_entry", "stop", "target", "fill_price", "exit_price"})


class ReconciliationError(RuntimeError):
    """Inputs cannot be read safely."""


@dataclass
class CandidateRecord:
    mode: str
    key: str
    instrument: str
    strategy: str
    signal_ts: str
    fields: dict[str, Any] = field(default_factory=dict)


def _utc(value: Any) -> Optional[str]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def identity_key(instrument: str, strategy: str, signal_ts: str) -> str:
    return f"{instrument}|{strategy}|{signal_ts}"


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


# ─── Normalizers ────────────────────────────────────────────────────────────


def records_from_journal_rows(mode: str, rows: Iterable[Mapping[str, Any]]) -> list[CandidateRecord]:
    decisions: list[Mapping[str, Any]] = []
    outcomes: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if row.get("type") == "OUTCOME":
            outcome = row.get("outcome")
            if not isinstance(outcome, Mapping):
                raise ReconciliationError(f"{mode}: OUTCOME row has no object outcome")
            order_id = outcome.get("paper_order_id")
            if not order_id:
                raise ReconciliationError(f"{mode}: OUTCOME row missing paper_order_id")
            order_id = str(order_id)
            if order_id in outcomes:
                raise ReconciliationError(f"{mode}: duplicate OUTCOME for {order_id}")
            outcomes[order_id] = {**outcome, "instrument": row.get("instrument")}
        elif row.get("decision") in ("TRADE", "RISK_REJECTED"):
            if not isinstance(row.get("setup"), Mapping):
                raise ReconciliationError(f"{mode}: {row.get('decision')} row missing setup object")
            decisions.append(row)

    records: list[CandidateRecord] = []
    used: set[str] = set()
    for row in decisions:
        setup = row["setup"]
        instrument = str(row.get("instrument") or "").upper()
        strategy = str(setup.get("strategy") or "")
        signal_ts = _utc(row.get("bar_ts") or row.get("ts"))
        if not instrument or not strategy or signal_ts is None:
            raise ReconciliationError(
                f"{mode}: decision row missing canonical instrument/strategy/signal timestamp"
            )
        fields: dict[str, Any] = {
            "direction": str(setup.get("direction") or "").upper() or None,
            "intended_entry": _num(row.get("requested_entry", setup.get("entry"))),
            "stop": _num(setup.get("stop")),
            "target": _num(setup.get("target")),
        }
        if row.get("decision") == "RISK_REJECTED":
            risk = row.get("risk_check") or {}
            fields["fill_state"] = REJECTED
            fields["risk_rejection"] = risk.get("failed_rule") or risk.get("reason") or "RISK_REJECTED"
        else:
            order_id = str(row.get("paper_order_id") or "")
            if not order_id:
                raise ReconciliationError(f"{mode}: TRADE row missing paper_order_id")
            outcome = outcomes.get(order_id)
            if outcome is not None:
                used.add(order_id)
            result = str((outcome or {}).get("result") or "").upper()
            if outcome is None:
                fields["fill_state"] = FILLED
                fields["outcome"] = None  # still open / not yet resolved
            elif result in NO_FILL_RESULTS:
                fields["fill_state"] = NO_FILL
                fields["no_fill_reason"] = outcome.get("no_fill_reason") or outcome.get("exit_reason")
            else:
                fields["fill_state"] = FILLED
                fields["outcome"] = result or None
                fields["fill_price"] = _num(outcome.get("entry_price"))
                fields["exit_price"] = _num(outcome.get("exit_price"))
                fields["exit_reason"] = outcome.get("exit_reason")
        records.append(
            CandidateRecord(mode, identity_key(instrument, strategy, signal_ts), instrument, strategy,
                            signal_ts, fields)
        )
    # Any OUTCOME without a matching decision remains visible. It cannot PASS
    # because the decision-side bracket fields are unavailable, but silently
    # dropping it could make a partial/corrupt journal look reconciled.
    for order_id, outcome in outcomes.items():
        if order_id in used:
            continue
        instrument = str(outcome.get("instrument") or "").upper()
        strategy = str(outcome.get("strategy") or "")
        signal_ts = _utc(outcome.get("signal_timestamp"))
        if not instrument or not strategy or signal_ts is None:
            raise ReconciliationError(
                f"{mode}: unmatched OUTCOME {order_id} missing canonical identity"
            )
        result = str(outcome.get("result") or "").upper()
        fields: dict[str, Any] = {
            "intended_entry": _num(outcome.get("requested_entry") or outcome.get("entry_price")),
        }
        if result in NO_FILL_RESULTS:
            fields.update(
                fill_state=NO_FILL,
                no_fill_reason=outcome.get("no_fill_reason") or outcome.get("exit_reason"),
            )
        else:
            fields.update(
                fill_state=FILLED,
                outcome=result or None,
                fill_price=_num(outcome.get("entry_price")),
                exit_price=_num(outcome.get("exit_price")),
                exit_reason=outcome.get("exit_reason"),
            )
        records.append(
            CandidateRecord(
                mode, identity_key(instrument, strategy, signal_ts), instrument, strategy, signal_ts, fields
            )
        )
    return records


def records_from_bundle_members(mode: str, members: Iterable[Mapping[str, Any]]) -> list[CandidateRecord]:
    records: list[CandidateRecord] = []
    for row in members:
        instrument = str(row.get("instrument") or "").upper()
        strategy = str(row.get("strategy_identity") or "")
        signal_ts = _utc(row.get("signal_ts"))
        if not instrument or not strategy or signal_ts is None:
            continue
        state = str(row.get("fill_state") or "").upper()
        fields: dict[str, Any] = {
            "direction": str(row.get("direction") or "").upper() or None,
            "intended_entry": _num(row.get("intended_entry")),
            "stop": _num(row.get("stop")),
            "target": _num(row.get("target")),
            "earliest_legal_order_ts": _utc(row.get("earliest_legal_order_ts")),
        }
        if state == FILLED:
            fields.update(
                fill_state=FILLED,
                outcome=(str(row["replay_result"]).upper() if row.get("replay_result") else None),
                fill_price=_num(row.get("fill_price")),
                exit_price=_num(row.get("exit_price")),
                exit_reason=row.get("exit_reason"),
                costs_fees=_num(row.get("costs_fees")),
            )
        elif row.get("reject_reason"):
            fields.update(fill_state=REJECTED, risk_rejection=row.get("reject_reason"))
        else:
            fields.update(fill_state=NO_FILL, no_fill_reason=row.get("no_fill_reason"))
        records.append(
            CandidateRecord(mode, identity_key(instrument, strategy, signal_ts), instrument, strategy,
                            signal_ts, fields)
        )
    return records


# ─── Comparison ─────────────────────────────────────────────────────────────


def _equal(field_name: str, a: Any, b: Any, *, tick: Optional[float], tolerance_ticks: float) -> bool:
    if field_name in PRICE_FIELDS:
        fa, fb = _num(a), _num(b)
        if fa is None or fb is None or tick is None:
            return False
        return abs(fa - fb) <= tolerance_ticks * tick + 1e-9
    if field_name == "costs_fees":
        fa, fb = _num(a), _num(b)
        return fa is not None and fb is not None and abs(fa - fb) <= 0.005
    return a == b


def reconcile(
    by_mode: Mapping[str, list[CandidateRecord]],
    *,
    tolerance_ticks: float = 0.0,
) -> dict[str, Any]:
    from config.futures_contracts import UnsupportedContractError, contract_economics

    if (
        not isinstance(tolerance_ticks, (int, float))
        or isinstance(tolerance_ticks, bool)
        or not math.isfinite(float(tolerance_ticks))
        or tolerance_ticks < 0
    ):
        raise ReconciliationError("tolerance_ticks must be a finite non-negative number")
    modes = [m for m in MODES if m in by_mode]
    if len(modes) < 2:
        raise ReconciliationError("reconciliation needs at least two modes")

    indexed: dict[str, dict[str, list[CandidateRecord]]] = {m: {} for m in modes}
    for mode in modes:
        for record in by_mode[mode]:
            indexed[mode].setdefault(record.key, []).append(record)
    keys = sorted(set().union(*(indexed[m].keys() for m in modes)))

    candidates: list[dict[str, Any]] = []
    for key in keys:
        problems: list[str] = []
        divergences: list[dict[str, Any]] = []
        not_comparable: list[str] = []
        present: dict[str, CandidateRecord] = {}
        for mode in modes:
            found = indexed[mode].get(key, [])
            if not found:
                problems.append(f"absent in {mode}")
            elif len(found) > 1:
                problems.append(f"duplicate identity in {mode} ({len(found)} records)")
            else:
                present[mode] = found[0]
        instrument = key.split("|", 1)[0]
        try:
            tick: Optional[float] = contract_economics(instrument)[0]
        except UnsupportedContractError:
            tick = None
            problems.append(f"no contract metadata for {instrument}")

        if len(present) >= 2:
            filled_somewhere = any(r.fields.get("fill_state") == FILLED for r in present.values())
            no_fill_somewhere = any(r.fields.get("fill_state") == NO_FILL for r in present.values())
            core = list(CORE_FIELDS)
            if filled_somewhere:
                core.append("outcome")
            if no_fill_somewhere:
                core.append("no_fill_reason")
            for name in core + list(OPTIONAL_FIELDS):
                supplied = {m: r.fields.get(name) for m, r in present.items() if r.fields.get(name) is not None}
                if name in core:
                    missing = sorted(set(present) - set(supplied))
                    if missing:
                        problems.append(f"{name} missing in {', '.join(missing)}")
                if len(supplied) < 2 or (name in PRICE_FIELDS and tick is None):
                    if name not in core or tick is None:
                        not_comparable.append(name)
                    continue
                items = sorted(supplied.items(), key=lambda kv: MODES.index(kv[0]))
                base_mode, base_value = items[0]
                for other_mode, other_value in items[1:]:
                    if not _equal(name, base_value, other_value, tick=tick, tolerance_ticks=tolerance_ticks):
                        divergences.append(
                            {"field": name, base_mode: base_value, other_mode: other_value}
                        )
        status = DIVERGED if divergences else INCOMPLETE if problems else PASS
        candidates.append(
            {
                "key": key,
                "status": status,
                "modes_present": sorted(present, key=MODES.index),
                "divergences": divergences,
                "incomplete_reasons": problems,
                "not_comparable": sorted(set(not_comparable)),
                "by_mode": {m: r.fields for m, r in present.items()},
            }
        )

    counts = {s: sum(1 for c in candidates if c["status"] == s) for s in (PASS, DIVERGED, INCOMPLETE)}
    overall = (
        INCOMPLETE if not candidates
        else max((c["status"] for c in candidates), key=_RANK.__getitem__)
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "status": overall,
        "modes": modes,
        "tolerance_ticks": tolerance_ticks,
        "counts": counts,
        "candidates": candidates,
        "read_only": True,
    }


# ─── IO / CLI ───────────────────────────────────────────────────────────────


def read_journal_dir(path: Path, *, since: Optional[date], until: Optional[date]) -> list[dict]:
    if not path.is_dir():
        raise ReconciliationError(f"journal directory missing: {path}")
    rows: list[dict] = []
    for file in sorted(path.glob("journal_*.jsonl")):
        try:
            day = date.fromisoformat(file.stem.split("_", 1)[1])
        except ValueError:
            continue
        if (since and day < since) or (until and day > until):
            continue
        for lineno, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ReconciliationError(f"{file.name}:{lineno} unreadable journal row") from exc
            if not isinstance(row, dict):
                raise ReconciliationError(f"{file.name}:{lineno} journal row must be an object")
            rows.append(row)
    return rows


def read_bundle_members(bundle: Path, *, repo_root: Path = ROOT) -> list[dict]:
    """Read candidate rows only after the existing U4 identity gate approves them."""
    from ops import evidence_identity as identity_gate

    verdict = identity_gate.classify_evidence_bundle(repo_root, bundle)
    if not verdict.promotion_quality:
        detail = "; ".join(verdict.reasons) or "no promotion-quality identity"
        raise ReconciliationError(
            f"canonical bundle is {verdict.status}, not PROMOTION_QUALITY: {detail}"
        )
    path = bundle / "candidate_raw.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReconciliationError(f"canonical bundle unreadable: {path}: {exc}") from exc
    members = payload.get("members") if isinstance(payload, dict) else None
    if not isinstance(members, list):
        raise ReconciliationError(f"canonical bundle has no members: {path}")
    return members


def _assert_safe_report_path(out: Optional[Path], inputs: Iterable[Optional[Path]]) -> None:
    """A report may never overwrite or be created inside supplied evidence inputs."""
    if out is None:
        return
    target = out.resolve()
    for source in inputs:
        if source is None:
            continue
        root = source.resolve()
        try:
            target.relative_to(root)
        except ValueError:
            continue
        raise ReconciliationError(
            f"report output {out} is inside supplied evidence input {source}; refusing mutation"
        )


def _filter(records: list[CandidateRecord], *, instrument: Optional[str], strategy: Optional[str],
            since: Optional[date], until: Optional[date]) -> list[CandidateRecord]:
    out = []
    for r in records:
        day = date.fromisoformat(r.signal_ts[:10])
        if instrument and r.instrument != instrument.upper():
            continue
        if strategy and r.strategy != strategy:
            continue
        if (since and day < since) or (until and day > until):
            continue
        out.append(r)
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Replay/paper/demo same-candidate reconciliation (read-only).")
    parser.add_argument("--replay-journal", type=Path)
    parser.add_argument("--replay-bundle", type=Path)
    parser.add_argument("--paper-journal", type=Path)
    parser.add_argument("--demo-journal", type=Path)
    parser.add_argument("--instrument")
    parser.add_argument("--strategy")
    parser.add_argument("--since", type=date.fromisoformat)
    parser.add_argument("--until", type=date.fromisoformat)
    parser.add_argument("--tolerance-ticks", type=float, default=0.0)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.replay_journal and args.replay_bundle:
            raise ReconciliationError("give --replay-journal or --replay-bundle, not both")
        _assert_safe_report_path(
            args.out,
            (args.replay_journal, args.replay_bundle, args.paper_journal, args.demo_journal),
        )
        by_mode: dict[str, list[CandidateRecord]] = {}
        if args.replay_bundle:
            by_mode["replay"] = records_from_bundle_members("replay", read_bundle_members(args.replay_bundle))
        elif args.replay_journal:
            by_mode["replay"] = records_from_journal_rows(
                "replay", read_journal_dir(args.replay_journal, since=None, until=None))
        for mode, path in (("paper", args.paper_journal), ("demo", args.demo_journal)):
            if path:
                by_mode[mode] = records_from_journal_rows(mode, read_journal_dir(path, since=None, until=None))
        by_mode = {
            m: _filter(r, instrument=args.instrument, strategy=args.strategy, since=args.since, until=args.until)
            for m, r in by_mode.items()
        }
        report = reconcile(by_mode, tolerance_ticks=args.tolerance_ticks)
    except ReconciliationError as exc:
        print(f"RECONCILIATION ERROR: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(report, indent=2, sort_keys=True, default=str)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return {PASS: 0, INCOMPLETE: 3, DIVERGED: 4}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
