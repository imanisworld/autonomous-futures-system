#!/usr/bin/env python3
"""CLI for the AFS Experiment Runner.

Examples:
  python scripts/afs_experiment_runner.py discover
  python scripts/afs_experiment_runner.py validate --spec docs/research-experiment-specs/examples/E-2026-09-25-demo-single-variable-01.json
  python scripts/afs_experiment_runner.py run --experiment-id E-YYYY-MM-DD-slug-01

Never invents experiments. Never promotes, merges, deploys, or changes strategy settings.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ops.research_experiment_runner import (
    EXECUTABLE_STATUS,
    RUNNER_VERSION,
    discover_specs,
    execute_experiment,
    format_report_text,
    repo_root,
    run_validation,
)

from ops.research_experiment_adapters import register_builtin_adapters

register_builtin_adapters()


def _resolve_spec_path(root: Path, args: argparse.Namespace) -> Path:
    if args.spec:
        path = Path(args.spec)
        return path if path.is_absolute() else root / path
    if args.experiment_id:
        live = root / "docs" / "research-experiment-specs" / f"{args.experiment_id}.json"
        example = (
            root
            / "docs"
            / "research-experiment-specs"
            / "examples"
            / f"{args.experiment_id}.json"
        )
        if live.is_file():
            return live
        if example.is_file():
            return example
        raise SystemExit(f"experiment spec not found for id {args.experiment_id}")
    raise SystemExit("provide --spec or --experiment-id")


def cmd_discover(root: Path, args: argparse.Namespace) -> int:
    status = None if args.all_statuses else (args.status or EXECUTABLE_STATUS)
    paths = discover_specs(root, status=status, include_examples=args.include_examples)
    payload = {
        "runner_version": RUNNER_VERSION,
        "status_filter": status,
        "count": len(paths),
        "specs": [
            str(p.relative_to(root)) if p.is_relative_to(root) else str(p) for p in paths
        ],
    }
    print(json.dumps(payload, indent=2))
    return 0


def cmd_validate(root: Path, args: argparse.Namespace) -> int:
    path = _resolve_spec_path(root, args)
    report = run_validation(root, path, for_execution=args.for_execution)
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(format_report_text(report))
    return 0 if report.status == "VALID" and not args.for_execution else (
        0 if report.status == "VALID" else 2
    )


def cmd_run(root: Path, args: argparse.Namespace) -> int:
    path = _resolve_spec_path(root, args)
    report = execute_experiment(
        root,
        path,
        write_evidence=not args.no_write_evidence,
        evidence_dir=Path(args.evidence_dir) if args.evidence_dir else None,
    )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        print(format_report_text(report))
    return 0 if report.status == "VALID" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser("discover", help="List experiment specs")
    discover.add_argument("--status", default=EXECUTABLE_STATUS)
    discover.add_argument("--all-statuses", action="store_true")
    discover.add_argument("--include-examples", action="store_true")
    discover.set_defaults(func=cmd_discover)

    validate = sub.add_parser("validate", help="Validate a spec without executing")
    validate.add_argument("--spec", type=str, default=None)
    validate.add_argument("--experiment-id", type=str, default=None)
    validate.add_argument(
        "--for-execution",
        action="store_true",
        help="Apply execution gates (APPROVED, resolvable SHAs, adapter present)",
    )
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=cmd_validate)

    run = sub.add_parser("run", help="Execute an APPROVED experiment (fail-closed)")
    run.add_argument("--spec", type=str, default=None)
    run.add_argument("--experiment-id", type=str, default=None)
    run.add_argument("--json", action="store_true")
    run.add_argument("--no-write-evidence", action="store_true")
    run.add_argument("--evidence-dir", type=str, default=None)
    run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = repo_root(args.repo_root) if args.repo_root else repo_root(ROOT)
    return int(args.func(root, args))


if __name__ == "__main__":
    raise SystemExit(main())
