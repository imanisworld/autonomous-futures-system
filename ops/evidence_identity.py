"""Mandatory evidence identity gate for promotion-quality futures evidence (U4).

Classifies one evidence bundle directory as:

* ``PROMOTION_QUALITY`` — a canonical runner bundle whose identity is complete,
  internally consistent, registered in the trial ledger, and byte-verified;
* ``REFERENCE_ONLY`` — historical/legacy or non-promotion evidence (no
  canonical envelope, coverage evidence, or an envelope that does not claim
  promotion eligibility). It may be read as reference, never counted as
  promotion-quality;
* ``INVALID`` — a bundle that claims promotion eligibility but whose identity
  is absent, malformed, contradictory, edited after writing, or points at
  unregistered evidence.

Read-only. Never fabricates identity for historical evidence and never
rewrites bundles or ledgers. Zero promotion / deploy authority: U5 decides how
promotion consumes this classification.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Optional

from ops import evidence_row as evidence_contract
from ops import experiment_partitions as partition_contract

PROMOTION_QUALITY = "PROMOTION_QUALITY"
REFERENCE_ONLY = "REFERENCE_ONLY"
INVALID = "INVALID"

EVIDENCE_ROOT_REL = "docs/research-evidence"
SPECS_DIR_REL = "docs/research-experiment-specs"
TRIAL_LEDGER_REL = "docs/research-trial-ledger.jsonl"
ENVELOPE_FILENAME = "evidence_envelope.json"
BUNDLE_MANIFEST_FILENAME = "bundle_manifest.json"
REQUIRED_BUNDLE_FILES = (
    "experiment_spec.json",
    "runner_report.json",
    ENVELOPE_FILENAME,
    "reproduction.json",
    "baseline_raw.json",
    "candidate_raw.json",
)
REQUIRED_PROMOTION_IDENTITY = (
    "experiment_id",
    "trial_id",
    "preregistration_identity",
    "strategy_identity",
    "code_sha",
    "data_identity",
    "evaluation_partition",
    "execution_model_id",
)
REGISTERED_FIRST_EVENTS = frozenset({"PLANNED", "ADOPTED"})


@dataclass
class EvidenceIdentityResult:
    status: str
    bundle: str
    trial_id: Optional[str] = None
    reasons: list[str] = field(default_factory=list)
    identity: dict[str, Any] = field(default_factory=dict)

    @property
    def promotion_quality(self) -> bool:
        return self.status == PROMOTION_QUALITY

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "bundle": self.bundle,
            "trial_id": self.trial_id,
            "reasons": list(self.reasons),
            "identity": dict(self.identity),
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _present(value: Any) -> bool:
    return value is not None and value != ""


def ledger_first_rows(root: Path) -> dict[str, dict[str, Any]]:
    path = root / TRIAL_LEDGER_REL
    first: dict[str, dict[str, Any]] = {}
    if not path.is_file():
        return first
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        trial = row.get("trial_id")
        if isinstance(trial, str) and trial not in first:
            first[trial] = row
    return first


def _relative_bundle(root: Path, bundle_dir: Path) -> Optional[PurePosixPath]:
    try:
        return PurePosixPath(bundle_dir.resolve().relative_to(root.resolve()).as_posix())
    except ValueError:
        return None


def classify_evidence_bundle(
    root: Path,
    bundle_dir: Path,
    *,
    first_rows: Optional[Mapping[str, dict[str, Any]]] = None,
) -> EvidenceIdentityResult:
    """Classify one bundle directory. Never raises for bad evidence."""
    root = Path(root)
    bundle_dir = Path(bundle_dir)
    rel = _relative_bundle(root, bundle_dir)
    label = rel.as_posix() if rel is not None else str(bundle_dir)
    envelope_path = bundle_dir / ENVELOPE_FILENAME

    if not envelope_path.is_file():
        return EvidenceIdentityResult(
            REFERENCE_ONLY,
            label,
            reasons=["no canonical evidence_envelope.json (legacy/historical evidence)"],
        )
    try:
        envelope = _load_json(envelope_path)
    except (OSError, ValueError) as exc:
        return EvidenceIdentityResult(INVALID, label, reasons=[f"envelope unreadable: {exc}"])
    if not isinstance(envelope, dict):
        return EvidenceIdentityResult(INVALID, label, reasons=["envelope must be an object"])

    trial_id = envelope.get("trial_id") if isinstance(envelope.get("trial_id"), str) else None
    if envelope.get("promotion_eligible") is not True:
        return EvidenceIdentityResult(
            REFERENCE_ONLY,
            label,
            trial_id=trial_id,
            reasons=[
                f"envelope does not claim promotion eligibility "
                f"(evidence_type={envelope.get('evidence_type')!r})"
            ],
        )

    reasons: list[str] = []
    fail = reasons.append

    # 1. Canonical envelope contract (U1).
    try:
        evidence_contract.validate_common_envelope(envelope, require_execution_model=True)
    except evidence_contract.EvidenceContractError as exc:
        fail(f"envelope contract: {exc}")
    if envelope.get("evidence_type") != evidence_contract.EVIDENCE_TYPE_TRADE_EXECUTION:
        fail("only trade_execution evidence can be promotion-quality")
    for key in REQUIRED_PROMOTION_IDENTITY:
        if not _present(envelope.get(key)):
            fail(f"missing required identity: {key}")
    data_identity = str(envelope.get("data_identity") or "")
    if not data_identity.startswith("dataset_hash:"):
        fail("data_identity must be a pinned dataset_hash fingerprint")

    # 2. Bundle location = registered evidence folder for this trial.
    if rel is None:
        fail("bundle is outside the repository")
    elif trial_id is None or rel != PurePosixPath(EVIDENCE_ROOT_REL) / trial_id:
        fail(f"bundle must live at {EVIDENCE_ROOT_REL}/<trial_id>/ (found {label})")

    # 3. Byte identity: manifest written last by the runner.
    manifest_path = bundle_dir / BUNDLE_MANIFEST_FILENAME
    manifest: dict[str, Any] = {}
    if not manifest_path.is_file():
        fail("missing bundle_manifest.json")
    else:
        try:
            loaded = _load_json(manifest_path)
            manifest = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError) as exc:
            fail(f"bundle_manifest.json unreadable: {exc}")
        files = manifest.get("files") if isinstance(manifest.get("files"), dict) else {}
        if not files:
            fail("bundle_manifest.json lists no files")
        for name in REQUIRED_BUNDLE_FILES:
            if name not in files:
                fail(f"bundle_manifest.json missing required file {name}")
        for name, digest in sorted(files.items()):
            path = bundle_dir / name
            if PurePosixPath(name).name != name:
                fail(f"bundle_manifest.json entry {name!r} must be a plain file name")
            elif not path.is_file():
                fail(f"bundle file missing: {name}")
            elif _sha256(path) != digest:
                fail(f"bundle file edited after writing: {name}")
        for key in ("experiment_id", "trial_id"):
            if manifest.get(key) != envelope.get(key):
                fail(f"bundle_manifest.json {key} contradicts the envelope")

    # 3b. Strategy identity must be anchored to the canonical trade rows.
    strategy_identities: set[str] = set()
    strategy_rows_valid = True
    for raw_name in ("baseline_raw.json", "candidate_raw.json"):
        try:
            raw_payload = _load_json(bundle_dir / raw_name)
        except (OSError, ValueError) as exc:
            strategy_rows_valid = False
            fail(f"{raw_name} unreadable for strategy identity: {exc}")
            continue
        members = raw_payload.get("members") if isinstance(raw_payload, dict) else None
        if not isinstance(members, list):
            strategy_rows_valid = False
            fail(f"{raw_name} must contain members[] for strategy identity")
            continue
        for index, row in enumerate(members):
            if not isinstance(row, dict):
                strategy_rows_valid = False
                fail(f"{raw_name}.members[{index}] must be an object")
                continue
            value = row.get("strategy_identity")
            if not isinstance(value, str) or not value.strip():
                strategy_rows_valid = False
                fail(f"{raw_name}.members[{index}] missing strategy_identity")
                continue
            strategy_identities.add(value.strip())
    if strategy_rows_valid:
        if len(strategy_identities) != 1:
            fail(
                "promotion-quality trade evidence requires exactly one canonical "
                f"strategy_identity across both arms; found {sorted(strategy_identities)}"
            )
        elif envelope.get("strategy_identity") != next(iter(strategy_identities)):
            fail(
                "strategy_identity contradicts the canonical baseline/candidate "
                "trade_execution rows"
            )

    # 4. Consistency with the bundled spec, runner report, and approved spec.
    spec: dict[str, Any] = {}
    try:
        loaded_spec = _load_json(bundle_dir / "experiment_spec.json")
        spec = loaded_spec if isinstance(loaded_spec, dict) else {}
    except (OSError, ValueError) as exc:
        fail(f"experiment_spec.json unreadable: {exc}")
    if spec:
        for key in ("experiment_id", "trial_id"):
            if spec.get(key) != envelope.get(key):
                fail(f"envelope {key} contradicts the bundled spec")
        if spec.get("status") != "APPROVED":
            fail(f"bundled spec status is {spec.get('status')!r}, not APPROVED")
        if spec.get("prereg_path") != envelope.get("preregistration_identity"):
            fail("preregistration_identity contradicts the bundled spec prereg_path")
        declared = {
            str((spec.get(arm) or {}).get("commit_sha") or "").lower()
            for arm in ("baseline", "candidate")
        }
        if str(envelope.get("code_sha") or "").lower() not in declared:
            fail("code_sha is not a commit declared by the bundled spec")
        try:
            if evidence_contract.data_identity_from_spec(spec) != envelope.get("data_identity"):
                fail("data_identity contradicts the bundled spec dataset identity")
        except evidence_contract.EvidenceContractError as exc:
            fail(f"bundled spec data identity: {exc}")
        try:
            expected_model = evidence_contract.execution_model_id(
                spec.get("execution_assumptions") or {}
            )
            if expected_model != envelope.get("execution_model_id"):
                fail("execution_model_id contradicts the bundled spec execution_assumptions")
        except evidence_contract.EvidenceContractError as exc:
            fail(f"bundled spec execution_assumptions: {exc}")
        if not partition_contract.partitions_declared(spec):
            fail("promotion-quality evidence requires declared chronological_partitions")
        declared_partition = spec.get("evaluation_partition")
        if _present(declared_partition) and declared_partition != envelope.get(
            "evaluation_partition"
        ):
            fail("evaluation_partition contradicts the bundled spec")
        approved_path = root / SPECS_DIR_REL / f"{envelope.get('experiment_id')}.json"
        if not approved_path.is_file():
            fail("no approved spec file for this experiment_id in the repository")
        else:
            try:
                if _load_json(approved_path) != spec:
                    fail("bundled spec differs from the repository's approved spec")
            except (OSError, ValueError) as exc:
                fail(f"approved spec unreadable: {exc}")
    try:
        report = _load_json(bundle_dir / "runner_report.json")
    except (OSError, ValueError) as exc:
        report = {}
        fail(f"runner_report.json unreadable: {exc}")
    if isinstance(report, dict) and report:
        if report.get("status") != "VALID":
            fail(f"runner_report status is {report.get('status')!r}, not VALID")
        for key in ("experiment_id", "trial_id"):
            if report.get(key) != envelope.get(key):
                fail(f"runner_report {key} contradicts the envelope")

    # 5. Registration (register-before-count) and prior exposure.
    rows = first_rows if first_rows is not None else ledger_first_rows(root)
    first = rows.get(trial_id or "")
    if first is None:
        fail("trial_id is not registered in the trial ledger")
    else:
        if first.get("event") not in REGISTERED_FIRST_EVENTS:
            fail(f"trial's first ledger event is {first.get('event')!r} (needs PLANNED/ADOPTED)")
        if first.get("prereg_path") != envelope.get("preregistration_identity"):
            fail("preregistration_identity contradicts the trial ledger prereg_path")
        if "prior_exposed" in first and envelope.get("trial_prior_exposed") != first.get(
            "prior_exposed"
        ):
            fail("trial_prior_exposed missing or contradicts the trial ledger prior_exposed")

    # 6. OOS consumption receipt binding (U2).
    receipt_path = bundle_dir / partition_contract.OOS_RECEIPT_FILENAME
    listed = set((manifest.get("files") or {}) if isinstance(manifest.get("files"), dict) else {})
    if envelope.get("evaluation_partition") == partition_contract.PARTITION_UNTOUCHED_OOS:
        if partition_contract.OOS_RECEIPT_FILENAME not in listed or not receipt_path.is_file():
            fail("untouched_oos evidence requires its oos_consumption_receipt.json")
        else:
            try:
                receipt = _load_json(receipt_path)
                ledger_receipt = partition_contract.find_oos_consumption(
                    root, trial_id=trial_id or ""
                )
            except (OSError, ValueError, partition_contract.PartitionContractError) as exc:
                receipt, ledger_receipt = {}, None
                fail(f"OOS receipt unreadable: {exc}")
            if ledger_receipt is None:
                fail("OOS receipt is not recorded in the OOS consumption ledger")
            elif ledger_receipt != receipt:
                fail("bundle OOS receipt contradicts the OOS consumption ledger")
            if isinstance(receipt, dict) and receipt:
                if str(receipt.get("code_sha") or "").lower() != str(
                    envelope.get("code_sha") or ""
                ).lower():
                    fail("OOS receipt code_sha contradicts the envelope")
                if f"dataset_hash:{receipt.get('dataset_hash')}" != envelope.get("data_identity"):
                    fail("OOS receipt dataset_hash contradicts the envelope")
    elif partition_contract.OOS_RECEIPT_FILENAME in listed:
        fail("non-OOS evidence must not carry an OOS consumption receipt")

    identity = {key: envelope.get(key) for key in REQUIRED_PROMOTION_IDENTITY}
    identity["trial_prior_exposed"] = envelope.get("trial_prior_exposed")
    identity["bundle_manifest_sha256"] = (
        _sha256(manifest_path) if manifest_path.is_file() else None
    )
    return EvidenceIdentityResult(
        INVALID if reasons else PROMOTION_QUALITY,
        label,
        trial_id=trial_id,
        reasons=reasons,
        identity=identity,
    )


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Classify an evidence bundle's identity.")
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    result = classify_evidence_bundle(args.repo_root, args.bundle)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0 if result.status != INVALID else 2


if __name__ == "__main__":
    raise SystemExit(main())
