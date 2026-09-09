#!/usr/bin/env python3
"""Current-engine MES strat_122 slippage robustness study."""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from scripts.mes_122_controlled_one_variable_tests import (  # noqa: E402
    _compare_months,
    _fixed_one_contract_config,
    _run_pass,
    _summarize_run,
    _variant_advance,
)

SLIPPAGE_TICKS = (1.0, 2.0, 3.0)


def run(out_path: Path) -> dict:
    results = {
        "design": {
            "strategy": "strat_122",
            "instrument": "MES",
            "contracts": 1,
            "slippage_ticks": list(SLIPPAGE_TICKS),
            "pessimistic_same_bar": True,
            "commission_round_trip": 1.48,
            "strategy_change": "none",
        },
        "slippage": {},
    }

    base_cfg = _fixed_one_contract_config(isolated=True)
    with tempfile.TemporaryDirectory(prefix="mes122_slippage_") as tmp:
        root = Path(tmp)
        for slip in SLIPPAGE_TICKS:
            name = f"slip_{int(slip)}t"
            print(f"[{name}] current baseline, fixed 1 contract", flush=True)
            cfg = dataclasses.replace(base_cfg, fill_slippage_ticks=slip)
            run_data = _run_pass(
                cfg,
                root / name,
                advance_fn=_variant_advance("baseline"),
            )
            results["slippage"][name] = _summarize_run(run_data)

        baseline = results["slippage"]["slip_1t"]["metrics"]
        for name in ("slip_2t", "slip_3t"):
            results["slippage"][name]["vs_1t_months"] = _compare_months(
                baseline,
                results["slippage"][name]["metrics"],
            )

    results["screen"] = {}
    for name in ("slip_1t", "slip_2t", "slip_3t"):
        m = results["slippage"][name]["metrics"]
        results["screen"][name] = {
            "net_positive": m["commission_adjusted_net"] > 0,
            "pf_above_1": (
                m["commission_adjusted_pf"] is not None
                and m["commission_adjusted_pf"] > 1.0
            ),
            "h1_positive": m["h1_commission_adjusted"] > 0,
            "h2_positive": m["h2_commission_adjusted"] > 0,
            "no_unresolved": results["slippage"][name]["unresolved_count"] == 0,
            "no_cancelled": results["slippage"][name]["cancelled_count"] == 0,
        }

    results["robust_through_3t"] = all(
        all(
            results["screen"][name][key]
            for key in ("net_positive", "pf_above_1", "h1_positive", "h2_positive")
        )
        for name in ("slip_1t", "slip_2t", "slip_3t")
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "slippage": {
                    name: results["slippage"][name]["metrics"]
                    for name in ("slip_1t", "slip_2t", "slip_3t")
                },
                "screen": results["screen"],
                "robust_through_3t": results["robust_through_3t"],
            },
            indent=2,
        )
    )
    print(f"wrote {out_path}")
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO / "scripts" / "mes_122_current_baseline_slippage_stress_2026-09-08.json",
    )
    args = parser.parse_args()
    run(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
