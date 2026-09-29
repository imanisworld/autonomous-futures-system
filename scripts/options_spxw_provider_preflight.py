#!/usr/bin/env python3
"""Read-only SPX→SPXW provider reachability preflight.

Checks that real providers can:
1. Fetch an SPX underlying snapshot
2. List SPXW expirations
3. Fetch at least one SPXW chain (preferring same-day / 0DTE when listed)

Fails closed when credentials are missing. Never accepts mocks/stubs as PASS.
Never enables the SPXW paper lane, never writes the SPXW journal, never
touches Discord, and never mutates OPTIONS_SCANNER_WATCHLIST.

    python scripts/options_spxw_provider_preflight.py --json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.config import load_config  # noqa: E402
from alert_ranker.market_data import create_market_data_client  # noqa: E402
from alert_ranker.paper_spxw_v1 import (  # noqa: E402
    CONTRACT_ROOT,
    SIGNAL_UNDERLYING,
    list_eligible_expirations,
)

PREFLIGHT_ID = "OPTIONS_SPXW_PROVIDER_PREFLIGHT"
PREFLIGHT_VERSION = "spxw-prov-v0.1"


def _git_source() -> dict[str, Any]:
    def _run(*args: str) -> str:
        try:
            return subprocess.check_output(args, cwd=ROOT, text=True).strip()
        except (OSError, subprocess.CalledProcessError):
            return ""

    return {
        "sha": _run("git", "rev-parse", "HEAD"),
        "branch": _run("git", "rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(_run("git", "status", "--porcelain")),
    }


def _missing_deps(cfg: Any) -> list[str]:
    missing: list[str] = []
    if not cfg.public_api_key_configured and cfg.market_data_provider == "public":
        missing.append("missing_env:PUBLIC_API_SECRET_KEY_or_PUBLIC_API_KEY")
    if cfg.market_data_provider == "public" and not cfg.public_account_id:
        missing.append("missing_env:PUBLIC_ACCOUNT_ID")
    if not cfg.market_data_configured:
        missing.append("market_data_unconfigured")
    return missing


async def _probe(cfg: Any) -> dict[str, Any]:
    now = datetime.now(ZoneInfo(cfg.timezone))
    out: dict[str, Any] = {
        "preflight_id": PREFLIGHT_ID,
        "preflight_version": PREFLIGHT_VERSION,
        "source": _git_source(),
        "signal_underlying": SIGNAL_UNDERLYING,
        "contract_root": CONTRACT_ROOT,
        "lane_enabled_in_env": bool(cfg.spxw_paper_lane_enabled),
        "lane_must_remain_disabled_for_pass": True,
        "real_providers_required": True,
        "stubs_accepted": False,
        "deploy_performed": False,
        "spxw_journal_written": False,
        "discord_side_effects": False,
        "checks": {},
    }
    missing = _missing_deps(cfg)
    if missing:
        out.update({"verdict": "FAIL", "reasons": missing, "missing_provider_dependencies": missing})
        return out
    if cfg.spxw_paper_lane_enabled:
        # Reachability probe must not require the lane to be on; refuse PASS if
        # someone accidentally enabled it in the probe environment.
        out.update(
            {
                "verdict": "FAIL",
                "reasons": ["spxw_lane_enabled_during_provider_preflight"],
                "missing_provider_dependencies": [],
            }
        )
        return out

    reasons: list[str] = []
    async with create_market_data_client(cfg) as market_data:
        # 1) SPX underlying
        try:
            snap = await market_data.fetch_market_snapshot(SIGNAL_UNDERLYING)
            price = getattr(snap, "price", None)
            out["checks"]["spx_snapshot"] = {
                "ok": price is not None and float(price) > 0,
                "price_present": price is not None,
            }
            if not out["checks"]["spx_snapshot"]["ok"]:
                reasons.append("spx_snapshot_missing_or_invalid_price")
        except Exception as exc:  # noqa: BLE001
            out["checks"]["spx_snapshot"] = {"ok": False, "error": type(exc).__name__}
            reasons.append(f"spx_snapshot_error:{type(exc).__name__}")

        # 2) SPXW expirations
        try:
            expirations = await market_data.fetch_option_expirations(CONTRACT_ROOT)
            eligible = list_eligible_expirations(expirations, now)
            cohorts = sorted({item.cohort for item in eligible})
            out["checks"]["spxw_expirations"] = {
                "ok": bool(eligible),
                "raw_count": len(list(expirations or [])),
                "eligible_count": len(eligible),
                "cohorts_present": cohorts,
                "has_0dte": "0DTE" in cohorts,
                "has_1_plus": "1_PLUS_DTE" in cohorts,
            }
            if not eligible:
                reasons.append("spxw_no_eligible_expiration")
        except Exception as exc:  # noqa: BLE001
            out["checks"]["spxw_expirations"] = {"ok": False, "error": type(exc).__name__}
            reasons.append(f"spxw_expiration_error:{type(exc).__name__}")
            eligible = []

        # 3) Chain for nearest eligible expiry
        if eligible:
            target = eligible[0]
            try:
                chain = await market_data.fetch_option_chain(CONTRACT_ROOT, target.expiration)
                calls = len(getattr(chain, "calls", ()) or ())
                puts = len(getattr(chain, "puts", ()) or ())
                err = getattr(chain, "error", None)
                out["checks"]["spxw_chain"] = {
                    "ok": err is None and (calls + puts) > 0,
                    "expiration": target.expiration,
                    "dte": target.dte,
                    "cohort": target.cohort,
                    "calls": calls,
                    "puts": puts,
                    "error": err,
                }
                if not out["checks"]["spxw_chain"]["ok"]:
                    reasons.append("spxw_chain_empty_or_error")
            except Exception as exc:  # noqa: BLE001
                out["checks"]["spxw_chain"] = {
                    "ok": False,
                    "expiration": target.expiration,
                    "error": type(exc).__name__,
                }
                reasons.append(f"spxw_chain_error:{type(exc).__name__}")

    out["missing_provider_dependencies"] = []
    out["verdict"] = "PASS" if not reasons else "FAIL"
    out["reasons"] = reasons
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--out", help="Write JSON report path")
    args = parser.parse_args(argv)

    # Force lane-off for this probe regardless of ambient env spelling mistakes
    # other than explicit true — load_config still reads env; we fail if enabled.
    cfg = load_config()
    try:
        payload = asyncio.run(_probe(cfg))
    except Exception as exc:  # noqa: BLE001
        payload = {
            "preflight_id": PREFLIGHT_ID,
            "preflight_version": PREFLIGHT_VERSION,
            "source": _git_source(),
            "verdict": "FAIL",
            "reasons": [f"exception:{type(exc).__name__}:{exc}"],
            "stubs_accepted": False,
            "spxw_journal_written": False,
            "discord_side_effects": False,
            "deploy_performed": False,
        }

    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
    if args.json or payload.get("verdict") != "PASS":
        print(text)
    else:
        print(f"PASS: SPX snapshot + SPXW expirations/chain reachable; lane remained disabled")
    return 0 if payload.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
