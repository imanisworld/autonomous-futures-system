"""Read-only option-contract census for broader V1 universe selection.

This is deliberately separate from the normalized-data capacity preflight.  It
uses the configured read-only market-data provider to fetch expirations and one
V1-selected chain per symbol, then applies the frozen OPTIONS_PAPER_V1 contract
quality rules to CALL and PUT independently.

No setup scan is run.  No sqlite is opened.  No alert or broker/order path is
available.  The live watchlist is never modified.

Examples:

  # Audit a proposed 20-symbol activation set during RTH
  python scripts/options_v1_contract_preflight.py \
    --tickers AAPL,MSFT,NVDA,TSLA,SPY,QQQ,AMZN,GOOGL,PLTR,INTC,IWM,TLT,JPM,BAC,COIN,XOM,MRK,WMT,NFLX,GE \
    --json

  # Full 149-candidate census (intentionally explicit; many provider calls)
  python scripts/options_v1_contract_preflight.py --all-candidates --out /tmp/v1-contract-census.json

The report separates frozen-V1 validity from the operator's separate preference
for total premium <= $300/contract.  The latter is selection telemetry only and
does not silently rewrite the frozen V1 paper policy.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.config import load_config  # noqa: E402
from alert_ranker.market_data import build_provider_capabilities, create_market_data_client  # noqa: E402
from alert_ranker.session_calendar import EXCHANGE_TIMEZONE, nyse_session_for  # noqa: E402
from alert_ranker.v1_contract_capacity import (  # noqa: E402
    PREFERRED_MAX_CONTRACT_COST_DOLLARS,
    run_contract_census,
)
from alert_ranker.v1_universe import load_candidate_universe, ticker_list  # noqa: E402

PREFLIGHT_ID = "OPTIONS_V1_CONTRACT_PREFLIGHT"
PREFLIGHT_VERSION = "contract-cap-v0.2"


def _git_source() -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()

    try:
        return {
            "sha": run("rev-parse", "HEAD"),
            "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "dirty": bool(run("status", "--porcelain", "--untracked-files=no")),
        }
    except (OSError, subprocess.CalledProcessError):
        return {"sha": None, "branch": None, "dirty": None}


def _parse_tickers(text: str | None) -> tuple[str, ...]:
    if not text:
        return ()
    values = tuple(item.strip().upper() for item in text.split(",") if item.strip())
    if len(values) != len(set(values)):
        raise ValueError("duplicate ticker in --tickers")
    return values


def _is_rth(now: datetime) -> bool:
    """Use the same NYSE holiday/early-close calendar as the live scanner."""
    exchange_now = now.astimezone(ZoneInfo(EXCHANGE_TIMEZONE))
    session = nyse_session_for(exchange_now.date())
    if session is None:
        return False
    current = exchange_now.astimezone(timezone.utc)
    return session.open <= current < session.close


async def _run(tickers: tuple[str, ...], preferred_cost: float) -> tuple[int, dict[str, Any]]:
    cfg = load_config()
    now = datetime.now(ZoneInfo(cfg.timezone))
    capabilities = build_provider_capabilities(cfg)
    base: dict[str, Any] = {
        "preflight_id": PREFLIGHT_ID,
        "preflight_version": PREFLIGHT_VERSION,
        "source": _git_source(),
        "observed_at": now.isoformat(),
        "candidate_count": len(tickers),
        "live_watchlist": list(cfg.watchlist),
        "live_watchlist_changed": False,
        "market_calendar": "same_nyse_session_calendar_as_live_scanner",
        "provider": capabilities.to_dict(),
        "preferred_max_contract_cost_dollars": preferred_cost,
        "frozen_v1_policy_note": (
            "V1 planned-risk cap is $300 with a 25% premium stop; "
            "the <=$300 total contract cost preference is reported separately"
        ),
        "scope": "read-only expirations + one V1-selected chain per symbol; no scan/storage/alerts/orders",
        "storage_writes": 0,
        "alerts_sent": 0,
    }

    preconditions: list[str] = []
    if not tickers:
        preconditions.append("empty_ticker_set")
    if not capabilities.configured:
        preconditions.append("market_data_unconfigured")
    if not capabilities.read_only:
        preconditions.append("provider_not_read_only")
    if not capabilities.options_supported:
        preconditions.append("provider_options_unsupported")
    if not _is_rth(now):
        preconditions.append("outside_regular_market_hours")
    if preferred_cost <= 0:
        preconditions.append("invalid_preferred_contract_cost")
    if preconditions:
        base.update({"verdict": "FAIL", "reasons": preconditions, "report": None})
        return 1, base

    async with create_market_data_client(cfg) as provider:
        report = await run_contract_census(
            provider,
            tickers,
            now=now,
            preferred_max_contract_cost_dollars=preferred_cost,
        )

    payload = dict(base)
    payload["verdict"] = report.verdict
    payload["reasons"] = list(report.reasons)
    payload["report"] = report.to_dict()
    return (0 if report.verdict == "PASS" else 1), payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--tickers", help="Comma-separated symbols to census")
    scope.add_argument(
        "--all-candidates",
        action="store_true",
        help="Explicitly census all 149 derived V1 candidates",
    )
    parser.add_argument(
        "--preferred-max-contract-cost",
        type=float,
        default=PREFERRED_MAX_CONTRACT_COST_DOLLARS,
        help="Informational total-premium preference in dollars (default: 300)",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON report")
    parser.add_argument("--out", help="Also write full JSON report to this path")
    args = parser.parse_args(argv)

    try:
        tickers = (
            ticker_list(load_candidate_universe())
            if args.all_candidates
            else _parse_tickers(args.tickers)
        )
        code, payload = asyncio.run(_run(tickers, args.preferred_max_contract_cost))
    except Exception as exc:
        payload = {
            "preflight_id": PREFLIGHT_ID,
            "preflight_version": PREFLIGHT_VERSION,
            "source": _git_source(),
            "verdict": "FAIL",
            "reasons": [f"exception:{type(exc).__name__}:{exc}"],
            "live_watchlist_changed": False,
            "storage_writes": 0,
            "alerts_sent": 0,
        }
        code = 1

    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(rendered + "\n", encoding="utf-8")
    if args.json or code != 0:
        print(rendered)
    else:
        report = payload.get("report") or {}
        print(
            f"{payload['verdict']}: tested={report.get('tested_count')} "
            f"v1_both={len(report.get('symbols_v1_both_sides') or [])} "
            f"preferred_cost_both={len(report.get('symbols_preferred_cost_both_sides') or [])} "
            f"no_v1_contract={len(report.get('symbols_no_v1_contract') or [])} "
            f"elapsed={report.get('total_elapsed_seconds')}s; live watchlist unchanged"
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
