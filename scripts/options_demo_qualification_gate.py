#!/usr/bin/env python3
"""Read-only options Backtest -> DEMO qualification gate."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from options_manager.validation.demo_qualification import (
    build_options_demo_qualification_report,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--evidence-file", type=Path, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    report = build_options_demo_qualification_report(
        strategy=args.strategy,
        repo_root=ROOT,
        evidence_path=args.evidence_file,
    )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True, default=str))
    else:
        print("OPTIONS BACKTEST -> DEMO QUALIFICATION: {}".format(args.strategy))
        print("  verdict: {}".format(report.get("verdict", "BLOCKED")))
        print("  demo evidence eligible: {}".format(
            report.get("demo_evidence_eligible", False)
        ))
        blockers = report.get("blockers") or []
        if blockers:
            print("  BLOCKERS:")
            for blocker in blockers:
                print("    - {}".format(blocker))
        else:
            print("  all evidence gates passed")
        print("  independent review still required: yes")
        print("  runtime/release reconciliation still required: yes")
        print("  paper/DEMO activation authorized by this command: NO")
        print("  live options trading authorized: NO")

    return 0 if report.get("gate_pass") else 2


if __name__ == "__main__":
    raise SystemExit(main())
