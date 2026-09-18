#!/usr/bin/env python3
"""Emit a canonical read-only snapshot of the options aggregate-risk budget.

The snapshot contains only the non-secret aggregate-risk env key and its finite
positive value. It does not mutate environment/config, inspect accounts, call a
broker/provider, or activate any runtime path. Redirect stdout to a file when a
frozen evidence artifact is needed.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Mapping

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from options_manager.validation.portfolio_risk_gate import (
    AGGREGATE_RISK_BUDGET_ENV,
)


def build_budget_snapshot(environ: Mapping[str, str]) -> dict[str, object]:
    raw = str(environ.get(AGGREGATE_RISK_BUDGET_ENV, "") or "").strip()
    if not raw:
        raise ValueError(f"{AGGREGATE_RISK_BUDGET_ENV} is not configured")

    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"{AGGREGATE_RISK_BUDGET_ENV} must be numeric"
        ) from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError(
            f"{AGGREGATE_RISK_BUDGET_ENV} must be finite and > 0"
        )
    return {
        "schema_version": 1,
        "source_env_key": AGGREGATE_RISK_BUDGET_ENV,
        "max_aggregate_open_risk_dollars": value,
    }


def snapshot_json(snapshot: Mapping[str, object]) -> str:
    return json.dumps(snapshot, sort_keys=True, separators=(",", ":")) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env-file",
        type=Path,
        default=ROOT / ".env",
        help="Local env file to read without modifying it.",
    )
    args = parser.parse_args(argv)

    if not args.env_file.is_file():
        print(
            json.dumps(
                {"verdict": "BLOCKED", "reason": "env file is unavailable"},
                sort_keys=True,
            )
        )
        return 2

    source = {
        key: str(value)
        for key, value in dotenv_values(args.env_file).items()
        if value is not None
    }
    try:
        snapshot = build_budget_snapshot(source)
    except ValueError as exc:
        print(
            json.dumps(
                {"verdict": "BLOCKED", "reason": str(exc)},
                sort_keys=True,
            )
        )
        return 2

    sys.stdout.write(snapshot_json(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
