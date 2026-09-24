"""Fail-closed governance checks for the append-only research trial ledger.

Repository governance only. These tests do not import runtime, broker, risk,
collector, strategy, or execution modules.
"""
from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

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
        assert _read_trial_id_from_artifact(ROOT / rel) == trial_id, f"{rel}: embedded trial_id mismatch"

        matching = [
            row
            for row in by_trial[trial_id]
            if row["event"] in {"COMPLETED", "UNREGISTERED_ATTEMPT"}
            and row.get("result_artifact") == rel
        ]
        assert len(matching) == 1, f"{rel}: expected exactly one terminal ledger event"
        terminal = matching[0]

        # Provenance is derived from history, not from SHAs stored in the row: after a
        # squash merge the branch commits that added the artifact / prereg no longer
        # exist on main, so a stored SHA could never match here.
        artifact_commit = _first_add_commit(rel)

        first_event = first[trial_id]["event"]
        if terminal["event"] == "COMPLETED":
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

    for row in rows:
        if row["event"] not in {"COMPLETED", "UNREGISTERED_ATTEMPT"}:
            continue
        artifact = row.get("result_artifact")
        assert isinstance(artifact, str)
        expected_prefix = f"{EVIDENCE_PREFIX}{row['trial_id']}/"
        assert artifact.startswith(expected_prefix), (
            f"{row['trial_id']}: terminal result artifact must live under {expected_prefix}"
        )


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
