#!/usr/bin/env python3
"""SPXW-only paper P&L rollup (read-only, evidence only).

Never folds into the equity 66-symbol OPTIONS_PAPER_V1 cohort. Reads the
isolated ``options_spxw_shadow_journal`` and emits 0DTE / 1+DTE metrics.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alert_ranker.spxw_storage import SpxwStorage, build_spxw_rollup  # noqa: E402
from ops.gate_condition_report import _post_discord  # noqa: E402

ET = ZoneInfo("America/New_York")
DEFAULT_DB = "logs/options_spxw_scanner.sqlite"


def build_report(db_path: str | Path) -> dict:
    path = Path(db_path)
    if not path.exists():
        return {
            "generated_at": datetime.now(ET).isoformat(),
            "lane": "SPX_SPXW_PAPER",
            "paper_policy_id": "OPTIONS_PAPER_SPXW_V1",
            "mixed_with_equity_universe": False,
            "rows": 0,
            "by_cohort": {},
            "authority": "evidence_only",
        }
    storage = SpxwStorage(path)
    rollup = build_spxw_rollup(storage.all_rows())
    rollup["generated_at"] = datetime.now(ET).isoformat()
    rollup["authority"] = "evidence_only"
    return rollup


def format_digest(rep: dict) -> str:
    by = rep.get("by_cohort") or {}
    zero = by.get("0DTE") or {}
    plus = by.get("1_PLUS_DTE") or {}
    lines = [
        f"**SPX→SPXW paper P&L** · policy `{rep.get('paper_policy_id')}`",
        (
            f"0DTE: closed={zero.get('closed', 0)} · "
            f"pnl={zero.get('pnl_dollars', 0.0)} · "
            f"expectancy={zero.get('expectancy')}"
        ),
        (
            f"1+DTE: closed={plus.get('closed', 0)} · "
            f"pnl={plus.get('pnl_dollars', 0.0)} · "
            f"expectancy={plus.get('expectancy')}"
        ),
        f"Open (lane-local): {rep.get('open', 0)} · rows={rep.get('rows', 0)}",
        "[paper only · isolated from equity OPTIONS_PAPER_V1 · no live orders]",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        default=os.getenv("OPTIONS_SPXW_SQLITE_PATH", DEFAULT_DB),
    )
    parser.add_argument("--log-dir", default=os.getenv("LOG_DIR", "logs"))
    parser.add_argument("--json", action="store_true", help="print JSON instead of digest")
    parser.add_argument(
        "--discord",
        action="store_true",
        help="post digest to DISCORD_OPTIONS_SPXW_DAILY_REPORT (optional)",
    )
    args = parser.parse_args(argv)

    report = build_report(args.db)
    day = date.fromisoformat(str(report.get("generated_at", ""))[:10]) if report.get("generated_at") else datetime.now(ET).date()
    try:
        text = json.dumps(report, indent=2)
        (Path(args.log_dir) / f"options_spxw_daily_pnl_{day.isoformat()}.json").write_text(text)
        (Path(args.log_dir) / "options_spxw_daily_pnl_latest.json").write_text(text)
    except OSError:
        pass
    digest = format_digest(report)
    print(json.dumps(report, indent=2) if args.json else digest)
    if args.discord:
        url = os.getenv("DISCORD_OPTIONS_SPXW_DAILY_REPORT")
        if url:
            print("discord:", "ok" if _post_discord(url, digest) else "FAILED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
