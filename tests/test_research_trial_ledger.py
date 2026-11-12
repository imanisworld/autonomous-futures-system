"""Fail-closed governance checks for the append-only research trial ledger.

Repository governance only. These tests do not import runtime, broker, risk,
collector, strategy, or execution modules.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[1]
LEDGER_REL = "docs/research-trial-ledger.jsonl"
LEDGER = ROOT / LEDGER_REL
EVIDENCE_PREFIX = "docs/research-evidence/"
MANIFEST_PREFIX = "docs/research-trial-manifests/"

EVENTS = {
    "PLANNED",
    "ADOPTED",
    "RUNNING",
    "COMPLETED",
    "ABORTED",
    "SUPERSEDED",
    "UNREGISTERED_ATTEMPT",
    "GATE",
    "GATE_REVIEW",
    "SLICE",
}
FIRST_EVENTS = {"PLANNED", "ADOPTED", "UNREGISTERED_ATTEMPT"}
TERMINAL_EVENTS = {"COMPLETED", "ABORTED", "SUPERSEDED", "UNREGISTERED_ATTEMPT"}
DISPOSITIONS = {
    "VALIDATED",
    "PROMISING_BUT_UNPROVEN",
    "WAIT",
    "RESEARCH_ONLY",
    "BROKEN",
    "OVERFIT",
    "RETIRE",
    "NOT_RUN",
    "INVALID_EVIDENCE",
}
TRIAL_RE = re.compile(r"^T-\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*-\d{2}$")
FAMILY_RE = re.compile(r"^[a-z0-9_]+$")
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
REPLAY_LEDGER_RE = re.compile(r"^reproduction-replay-ledger(?:\.attempt-\d{2,})?\.json$")
RUN_PROVENANCE = frozenset({"gate_run.log", "run_gate.py", "sha256sums.txt"})
SLICES_DIRNAME = "slices"
SLICE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
COMBINE_NAME_RE = re.compile(r"^combine\.attempt-(\d{2,})\.json$")
# Exact result_artifact a COMPLETED line may use to cite a parent trial's combine file.
COMBINE_RESULT_RE = re.compile(
    r"^docs/research-evidence/(T-\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]*-\d{2})"
    r"/slices/combine\.attempt-\d{2,}\.json$"
)

REQUIRED = {
    "trial_id",
    "event",
    "recorded_at",
    "recorded_by",
    "prereg_path",
    "family_id",
    "family_label",
    "population",
    "variant_set",
    "prior_exposed",
}
# Trial-definition fields fixed by the first line; every later line for the same
# trial_id must restate them byte-for-byte (or omit an optional one identically).
FROZEN = {
    "prereg_path",
    "prereg_commit",
    "family_id",
    "family_label",
    "population",
    "variant_set",
    "prior_exposed",
}
# Stored commit SHAs are informational only. Branch commit SHAs do not survive a
# squash merge onto main, so provenance is always derived from `git log` on the
# checked-out history (see _first_add_commit / _first_trial_commit), never from
# a SHA written into a ledger row.
OPTIONAL_SHA_FIELDS = ("prereg_commit", "result_commit")


def _git(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=check,
    )


def _rows() -> list[dict]:
    assert LEDGER.exists(), f"missing {LEDGER_REL}"
    rows: list[dict] = []
    for lineno, raw in enumerate(LEDGER.read_text(encoding="utf-8").splitlines(), 1):
        assert raw.strip(), f"{LEDGER_REL}:{lineno}: blank lines are not allowed"
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise AssertionError(f"{LEDGER_REL}:{lineno}: invalid JSON: {exc}") from exc
        assert isinstance(row, dict), f"{LEDGER_REL}:{lineno}: row must be an object"
        rows.append(row)
    assert rows, "ledger must contain the adopted bootstrap lanes"
    return rows


def _utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert dt.utcoffset() == timedelta(0), f"recorded_at must be UTC: {value}"
    return dt


def _first_add_commit(path: str) -> str:
    out = _git("log", "--diff-filter=A", "--format=%H", "--reverse", "--", path).stdout.splitlines()
    assert out, f"cannot find first-add commit for {path}"
    return out[0].strip()


def _first_trial_commit(trial_id: str) -> str:
    out = _git(
        "log",
        f"-S{trial_id}",
        "--format=%H",
        "--reverse",
        "--",
        LEDGER_REL,
    ).stdout.splitlines()
    assert out, f"cannot find first ledger commit for {trial_id}"
    return out[0].strip()


def _is_ancestor(older: str, newer: str) -> bool:
    proc = _git("merge-base", "--is-ancestor", older, newer, check=False)
    return proc.returncode == 0


def _normalized_family(value: str) -> str:
    return re.sub(r"[_\-\s]", "", value).lower()


def _first_rows(rows: list[dict]) -> dict[str, dict]:
    first: dict[str, dict] = {}
    for row in rows:
        first.setdefault(row["trial_id"], row)
    return first


def _read_trial_id_from_artifact(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        payload = json.loads(text)
        assert isinstance(payload, dict), f"{path}: JSON evidence must be an object"
        trial_id = payload.get("trial_id")
        assert isinstance(trial_id, str), f"{path}: missing top-level trial_id"
        return trial_id

    front = re.search(r"(?m)^\s*trial_id:\s*([^\s]+)\s*$", text)
    if front:
        return front.group(1)
    comment = re.search(r"<!--\s*trial_id:\s*([^\s]+)\s*-->", text)
    assert comment, f"{path}: missing machine-readable trial_id"
    return comment.group(1)


def _is_clean_rel(value: object) -> bool:
    """A normalized, repo-relative POSIX path with no traversal segments."""
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return False
    if value.startswith("/"):
        return False
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    return PurePosixPath(value).as_posix() == value


def _slices_prefix(trial_id: str) -> str:
    return f"{EVIDENCE_PREFIX}{trial_id}/{SLICES_DIRNAME}/"


def _assert_slice_product(
    row: dict,
    lineno: int,
    path_key: str,
    sha_key: str,
    name_re: re.Pattern[str],
    root: Path,
) -> str | None:
    """Check one (path, sha256) pair on a SLICE line. Returns the attempt label or None."""
    rel = row.get(path_key)
    digest = row.get(sha_key)
    if rel is None and digest is None:
        return None
    where = f"line {lineno}: {row['trial_id']}: SLICE {path_key}"
    assert _is_clean_rel(rel), f"{where} must be a clean repo-relative path"
    prefix = _slices_prefix(row["trial_id"])
    assert rel.startswith(prefix), f"{where} must live under {prefix}"
    name = rel[len(prefix):]
    match = name_re.fullmatch(name)
    assert match, f"{where} name {name!r} does not match slice_id {row['slice_id']!r}"
    assert isinstance(digest, str) and SHA256_RE.fullmatch(digest), f"{where} needs a sha256"
    target = root / rel
    assert target.is_file(), f"{where} file is missing"
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest, (
        f"{where} bytes do not match the recorded sha256"
    )
    return match.group(1)


def _assert_slice_row(row: dict, lineno: int, root: Path = ROOT) -> None:
    """Strict checks for a SLICE line written by the slice scoring wrapper.

    A scoring failure may leave null products, but then it must carry an error.
    """
    assert row.get("event") == "SLICE"
    where = f"line {lineno}: {row['trial_id']}: SLICE"
    slice_id = row.get("slice_id")
    assert isinstance(slice_id, str) and SLICE_ID_RE.fullmatch(slice_id), f"{where} bad slice_id"
    assert SHA40_RE.fullmatch(row.get("git_head") or ""), f"{where} git_head must be 40 hex"
    assert SHA256_RE.fullmatch(row.get("evaluator_sha256") or ""), (
        f"{where} evaluator_sha256 must be sha256 hex"
    )
    escaped = re.escape(slice_id)
    artifact_attempt = _assert_slice_product(
        row,
        lineno,
        "slice_artifact",
        "slice_artifact_sha256",
        re.compile(rf"^{escaped}\.attempt-(\d{{2,}})\.json$"),
        root,
    )
    replay_attempt = _assert_slice_product(
        row,
        lineno,
        "replay_ledger",
        "replay_ledger_sha256",
        re.compile(rf"^{escaped}\.replay-ledger\.attempt-(\d{{2,}})\.json$"),
        root,
    )
    error = row.get("error")
    if error is None:
        assert artifact_attempt is not None and replay_attempt is not None, (
            f"{where} without an error needs both the slice artifact and the replay ledger"
        )
    else:
        assert isinstance(error, str) and error.strip(), f"{where} error must be a non-empty string"
    if artifact_attempt is not None and replay_attempt is not None:
        assert artifact_attempt == replay_attempt, f"{where} artifact and replay attempts differ"


def _assert_terminal_artifact_location(row: dict, known_trials: set[str]) -> None:
    """A terminal result lives under its own trial folder.

    The one exception is a COMPLETED line whose parent_trial owns the combine file
    ``<parent>/slices/combine.attempt-NN.json``; that line must pin the file's sha256.
    """
    trial_id = row["trial_id"]
    artifact = row.get("result_artifact")
    assert isinstance(artifact, str), f"{trial_id}: result_artifact must be a string"
    assert _is_clean_rel(artifact), f"{trial_id}: result_artifact must be a clean repo-relative path"
    own_prefix = f"{EVIDENCE_PREFIX}{trial_id}/"
    if artifact.startswith(own_prefix):
        return
    parent = row.get("parent_trial")
    assert row["event"] == "COMPLETED" and parent is not None, (
        f"{trial_id}: terminal result artifact must live under {own_prefix}"
    )
    assert isinstance(parent, str) and TRIAL_RE.fullmatch(parent), f"{trial_id}: bad parent_trial"
    assert parent != trial_id and parent in known_trials, f"{trial_id}: parent_trial is not a known other trial"
    match = COMBINE_RESULT_RE.fullmatch(artifact)
    assert match and match.group(1) == parent, (
        f"{trial_id}: a result outside {own_prefix} must be {EVIDENCE_PREFIX}{parent}/slices/combine.attempt-NN.json"
    )
    assert SHA256_RE.fullmatch(row.get("result_artifact_sha256") or ""), (
        f"{trial_id}: a parent combine result must pin result_artifact_sha256"
    )


def _assert_slices_file_registered(rel: str, trial_id: str, rows: list[dict], root: Path) -> dict | None:
    """Admit one tracked file under <trial>/slices/ only when its bytes are pinned.

    Slice artifacts and replay ledgers are pinned by a SLICE line of that trial.
    ``combine.attempt-NN.json`` is pinned by a COMPLETED line of a trial whose
    parent_trial is this folder's trial; that COMPLETED row is returned.
    """
    assert _is_clean_rel(rel), f"{rel}: not a clean repo-relative path"
    name = PurePosixPath(rel).name
    digest = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    folder_slices = [r for r in rows if r["trial_id"] == trial_id and r["event"] == "SLICE"]

    if COMBINE_NAME_RE.fullmatch(name):
        known = {r["trial_id"] for r in rows}
        pins = [
            r
            for r in rows
            if r["event"] == "COMPLETED"
            and r["trial_id"] != trial_id
            and r.get("parent_trial") == trial_id
            and r.get("result_artifact") == rel
            and r.get("result_artifact_sha256") == digest
        ]
        assert len(pins) == 1, (
            f"{rel}: combine file needs exactly one COMPLETED line of a child trial pinning its sha256"
        )
        _assert_terminal_artifact_location(pins[0], known)
        combine = json.loads((root / rel).read_bytes())
        assert isinstance(combine, dict), f"{rel}: combine artifact must be an object"
        inputs = combine.get("inputs")
        assert isinstance(inputs, dict) and inputs, f"{rel}: combine artifact needs inputs"
        for slice_id, item in inputs.items():
            pinned = [
                r
                for r in folder_slices
                if r.get("slice_id") == slice_id
                and r.get("slice_artifact") == item.get("slice_artifact")
                and r.get("slice_artifact_sha256") == item.get("slice_artifact_sha256")
                and r.get("evaluator_sha256") == item.get("evaluator_sha256")
            ]
            assert len(pinned) == 1, f"{rel}: input {slice_id} is not pinned by exactly one SLICE line"
        return pins[0]

    pinned = [
        r
        for r in folder_slices
        if (r.get("slice_artifact") == rel and r.get("slice_artifact_sha256") == digest)
        or (r.get("replay_ledger") == rel and r.get("replay_ledger_sha256") == digest)
    ]
    assert len(pinned) == 1, f"{rel}: slices/ file is not pinned by exactly one SLICE line with its sha256"
    return None


def _assert_planned_before_artifact(rel: str, trial_id: str, first: dict[str, dict], artifact_commit: str) -> None:
    first_event = first[trial_id]["event"]
    assert first_event in {"PLANNED", "ADOPTED"}
    if first_event == "PLANNED":
        ledger_commit = _first_trial_commit(trial_id)
        prereg_commit = _first_add_commit(first[trial_id]["prereg_path"])
        assert ledger_commit != artifact_commit and _is_ancestor(ledger_commit, artifact_commit), (
            f"{rel}: PLANNED line must be committed strictly before the artifact"
        )
        assert prereg_commit != artifact_commit and _is_ancestor(prereg_commit, artifact_commit), (
            f"{rel}: prereg must be committed strictly before the artifact"
        )
        prereg_text = (ROOT / first[trial_id]["prereg_path"]).read_text(encoding="utf-8")
        assert trial_id in prereg_text, f"{trial_id}: prereg must cite its trial_id"


def test_trial_ledger_schema_and_family_counts() -> None:
    rows = _rows()
    first_by_trial: dict[str, dict] = {}
    family_first_counts: dict[str, int] = {}
    normalized_families: dict[str, str] = {}
    last_time: dict[str, datetime] = {}

    for lineno, row in enumerate(rows, 1):
        missing = REQUIRED - row.keys()
        assert not missing, f"line {lineno}: missing required fields {sorted(missing)}"

        trial_id = row["trial_id"]
        event = row["event"]
        family_id = row["family_id"]

        assert isinstance(trial_id, str) and TRIAL_RE.fullmatch(trial_id), f"line {lineno}: bad trial_id"
        assert event in EVENTS, f"line {lineno}: unknown event {event!r}"
        assert isinstance(row["recorded_by"], str) and row["recorded_by"].strip()
        assert isinstance(family_id, str) and FAMILY_RE.fullmatch(family_id), f"line {lineno}: bad family_id"
        assert isinstance(row["family_label"], str) and row["family_label"].strip()
        assert isinstance(row["population"], str) and row["population"].strip()
        assert isinstance(row["prior_exposed"], str) and row["prior_exposed"].strip()

        norm = _normalized_family(family_id)
        prior_raw = normalized_families.setdefault(norm, family_id)
        assert prior_raw == family_id, f"family_id collision: {prior_raw!r} vs {family_id!r}"

        prereg_path = row["prereg_path"]
        prereg_commit = row.get("prereg_commit")
        if event == "UNREGISTERED_ATTEMPT":
            assert prereg_path is None and prereg_commit is None
        else:
            assert isinstance(prereg_path, str) and prereg_path.startswith("docs/prereg")
        for key in OPTIONAL_SHA_FIELDS:
            value = row.get(key)
            assert value is None or (isinstance(value, str) and SHA40_RE.fullmatch(value)), (
                f"line {lineno}: {key} must be null or a 40-hex commit"
            )

        variant = row["variant_set"]
        assert isinstance(variant, dict)
        assert set(variant) == {"count", "manifest"}
        assert isinstance(variant["count"], int) and variant["count"] >= 1
        assert isinstance(variant["manifest"], str) and variant["manifest"].startswith("docs/")

        ts = _utc(row["recorded_at"])
        if trial_id in last_time:
            assert ts >= last_time[trial_id], f"{trial_id}: recorded_at moved backwards"
        last_time[trial_id] = ts

        if trial_id not in first_by_trial:
            assert event in FIRST_EVENTS, f"{trial_id}: first event cannot be {event}"
            expected = family_first_counts.get(family_id, 0)
            assert row.get("attempts_in_family_before") == expected, (
                f"{trial_id}: attempts_in_family_before must be {expected}"
            )
            pre = row.get("pre_ledger_attempts")
            assert (isinstance(pre, int) and pre >= 0) or pre == "UNKNOWN"
            first_by_trial[trial_id] = row
            family_first_counts[family_id] = expected + 1
        else:
            assert event not in {"PLANNED", "ADOPTED"}, f"{trial_id}: repeated first-only event {event}"
            assert "attempts_in_family_before" not in row
            assert "pre_ledger_attempts" not in row
            first = first_by_trial[trial_id]
            for key in sorted(FROZEN):
                assert row.get(key) == first.get(key), (
                    f"line {lineno}: {trial_id}: frozen field {key!r} differs from the first line"
                )

        if event == "SLICE":
            _assert_slice_row(row, lineno)

        if event == "GATE_REVIEW":
            assert SHA256_RE.fullmatch(row.get("gate_artifact_sha256") or ""), (
                f"line {lineno}: GATE_REVIEW requires the PASS artifact sha256"
            )
            assert SHA40_RE.fullmatch(row.get("pr_head_sha") or ""), (
                f"line {lineno}: GATE_REVIEW requires the reviewed pull request head SHA"
            )

        if event in TERMINAL_EVENTS:
            assert row.get("disposition") in DISPOSITIONS
            assert "result_artifact" in row
            if event in {"COMPLETED", "UNREGISTERED_ATTEMPT"}:
                assert isinstance(row.get("result_artifact"), str) and row["result_artifact"]
            if event == "ABORTED" and row.get("result_artifact") is None:
                assert isinstance(row.get("reason"), str) and row["reason"].strip()


def test_trial_ledger_is_append_only_against_origin_main_or_valid_bootstrap() -> None:
    rows = _rows()
    assert _git("rev-parse", "--verify", "origin/main", check=False).returncode == 0, (
        "origin/main is required for fail-closed ledger verification"
    )

    base_has_ledger = _git(
        "cat-file",
        "-e",
        f"origin/main:{LEDGER_REL}",
        check=False,
    ).returncode == 0

    if base_has_ledger:
        base = _git("show", f"origin/main:{LEDGER_REL}").stdout.encode()
        current = LEDGER.read_bytes()
        assert current.startswith(base), "existing ledger bytes may only be appended"
        return

    additions = _git(
        "log",
        "--diff-filter=A",
        "--format=%H",
        "--reverse",
        "--",
        LEDGER_REL,
    ).stdout.splitlines()
    assert additions, "bootstrap ledger must be a real file addition"
    assert all(row["event"] in {"ADOPTED", "PLANNED"} for row in _first_rows(rows).values()), (
        "bootstrap first lines may only be ADOPTED or PLANNED"
    )


def test_trial_variant_manifests_are_frozen_and_machine_checkable() -> None:
    first = _first_rows(_rows())

    for trial_id, row in first.items():
        manifest = row["variant_set"]["manifest"]
        count = row["variant_set"]["count"]
        first_commit = _first_trial_commit(trial_id)

        assert _git("cat-file", "-e", f"{first_commit}:{manifest}", check=False).returncode == 0, (
            f"{trial_id}: manifest did not exist at the first ledger line"
        )
        assert (ROOT / manifest).exists(), f"{trial_id}: manifest missing at HEAD"

        if count > 1:
            expected_path = f"{MANIFEST_PREFIX}{trial_id}.json"
            assert manifest == expected_path, f"{trial_id}: multi-variant trials require {expected_path}"

            frozen = json.loads(_git("show", f"{first_commit}:{manifest}").stdout)
            current = json.loads((ROOT / manifest).read_text(encoding="utf-8"))
            for payload, label in ((frozen, "frozen"), (current, "current")):
                assert payload.get("trial_id") == trial_id, f"{trial_id}: {label} manifest trial_id mismatch"
                variants = payload.get("variants")
                assert isinstance(variants, list) and len(variants) == count
                ids = [v.get("id") for v in variants if isinstance(v, dict)]
                assert len(ids) == count and all(isinstance(v, str) and v for v in ids)
                assert len(set(ids)) == count, f"{trial_id}: variant ids must be unique"

            assert frozen.get("results_sha256") is None
            frozen_core = dict(frozen)
            current_core = dict(current)
            frozen_core.pop("results_sha256", None)
            current_core.pop("results_sha256", None)
            assert current_core == frozen_core, f"{trial_id}: frozen variant inventory changed"
            result_hash = current.get("results_sha256")
            assert result_hash is None or (isinstance(result_hash, str) and SHA256_RE.fullmatch(result_hash))
        else:
            expected_json = f"{MANIFEST_PREFIX}{trial_id}.json"
            assert manifest in {row["prereg_path"], expected_json}, (
                f"{trial_id}: single-variant manifest must be its prereg or {expected_json}"
            )


def _assert_gate_outcome(row: dict, rel: str) -> None:
    assert row.get("gate_pass") in (True, False), f"{rel}: gate outcome must be PASS or FAIL"


def _registered_gate_row(
    rel: str,
    digest: str,
    trial_rows: list[dict],
    *,
    match_artifact_path: bool,
) -> dict:
    gate_rows = [
        row
        for row in trial_rows
        if row.get("event") == "GATE"
        and row.get("gate_artifact_sha256") == digest
        and (not match_artifact_path or row.get("gate_artifact") == rel)
    ]
    kind = "reproduction gate artifact" if match_artifact_path else "attempt artifact"
    assert len(gate_rows) == 1, f"{rel}: {kind} needs one GATE line"
    _assert_gate_outcome(gate_rows[0], rel)
    return gate_rows[0]


def _assert_replay_pin_matches_bytes(rel: str, row: dict, artifact: dict, root: Path) -> None:
    """A pinned replay ledger must match the gate artifact and the file bytes.

    GATE lines written before replay ledgers omit both fields. A null path and
    hash means the attempt failed before a ledger existed.
    """
    if "replay_ledger" not in row and "replay_ledger_sha256" not in row:
        return
    row_path = row.get("replay_ledger")
    row_hash = row.get("replay_ledger_sha256")
    assert artifact.get("replay_ledger") == row_path, f"{rel}: gate artifact replay path mismatch"
    assert artifact.get("replay_ledger_sha256") == row_hash, (
        f"{rel}: gate artifact replay hash mismatch"
    )
    if row_path is None and row_hash is None:
        return
    assert isinstance(row_path, str) and row_path, f"{rel}: replay ledger path missing"
    assert isinstance(row_hash, str) and SHA256_RE.fullmatch(row_hash), (
        f"{rel}: replay ledger hash missing"
    )
    digest = hashlib.sha256((root / row_path).read_bytes()).hexdigest()
    assert digest == row_hash, f"{rel}: replay ledger bytes do not match the recorded sha256"


def _assert_replay_ledger_registered(
    rel: str,
    digest: str,
    trial_rows: list[dict],
    root: Path,
) -> None:
    matches = [
        row
        for row in trial_rows
        if row.get("event") == "GATE"
        and row.get("replay_ledger") == rel
        and row.get("replay_ledger_sha256") == digest
    ]
    assert len(matches) == 1, f"{rel}: replay ledger needs one GATE line with its sha256"
    row = matches[0]
    _assert_gate_outcome(row, rel)
    artifact_rel = row.get("gate_artifact")
    assert isinstance(artifact_rel, str) and artifact_rel, f"{rel}: GATE line missing gate artifact"
    artifact = json.loads((root / artifact_rel).read_bytes())
    assert isinstance(artifact, dict), f"{rel}: gate artifact must be an object"
    assert artifact.get("replay_ledger") == rel, f"{rel}: gate artifact replay path mismatch"
    assert artifact.get("replay_ledger_sha256") == digest, (
        f"{rel}: gate artifact replay hash mismatch"
    )


def _assert_run_provenance(
    rel: str,
    name: str,
    trial_id: str,
    trial_rows: list[dict],
    root: Path,
) -> None:
    assert any(row.get("event") == "GATE" for row in trial_rows), (
        f"{rel}: supporting run provenance requires a GATE line"
    )
    sums_rel = f"{EVIDENCE_PREFIX}{trial_id}/run/sha256sums.txt"
    sums = (root / sums_rel).read_text(encoding="utf-8")
    if name == "sha256sums.txt":
        for sibling in ("reproduction-gate.json", "reproduction-replay-ledger.json"):
            sibling_rel = f"{EVIDENCE_PREFIX}{trial_id}/{sibling}"
            digest = hashlib.sha256((root / sibling_rel).read_bytes()).hexdigest()
            assert digest in sums, f"{rel}: {sibling} sha256 is not listed in sha256sums.txt"
        return
    digest = hashlib.sha256((root / rel).read_bytes()).hexdigest()
    assert digest in sums, f"{rel}: sha256 is not listed in sha256sums.txt"


def test_canonical_research_evidence_is_registered_before_counting() -> None:
    rows = _rows()
    first = _first_rows(rows)
    by_trial: dict[str, list[dict]] = {}
    for row in rows:
        by_trial.setdefault(row["trial_id"], []).append(row)

    tracked = _git("ls-files", "docs/research-evidence").stdout.splitlines()
    for rel in tracked:
        path = PurePosixPath(rel)
        assert len(path.parts) >= 4 and "/".join(path.parts[:2]) == "docs/research-evidence"
        trial_id = path.parts[2]
        assert trial_id in first, f"{rel}: trial_id directory not present in ledger"

        if path.name == "databento-download-manifest.json":
            prereg = ROOT / first[trial_id]["prereg_path"]
            digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            assert digest in prereg.read_text(encoding="utf-8"), (
                f"{rel}: download manifest sha256 must be cited by the prereg"
            )
            continue

        is_first_gate = path.name == "reproduction-gate.json"
        is_later_gate = path.name.startswith("reproduction-gate.attempt-") and path.suffix == ".json"
        if is_first_gate or is_later_gate:
            digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            row = _registered_gate_row(
                rel,
                digest,
                by_trial[trial_id],
                match_artifact_path=is_first_gate,
            )
            artifact = json.loads((ROOT / rel).read_bytes())
            assert isinstance(artifact, dict), f"{rel}: gate artifact must be an object"
            _assert_replay_pin_matches_bytes(rel, row, artifact, ROOT)
            continue

        if REPLAY_LEDGER_RE.fullmatch(path.name):
            digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            _assert_replay_ledger_registered(rel, digest, by_trial[trial_id], ROOT)
            continue

        if len(path.parts) == 5 and path.parts[3] == "run" and path.name in RUN_PROVENANCE:
            _assert_run_provenance(rel, path.name, trial_id, by_trial[trial_id], ROOT)
            continue

        if path.name in {
            "BAR_EVIDENCE.md",
            "BAR_EVIDENCE.json",
            "MAR06_BAR_EVIDENCE.md",
            "MAR06_BAR_EVIDENCE.json",
        }:
            digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            cited = (ROOT / "docs/agent-work-state.md").read_text(encoding="utf-8")
            assert digest in cited, f"{rel}: bar evidence sha256 is not cited in agent-work-state"
            continue

        if len(path.parts) == 5 and path.parts[3] == SLICES_DIRNAME:
            child = _assert_slices_file_registered(rel, trial_id, rows, ROOT)
            if child is not None:
                _assert_planned_before_artifact(
                    rel, child["trial_id"], first, _first_add_commit(rel)
                )
            continue

        matching = [
            row
            for row in by_trial[trial_id]
            if row["event"] in {"COMPLETED", "UNREGISTERED_ATTEMPT"}
            and row.get("result_artifact") == rel
        ]
        if not matching:
            terminals = [
                row
                for row in by_trial[trial_id]
                if row["event"] == "COMPLETED" and isinstance(row.get("result_artifact"), str)
            ]
            assert len(terminals) == 1, f"{rel}: companion file needs exactly one COMPLETED result"
            pinned = set(re.findall(r"[0-9a-f]{64}", terminals[0].get("notes") or ""))
            digest = hashlib.sha256((ROOT / rel).read_bytes()).hexdigest()
            assert digest in pinned, f"{rel}: preserved companion bytes are not pinned on the COMPLETED line"
            text = (ROOT / rel).read_text(encoding="utf-8")
            if rel.endswith(".json"):
                payload = json.loads(text)
                embedded = payload.get("trial_id") if isinstance(payload, dict) else None
                if embedded is not None:
                    assert embedded == trial_id, f"{rel}: companion trial_id mismatch"
            continue

        assert len(matching) == 1, f"{rel}: expected exactly one terminal ledger event"
        assert _read_trial_id_from_artifact(ROOT / rel) == trial_id, f"{rel}: embedded trial_id mismatch"
        terminal = matching[0]

        # Provenance is derived from history, not from SHAs stored in the row: after a
        # squash merge the branch commits that added the artifact / prereg no longer
        # exist on main, so a stored SHA could never match here.
        artifact_commit = _first_add_commit(rel)

        if terminal["event"] == "COMPLETED":
            _assert_planned_before_artifact(rel, trial_id, first, artifact_commit)

    for row in rows:
        if row["event"] not in {"COMPLETED", "UNREGISTERED_ATTEMPT"}:
            continue
        _assert_terminal_artifact_location(row, set(first))


def test_new_preregs_after_ledger_start_are_linked_to_planned_trials() -> None:
    rows = _rows()
    first = _first_rows(rows)
    start_commit = _first_add_commit(LEDGER_REL)

    for prereg in sorted((ROOT / "docs").glob("prereg-*.md")):
        rel = prereg.relative_to(ROOT).as_posix()
        first_commit = _first_add_commit(rel)
        if first_commit == start_commit or not _is_ancestor(start_commit, first_commit):
            continue

        linked = [
            trial_id
            for trial_id, row in first.items()
            if row["event"] == "PLANNED" and row["prereg_path"] == rel
        ]
        assert linked, f"{rel}: new prereg has no PLANNED ledger entry"
        text = prereg.read_text(encoding="utf-8")
        assert any(trial_id in text for trial_id in linked), f"{rel}: prereg does not cite its trial_id"


def _write_gate_pair(
    root: Path,
    trial: str,
    *,
    attempt: str | None,
    gate_pass: bool,
) -> tuple[str, str, str, dict]:
    suffix = "" if attempt is None else f".attempt-{attempt}"
    gate_rel = f"docs/research-evidence/{trial}/reproduction-gate{suffix}.json"
    replay_rel = f"docs/research-evidence/{trial}/reproduction-replay-ledger{suffix}.json"
    label = attempt or "01"
    replay_bytes = (json.dumps({"ledger": {"attempt": label}}) + "\n").encode()
    digest = hashlib.sha256(replay_bytes).hexdigest()
    artifact = {
        "pass": gate_pass,
        "replay_ledger": replay_rel,
        "replay_ledger_sha256": digest,
    }
    gate_bytes = (json.dumps(artifact, separators=(",", ":")) + "\n").encode()
    (root / replay_rel).parent.mkdir(parents=True, exist_ok=True)
    (root / replay_rel).write_bytes(replay_bytes)
    (root / gate_rel).write_bytes(gate_bytes)
    row = {
        "event": "GATE",
        "gate_pass": gate_pass,
        "gate_artifact": gate_rel,
        "gate_artifact_sha256": hashlib.sha256(gate_bytes).hexdigest(),
        "replay_ledger": replay_rel,
        "replay_ledger_sha256": digest,
    }
    return gate_rel, replay_rel, digest, row


def test_pass_gate_with_matching_replay_ledger_is_accepted(tmp_path: Path) -> None:
    trial = "T-2026-11-11-example-pass-01"
    gate_rel, replay_rel, digest, row = _write_gate_pair(
        tmp_path, trial, attempt=None, gate_pass=True
    )
    registered = _registered_gate_row(
        gate_rel,
        row["gate_artifact_sha256"],
        [row],
        match_artifact_path=True,
    )
    assert registered["gate_pass"] is True
    artifact = json.loads((tmp_path / gate_rel).read_bytes())
    _assert_replay_pin_matches_bytes(gate_rel, registered, artifact, tmp_path)
    _assert_replay_ledger_registered(replay_rel, digest, [row], tmp_path)

    later_gate, later_replay, later_digest, later_row = _write_gate_pair(
        tmp_path, trial, attempt="02", gate_pass=False
    )
    later = _registered_gate_row(
        later_gate,
        later_row["gate_artifact_sha256"],
        [later_row],
        match_artifact_path=False,
    )
    assert later["gate_pass"] is False
    _assert_replay_ledger_registered(later_replay, later_digest, [later_row], tmp_path)

    undecided = dict(row)
    undecided["gate_pass"] = "PASS"
    with pytest.raises(AssertionError, match="PASS or FAIL"):
        _registered_gate_row(
            gate_rel,
            row["gate_artifact_sha256"],
            [undecided],
            match_artifact_path=True,
        )


def test_replay_ledger_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    trial = "T-2026-11-11-example-mismatch-01"
    gate_rel, replay_rel, digest, row = _write_gate_pair(
        tmp_path, trial, attempt=None, gate_pass=True
    )
    wrong = "0" * 64
    assert wrong != digest

    with pytest.raises(AssertionError, match="one GATE line"):
        _assert_replay_ledger_registered(replay_rel, wrong, [row], tmp_path)

    with pytest.raises(AssertionError, match="one GATE line"):
        _assert_replay_ledger_registered(replay_rel, digest, [], tmp_path)

    artifact = json.loads((tmp_path / gate_rel).read_bytes())
    artifact["replay_ledger_sha256"] = wrong
    (tmp_path / gate_rel).write_bytes((json.dumps(artifact) + "\n").encode())
    with pytest.raises(AssertionError, match="gate artifact replay hash mismatch"):
        _assert_replay_ledger_registered(replay_rel, digest, [row], tmp_path)

    mismatched = dict(row)
    mismatched["replay_ledger_sha256"] = wrong
    artifact["replay_ledger_sha256"] = wrong
    with pytest.raises(AssertionError, match="do not match"):
        _assert_replay_pin_matches_bytes(gate_rel, mismatched, artifact, tmp_path)


# --- slice-event governance (SLICE lines, slices/ evidence, combine results) ---

_PARENT = "T-2026-11-10-example-structural-01"
_CHILD = "T-2026-11-11-example-slice-success-01"
_EVALUATOR = "e" * 64
_RUN_COMMIT = "a" * 40


def _slice_fixture(root: Path) -> tuple[list[dict], dict[str, str]]:
    """Write a two-slice parent trial plus its child COMPLETED line under ``root``."""
    folder = f"{EVIDENCE_PREFIX}{_PARENT}/{SLICES_DIRNAME}"
    (root / folder).mkdir(parents=True)
    rows: list[dict] = [
        {"trial_id": _PARENT, "event": "PLANNED"},
        {"trial_id": _CHILD, "event": "PLANNED", "parent_trial": _PARENT},
    ]
    paths: dict[str, str] = {}
    inputs: dict[str, dict] = {}
    for slice_id in ("nq-example", "mnq-example"):
        artifact = f"{folder}/{slice_id}.attempt-01.json"
        replay = f"{folder}/{slice_id}.replay-ledger.attempt-01.json"
        (root / artifact).write_bytes(f'{{"slice":"{slice_id}"}}\n'.encode())
        (root / replay).write_bytes(f'{{"ledger":"{slice_id}"}}\n'.encode())
        a_sha = hashlib.sha256((root / artifact).read_bytes()).hexdigest()
        r_sha = hashlib.sha256((root / replay).read_bytes()).hexdigest()
        rows.append(
            {
                "trial_id": _PARENT,
                "event": "SLICE",
                "slice_id": slice_id,
                "slice_artifact": artifact,
                "slice_artifact_sha256": a_sha,
                "replay_ledger": replay,
                "replay_ledger_sha256": r_sha,
                "git_head": _RUN_COMMIT,
                "evaluator_sha256": _EVALUATOR,
                "error": None,
            }
        )
        inputs[slice_id] = {
            "slice_artifact": artifact,
            "slice_artifact_sha256": a_sha,
            "evaluator_sha256": _EVALUATOR,
        }
        paths[f"{slice_id}.artifact"] = artifact
        paths[f"{slice_id}.replay"] = replay
    combine = f"{folder}/combine.attempt-01.json"
    (root / combine).write_bytes((json.dumps({"overall": "FAIL", "inputs": inputs}) + "\n").encode())
    paths["combine"] = combine
    rows.append(
        {
            "trial_id": _CHILD,
            "event": "COMPLETED",
            "parent_trial": _PARENT,
            "disposition": "OVERFIT",
            "result_artifact": combine,
            "result_artifact_sha256": hashlib.sha256((root / combine).read_bytes()).hexdigest(),
        }
    )
    return rows, paths


def _check_all(rows: list[dict], paths: dict[str, str], root: Path) -> None:
    known = {r["trial_id"] for r in rows}
    for lineno, row in enumerate(rows, 1):
        if row["event"] == "SLICE":
            _assert_slice_row(row, lineno, root)
        if row["event"] == "COMPLETED":
            _assert_terminal_artifact_location(row, known)
    for rel in paths.values():
        _assert_slices_file_registered(rel, _PARENT, rows, root)


def _slice_row(rows: list[dict], slice_id: str = "nq-example") -> dict:
    return next(r for r in rows if r.get("slice_id") == slice_id)


def test_slice_event_is_a_known_event() -> None:
    assert "SLICE" in EVENTS
    assert "SLICE" not in FIRST_EVENTS and "SLICE" not in TERMINAL_EVENTS


def test_slice_fixture_passes_and_combine_is_admitted_only_via_child_completed(tmp_path: Path) -> None:
    rows, paths = _slice_fixture(tmp_path)
    _check_all(rows, paths, tmp_path)
    child = _assert_slices_file_registered(paths["combine"], _PARENT, rows, tmp_path)
    assert child is not None and child["trial_id"] == _CHILD
    assert _assert_slices_file_registered(paths["nq-example.artifact"], _PARENT, rows, tmp_path) is None


def test_unpinned_or_unknown_file_in_slices_is_rejected(tmp_path: Path) -> None:
    rows, paths = _slice_fixture(tmp_path)
    folder = f"{EVIDENCE_PREFIX}{_PARENT}/{SLICES_DIRNAME}"
    stray = f"{folder}/nq-example.attempt-02.json"
    (tmp_path / stray).write_bytes(b"{}\n")
    with pytest.raises(AssertionError, match="not pinned by exactly one SLICE line"):
        _assert_slices_file_registered(stray, _PARENT, rows, tmp_path)
    notes = f"{folder}/notes.md"
    (tmp_path / notes).write_bytes(b"hello\n")
    with pytest.raises(AssertionError, match="not pinned by exactly one SLICE line"):
        _assert_slices_file_registered(notes, _PARENT, rows, tmp_path)
    # A stray combine file with no COMPLETED pin is rejected too.
    stray_combine = f"{folder}/combine.attempt-02.json"
    (tmp_path / stray_combine).write_bytes(b"{}\n")
    with pytest.raises(AssertionError, match="needs exactly one COMPLETED"):
        _assert_slices_file_registered(stray_combine, _PARENT, rows, tmp_path)
    # A pin from the wrong trial's SLICE line does not admit the file.
    wrong_trial = [dict(r, trial_id=_CHILD) if r["event"] == "SLICE" else r for r in rows]
    with pytest.raises(AssertionError, match="not pinned by exactly one SLICE line"):
        _assert_slices_file_registered(paths["nq-example.artifact"], _PARENT, wrong_trial, tmp_path)


def test_sha256_mismatch_is_rejected(tmp_path: Path) -> None:
    rows, paths = _slice_fixture(tmp_path)
    (tmp_path / paths["nq-example.artifact"]).write_bytes(b'{"tampered":true}\n')
    with pytest.raises(AssertionError, match="bytes do not match the recorded sha256"):
        _assert_slice_row(_slice_row(rows), 1, tmp_path)
    with pytest.raises(AssertionError, match="not pinned by exactly one SLICE line"):
        _assert_slices_file_registered(paths["nq-example.artifact"], _PARENT, rows, tmp_path)

    rows, paths = _slice_fixture(tmp_path / "combine")
    root = tmp_path / "combine"
    (root / paths["combine"]).write_bytes(b'{"overall":"PASS","inputs":{}}\n')
    with pytest.raises(AssertionError, match="needs exactly one COMPLETED"):
        _assert_slices_file_registered(paths["combine"], _PARENT, rows, root)

    rows, paths = _slice_fixture(tmp_path / "replay")
    root = tmp_path / "replay"
    (root / paths["mnq-example.replay"]).write_bytes(b"{}\n")
    with pytest.raises(AssertionError, match="bytes do not match the recorded sha256"):
        _assert_slice_row(_slice_row(rows, "mnq-example"), 2, root)

    rows, paths = _slice_fixture(tmp_path / "badhex")
    root = tmp_path / "badhex"
    _slice_row(rows)["slice_artifact_sha256"] = "XYZ"
    with pytest.raises(AssertionError, match="needs a sha256"):
        _assert_slice_row(_slice_row(rows), 1, root)


def test_slice_line_must_point_inside_its_own_slices_folder(tmp_path: Path) -> None:
    rows, paths = _slice_fixture(tmp_path)
    row = _slice_row(rows)
    good = row["slice_artifact"]
    outside = (
        f"{EVIDENCE_PREFIX}{_PARENT}/nq-example.attempt-01.json",
        f"{EVIDENCE_PREFIX}{_CHILD}/{SLICES_DIRNAME}/nq-example.attempt-01.json",
        f"{EVIDENCE_PREFIX}{_PARENT}/{SLICES_DIRNAME}/sub/nq-example.attempt-01.json",
        f"{EVIDENCE_PREFIX}{_PARENT}/{SLICES_DIRNAME}/mnq-example.attempt-01.json",
        "research/afs0523_322_slice_evaluator.py",
        "/etc/passwd",
    )
    for bad in outside:
        row["slice_artifact"] = bad
        with pytest.raises(AssertionError):
            _assert_slice_row(row, 1, tmp_path)
    row["slice_artifact"] = good
    _assert_slice_row(row, 1, tmp_path)
    row["replay_ledger"] = f"{EVIDENCE_PREFIX}{_CHILD}/{SLICES_DIRNAME}/nq-example.replay-ledger.attempt-01.json"
    with pytest.raises(AssertionError, match="must live under"):
        _assert_slice_row(row, 1, tmp_path)


def test_slice_line_field_strictness(tmp_path: Path) -> None:
    rows, _ = _slice_fixture(tmp_path)
    row = _slice_row(rows)
    for key, bad, match in (
        ("git_head", "abc123", "git_head must be 40 hex"),
        ("git_head", None, "git_head must be 40 hex"),
        ("git_head", "A" * 40, "git_head must be 40 hex"),
        ("evaluator_sha256", "abc", "evaluator_sha256"),
        ("evaluator_sha256", None, "evaluator_sha256"),
        ("slice_id", "Bad Id", "bad slice_id"),
        ("error", "", "non-empty string"),
    ):
        mutated = dict(row, **{key: bad})
        with pytest.raises(AssertionError, match=match):
            _assert_slice_row(mutated, 1, tmp_path)
    # Null products without an error are never accepted; with an error they are (crash path).
    empty = dict(
        row,
        slice_artifact=None,
        slice_artifact_sha256=None,
        replay_ledger=None,
        replay_ledger_sha256=None,
    )
    with pytest.raises(AssertionError, match="without an error"):
        _assert_slice_row(empty, 1, tmp_path)
    _assert_slice_row(dict(empty, error="RuntimeError: boom"), 1, tmp_path)
    # A path without its sha256 (or the reverse) is rejected.
    with pytest.raises(AssertionError):
        _assert_slice_row(dict(row, replay_ledger_sha256=None), 1, tmp_path)
    # Mismatched attempts between artifact and replay ledger are rejected.
    folder = f"{EVIDENCE_PREFIX}{_PARENT}/{SLICES_DIRNAME}"
    other = f"{folder}/nq-example.replay-ledger.attempt-02.json"
    (tmp_path / other).write_bytes(b"{}\n")
    skewed = dict(
        row,
        replay_ledger=other,
        replay_ledger_sha256=hashlib.sha256(b"{}\n").hexdigest(),
    )
    with pytest.raises(AssertionError, match="attempts differ"):
        _assert_slice_row(skewed, 1, tmp_path)


def test_combine_result_requires_parent_trial_and_exact_pattern(tmp_path: Path) -> None:
    rows, paths = _slice_fixture(tmp_path)
    known = {r["trial_id"] for r in rows}
    done = next(r for r in rows if r["event"] == "COMPLETED")

    no_parent = {k: v for k, v in done.items() if k != "parent_trial"}
    with pytest.raises(AssertionError, match="must live under"):
        _assert_terminal_artifact_location(no_parent, known)
    with pytest.raises(AssertionError, match="must live under"):
        _assert_terminal_artifact_location(dict(done, parent_trial=None), known)
    # The combine file is not admitted when the child line has no parent_trial.
    unlinked = [r if r is not done else no_parent for r in rows]
    with pytest.raises(AssertionError, match="needs exactly one COMPLETED"):
        _assert_slices_file_registered(paths["combine"], _PARENT, unlinked, tmp_path)
    # parent_trial may only widen COMPLETED, never other terminal events.
    with pytest.raises(AssertionError, match="must live under"):
        _assert_terminal_artifact_location(dict(done, event="ABORTED"), known)
    # Parent must be another known trial and must be the folder owner.
    with pytest.raises(AssertionError, match="not a known other trial"):
        _assert_terminal_artifact_location(dict(done, parent_trial=_CHILD), known)
    with pytest.raises(AssertionError, match="not a known other trial"):
        _assert_terminal_artifact_location(dict(done, parent_trial="T-2026-01-01-unknown-01"), known)
    other_parent = "T-2026-11-09-example-other-01"
    with pytest.raises(AssertionError, match="slices/combine"):
        _assert_terminal_artifact_location(dict(done, parent_trial=other_parent), known | {other_parent})
    # Only the combine file qualifies, not slice artifacts or arbitrary files.
    for bad in (
        paths["nq-example.artifact"],
        f"{EVIDENCE_PREFIX}{_PARENT}/result.json",
        f"{EVIDENCE_PREFIX}{_PARENT}/{SLICES_DIRNAME}/combine.attempt-1.json",
        f"{EVIDENCE_PREFIX}{_PARENT}/{SLICES_DIRNAME}/sub/combine.attempt-01.json",
        f"{EVIDENCE_PREFIX}{_PARENT}/{SLICES_DIRNAME}/combine.attempt-01.json.bak",
    ):
        with pytest.raises(AssertionError, match="slices/combine"):
            _assert_terminal_artifact_location(dict(done, result_artifact=bad), known)
    # The pointing line must pin the sha256.
    with pytest.raises(AssertionError, match="pin result_artifact_sha256"):
        _assert_terminal_artifact_location(dict(done, result_artifact_sha256=None), known)
    # A pin with the wrong digest does not admit the file.
    wrong = [r if r is not done else dict(done, result_artifact_sha256="0" * 64) for r in rows]
    with pytest.raises(AssertionError, match="needs exactly one COMPLETED"):
        _assert_slices_file_registered(paths["combine"], _PARENT, wrong, tmp_path)
    # Combine inputs must be pinned by SLICE lines of the folder trial.
    unpinned = [r for r in rows if r.get("slice_id") != "mnq-example"]
    with pytest.raises(AssertionError, match="not pinned by exactly one SLICE line"):
        _assert_slices_file_registered(paths["combine"], _PARENT, unpinned, tmp_path)
    # Existing rule is unchanged: a trial with no parent keeps to its own folder.
    own = {"trial_id": _PARENT, "event": "COMPLETED", "result_artifact": f"{EVIDENCE_PREFIX}{_PARENT}/result.json"}
    _assert_terminal_artifact_location(own, known)
    with pytest.raises(AssertionError, match="must live under"):
        _assert_terminal_artifact_location(dict(own, result_artifact=f"{EVIDENCE_PREFIX}{_CHILD}/result.json"), known)


def test_path_traversal_is_rejected_everywhere(tmp_path: Path) -> None:
    rows, paths = _slice_fixture(tmp_path)
    known = {r["trial_id"] for r in rows}
    done = next(r for r in rows if r["event"] == "COMPLETED")
    folder = f"{EVIDENCE_PREFIX}{_PARENT}/{SLICES_DIRNAME}"
    traversals = (
        f"{EVIDENCE_PREFIX}{_PARENT}/{SLICES_DIRNAME}/../{SLICES_DIRNAME}/combine.attempt-01.json",
        f"{EVIDENCE_PREFIX}{_CHILD}/../{_PARENT}/{SLICES_DIRNAME}/combine.attempt-01.json",
        f"{folder}/../../../research/x.py",
        f"{folder}//combine.attempt-01.json",
        f"{folder}/./combine.attempt-01.json",
        f"{folder}\\combine.attempt-01.json",
    )
    for bad in traversals:
        with pytest.raises(AssertionError):
            _assert_terminal_artifact_location(dict(done, result_artifact=bad), known)
    own = f"{EVIDENCE_PREFIX}{_PARENT}/../{_CHILD}/result.json"
    with pytest.raises(AssertionError, match="clean repo-relative"):
        _assert_terminal_artifact_location(
            {"trial_id": _PARENT, "event": "COMPLETED", "result_artifact": own}, known
        )
    row = _slice_row(rows)
    for bad in (
        f"{folder}/../{SLICES_DIRNAME}/nq-example.attempt-01.json",
        f"{folder}/../../{_PARENT}/{SLICES_DIRNAME}/nq-example.attempt-01.json",
    ):
        with pytest.raises(AssertionError, match="clean repo-relative"):
            _assert_slice_row(dict(row, slice_artifact=bad), 1, tmp_path)
    with pytest.raises(AssertionError, match="clean repo-relative"):
        _assert_slices_file_registered(f"{folder}/../{SLICES_DIRNAME}/combine.attempt-01.json", _PARENT, rows, tmp_path)
