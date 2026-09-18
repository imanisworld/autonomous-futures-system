#!/usr/bin/env python3
"""Materialize a canonical frozen option-quote dataset manifest.

Offline evidence utility only. It does not fetch market data, read credentials,
call a broker, or activate any runtime path. Every input file must already be a
canonical retained-quote JSONL dataset under the declared dataset root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from options_manager.quotes import (
    build_quote_manifest,
    quote_manifest_json,
    retention_rule_from_mapping,
    verify_quote_manifest_files,
)

DEFAULT_RULE = Path("options_manager/quotes/quote_retention_rule_v2.json")


def _relative_dataset_path(root: Path, candidate: Path) -> str:
    root_resolved = root.resolve(strict=True)
    if candidate.is_symlink():
        raise ValueError(f"quote dataset file must not be a symlink: {candidate}")
    candidate_resolved = candidate.resolve(strict=True)
    if not candidate_resolved.is_file():
        raise ValueError(f"quote dataset path is not a file: {candidate}")
    try:
        relative = candidate_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"quote dataset file is outside dataset root: {candidate}") from exc
    if relative.suffix.lower() != ".jsonl":
        raise ValueError(f"quote dataset file must be .jsonl: {candidate}")
    return relative.as_posix()


def materialize_manifest(
    *,
    dataset_root: Path,
    quote_files: Iterable[Path],
    rule_path: Path = DEFAULT_RULE,
    output_path: Path,
) -> tuple[dict[str, object], bytes]:
    """Build, verify, and atomically write the canonical manifest bytes."""
    root = dataset_root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"dataset root is not a directory: {dataset_root}")

    rule_bytes = rule_path.read_bytes()
    rule_raw = json.loads(rule_bytes)
    if not isinstance(rule_raw, dict):
        raise ValueError("quote retention rule must be a JSON object")
    rule = retention_rule_from_mapping(rule_raw)
    rule_sha = hashlib.sha256(rule_bytes).hexdigest()

    quote_files = tuple(quote_files)
    files: dict[str, bytes] = {}
    for supplied in quote_files:
        path = supplied if supplied.is_absolute() else root / supplied
        relative = _relative_dataset_path(root, path)
        if relative in files:
            raise ValueError(f"duplicate quote dataset file: {relative}")
        files[relative] = path.read_bytes()
    if not files:
        raise ValueError("at least one quote dataset file is required")

    manifest = build_quote_manifest(files, rule=rule, rule_sha256=rule_sha)
    verify_quote_manifest_files(manifest, files)
    payload = quote_manifest_json(manifest).encode("utf-8")

    destination = output_path if output_path.is_absolute() else root / output_path
    if destination.is_symlink():
        raise ValueError(f"manifest output must not be a symlink: {destination}")
    destination_resolved = destination.resolve(strict=False)
    try:
        destination_resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"manifest output must stay inside dataset root: {destination}") from exc

    input_paths = {
        (supplied if supplied.is_absolute() else root / supplied).resolve(strict=True)
        for supplied in quote_files
    }
    if destination_resolved in input_paths:
        raise ValueError(f"manifest output must not overwrite quote dataset input: {destination}")

    destination = destination_resolved
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=f".{destination.name}.", delete=False) as tmp:
        tmp.write(payload)
        tmp.flush()
        temporary = Path(tmp.name)
    temporary.replace(destination)

    # Byte proof after the write: output must be exactly the canonical payload.
    if destination.read_bytes() != payload:
        raise RuntimeError("materialized manifest bytes differ from canonical payload")
    return manifest, payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--quote-file", type=Path, action="append", required=True, help="JSONL path under dataset root; repeat for each frozen dataset file")
    parser.add_argument("--rule", type=Path, default=DEFAULT_RULE)
    parser.add_argument("--output", type=Path, default=Path("option_quotes_manifest.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest, payload = materialize_manifest(
        dataset_root=args.dataset_root,
        quote_files=args.quote_file,
        rule_path=args.rule,
        output_path=args.output,
    )
    print(f"manifest_sha256={hashlib.sha256(payload).hexdigest()}")
    print(f"files={len(manifest['files'])}")
    print(f"rows={sum(int(entry['row_count']) for entry in manifest['files'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
