from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from options_manager.quotes import QuoteRetentionInput, quote_record_json, retain_quote, retention_rule_from_mapping
from scripts.options_quote_dataset_manifest import DEFAULT_RULE, materialize_manifest


def _record(contract_id: str) -> bytes:
    raw = json.loads(DEFAULT_RULE.read_text())
    rule = retention_rule_from_mapping(raw)
    rule_sha = hashlib.sha256(DEFAULT_RULE.read_bytes()).hexdigest()
    record = retain_quote(
        QuoteRetentionInput(
            contract_id=contract_id,
            underlying="SPY",
            expiration="2026-11-20",
            strike=550.0,
            right="CALL",
            bid=4.8,
            ask=5.0,
            quote_ts="2026-09-18T14:00:00+00:00",
            decision_ts="2026-09-18T14:01:00+00:00",
            source="fixture:option_chain_snapshot",
            volume=500,
            open_interest=1500,
            delta=0.5,
            iv=0.25,
        ),
        rule=rule,
        rule_sha256=rule_sha,
    )
    return quote_record_json(record).encode()


def test_materializes_canonical_manifest_and_hashes_exact_dataset_bytes(tmp_path: Path):
    root = tmp_path / "dataset"
    quotes = root / "quotes"
    quotes.mkdir(parents=True)
    a = quotes / "a.jsonl"
    b = quotes / "b.jsonl"
    a.write_bytes(_record("A"))
    b.write_bytes(_record("B") + _record("B2"))

    manifest, payload = materialize_manifest(
        dataset_root=root,
        quote_files=[Path("quotes/b.jsonl"), Path("quotes/a.jsonl")],
        output_path=Path("option_quotes_manifest.json"),
    )

    written = (root / "option_quotes_manifest.json").read_bytes()
    assert written == payload
    assert [entry["path"] for entry in manifest["files"]] == ["quotes/a.jsonl", "quotes/b.jsonl"]
    assert [entry["row_count"] for entry in manifest["files"]] == [1, 2]
    assert manifest["files"][0]["sha256"] == hashlib.sha256(a.read_bytes()).hexdigest()
    assert manifest["files"][1]["sha256"] == hashlib.sha256(b.read_bytes()).hexdigest()
    assert hashlib.sha256(written).hexdigest() == hashlib.sha256(payload).hexdigest()


def test_input_order_does_not_change_manifest_bytes(tmp_path: Path):
    root = tmp_path / "dataset"
    root.mkdir()
    a = root / "a.jsonl"
    b = root / "b.jsonl"
    a.write_bytes(_record("A"))
    b.write_bytes(_record("B"))

    _, first = materialize_manifest(dataset_root=root, quote_files=[a, b], output_path=Path("one.json"))
    _, second = materialize_manifest(dataset_root=root, quote_files=[b, a], output_path=Path("two.json"))
    assert first == second


def test_rejects_file_outside_dataset_root(tmp_path: Path):
    root = tmp_path / "dataset"
    root.mkdir()
    outside = tmp_path / "outside.jsonl"
    outside.write_bytes(_record("A"))
    with pytest.raises(ValueError, match="outside dataset root"):
        materialize_manifest(dataset_root=root, quote_files=[outside], output_path=Path("manifest.json"))


def test_rejects_symlinked_dataset_file(tmp_path: Path):
    root = tmp_path / "dataset"
    root.mkdir()
    target = root / "real.jsonl"
    target.write_bytes(_record("A"))
    link = root / "link.jsonl"
    link.symlink_to(target)
    with pytest.raises(ValueError, match="must not be a symlink"):
        materialize_manifest(dataset_root=root, quote_files=[link], output_path=Path("manifest.json"))


def test_rejects_non_jsonl_or_empty_input_set(tmp_path: Path):
    root = tmp_path / "dataset"
    root.mkdir()
    bad = root / "quotes.json"
    bad.write_bytes(_record("A"))
    with pytest.raises(ValueError, match="must be .jsonl"):
        materialize_manifest(dataset_root=root, quote_files=[bad], output_path=Path("manifest.json"))
    with pytest.raises(ValueError, match="at least one quote dataset file"):
        materialize_manifest(dataset_root=root, quote_files=[], output_path=Path("manifest.json"))


def test_rejects_manifest_output_outside_dataset_root(tmp_path: Path):
    root = tmp_path / "dataset"
    root.mkdir()
    quote = root / "quotes.jsonl"
    quote.write_bytes(_record("A"))
    with pytest.raises(ValueError, match="must stay inside dataset root"):
        materialize_manifest(
            dataset_root=root,
            quote_files=[quote],
            output_path=Path("../escaped.json"),
        )
    assert not (tmp_path / "escaped.json").exists()


def test_rejects_manifest_output_that_overwrites_quote_input(tmp_path: Path):
    root = tmp_path / "dataset"
    root.mkdir()
    quote = root / "quotes.jsonl"
    original = _record("A")
    quote.write_bytes(original)
    with pytest.raises(ValueError, match="must not overwrite quote dataset input"):
        materialize_manifest(
            dataset_root=root,
            quote_files=[quote],
            output_path=Path("quotes.jsonl"),
        )
    assert quote.read_bytes() == original


def test_manifest_cli_is_directly_invokable_from_repo_root():
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/options_quote_dataset_manifest.py", "--help"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Materialize a canonical frozen option-quote dataset manifest" in result.stdout


def test_generator_input_still_protects_quote_file_from_manifest_overwrite(tmp_path: Path):
    root = tmp_path / "dataset"
    root.mkdir()
    quote = root / "quotes.jsonl"
    original = _record("A")
    quote.write_bytes(original)
    with pytest.raises(ValueError, match="must not overwrite quote dataset input"):
        materialize_manifest(
            dataset_root=root,
            quote_files=(item for item in [quote]),
            output_path=Path("quotes.jsonl"),
        )
    assert quote.read_bytes() == original
