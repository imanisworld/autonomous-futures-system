"""Read-only capacity preflight for a broader V1 options universe.

Runs the scanner's *normalized-data build stage only* in the same serial ticker
order used by V1.  It never calls scan_ticker/scan_watchlist, never opens the V1
sqlite, never sends Discord, and deliberately exposes no option-chain methods to
the scanner.  The probe therefore measures current snapshot + causal-bar +
Signa enrichment latency/failures without creating evidence or contracts.

By default the script audits the full derived 149-symbol candidate universe.  A
comma-separated ``--tickers`` subset is available for an incremental activation
proof (for example, the proposed 20-symbol V1 cohort) without changing the live
watchlist.

Run during regular market hours so quote freshness and the causal-bar cutoff are
representative of the live five-minute cycle:

    python scripts/options_v1_capacity_preflight.py --json
    python scripts/options_v1_capacity_preflight.py --tickers AAPL,MSFT,SPY,QQQ --json
    python scripts/options_v1_capacity_preflight.py --out /tmp/v1-capacity.json

A PASS is necessary, not sufficient, for activation.  Contract-chain capacity is
a separate proof handled by ``options_v1_contract_preflight.py``.
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

from alert_ranker.bar_context import create_bar_context  # noqa: E402
from alert_ranker.config import load_config  # noqa: E402
from alert_ranker.market_data import create_market_data_client  # noqa: E402
from alert_ranker.scanner import OptionsScanner  # noqa: E402
from alert_ranker.signa_v2_observer import signa_v2_observe_enabled, signa_v2_timeframe  # noqa: E402
from alert_ranker.v1_capacity import run_serial_capacity_preflight  # noqa: E402
from alert_ranker.v1_universe import load_candidate_universe, ticker_list  # noqa: E402

PREFLIGHT_ID = "OPTIONS_V1_CAPACITY_PREFLIGHT"
PREFLIGHT_VERSION = "cap-v0.2"


class _ForbiddenSideEffect:
    """Explode if the data-build path ever reaches storage/alert behavior."""

    def __init__(self, label: str):
        self.label = label

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(f"capacity_preflight_forbidden_side_effect:{self.label}.{name}")


class _SnapshotOnlyMarketData:
    """Expose only the underlying quote call needed by normalized-data build.

    Option expiration/chain access is intentionally absent.  If a future
    refactor starts requiring it in the data-build stage, this preflight must
    fail instead of silently becoming a contract-fetch lane.
    """

    def __init__(self, delegate: Any):
        self.delegate = delegate
        self.provider_name = getattr(delegate, "provider_name", "")

    @property
    def last_error(self) -> str | None:
        return getattr(self.delegate, "last_error", None)

    async def fetch_market_snapshot(self, ticker: str):
        return await self.delegate.fetch_market_snapshot(ticker)


class _RecordingBarProvider:
    """Preserve transport failure detail the production scanner intentionally compresses.

    ``multisetup_scanner`` turns a ``BarProviderError`` into a stable reason
    such as ``provider_error`` / ``provider_unavailable``.  That is correct for
    trading telemetry, but a capacity audit needs the underlying ``HTTP 429``
    or ``ReadTimeout`` detail.  This wrapper records the exception and re-raises
    it unchanged, so scanner behavior is identical while the preflight can
    classify rate limits/timeouts accurately.
    """

    def __init__(self, delegate: Any):
        self.delegate = delegate
        self.feed = getattr(delegate, "feed", "")
        self.last_reason: str | None = None
        self.last_detail: str | None = None

    def reset(self) -> None:
        self.last_reason = None
        self.last_detail = None

    async def fetch_bars(self, symbols, timeframe, start, end):
        self.reset()
        try:
            return await self.delegate.fetch_bars(symbols, timeframe, start, end)
        except Exception as exc:
            self.last_reason = str(getattr(exc, "reason", type(exc).__name__) or type(exc).__name__)
            self.last_detail = str(getattr(exc, "detail", "") or exc)
            raise


class _CapacityScannerView:
    """Add preflight-only bar transport detail to normalized scanner telemetry."""

    def __init__(self, scanner: Any, bar_provider: _RecordingBarProvider):
        self.scanner = scanner
        self.bar_provider = bar_provider

    async def _build_normalized_data(self, ticker: str, context: dict[str, Any], now: datetime):
        self.bar_provider.reset()
        data = await self.scanner._build_normalized_data(ticker, context, now)
        if self.bar_provider.last_reason:
            enriched = dict(data)
            existing = str(enriched.get("bar_context_reason") or "")
            transport = self.bar_provider.last_reason
            if self.bar_provider.last_detail:
                transport += f":{self.bar_provider.last_detail}"
            enriched["bar_context_reason"] = "|".join(part for part in (existing, transport) if part)
            return enriched
        return data


def _git_source() -> dict[str, Any]:
    def run(*args: str) -> str:
        return subprocess.check_output(
            ["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()

    try:
        sha = run("rev-parse", "HEAD")
        branch = run("rev-parse", "--abbrev-ref", "HEAD")
        dirty = bool(run("status", "--porcelain", "--untracked-files=no"))
    except (OSError, subprocess.CalledProcessError):
        return {"sha": None, "branch": None, "dirty": None}
    return {"sha": sha, "branch": branch, "dirty": dirty}


def _parse_tickers(text: str | None) -> tuple[str, ...] | None:
    if text is None:
        return None
    values = tuple(item.strip().upper() for item in text.split(",") if item.strip())
    if not values:
        raise ValueError("--tickers must contain at least one symbol")
    if len(values) != len(set(values)):
        raise ValueError("duplicate ticker in --tickers")
    return values


async def _run(requested_tickers: tuple[str, ...] | None = None) -> tuple[int, dict[str, Any]]:
    cfg = load_config()
    full_candidate_set = ticker_list(load_candidate_universe())
    tickers = requested_tickers if requested_tickers is not None else full_candidate_set
    unknown = tuple(ticker for ticker in tickers if ticker not in set(full_candidate_set))
    now = datetime.now(ZoneInfo(cfg.timezone))

    # The live V1 data path is bar-context dependent.  Do not benchmark a
    # degraded/off configuration and call it capacity proof.
    preconditions: list[str] = []
    if unknown:
        preconditions.append(f"ticker_not_in_candidate_universe:{','.join(unknown)}")
    if not cfg.market_data_configured:
        preconditions.append("market_data_unconfigured")
    if not cfg.bar_context_enabled:
        preconditions.append("bar_context_disabled")
    if not cfg.bar_context_configured:
        preconditions.append("bar_context_unconfigured")
    if cfg.bar_context_feed != "sip":
        preconditions.append(f"bar_context_feed_not_sip:{cfg.bar_context_feed}")
    if cfg.bar_context_timeframe != "30Min":
        preconditions.append(f"bar_context_timeframe_not_30Min:{cfg.bar_context_timeframe}")

    base: dict[str, Any] = {
        "preflight_id": PREFLIGHT_ID,
        "preflight_version": PREFLIGHT_VERSION,
        "source": _git_source(),
        "candidate_count": len(tickers),
        "candidate_scope": "subset" if requested_tickers is not None else "full_149",
        "candidate_tickers": list(tickers),
        "live_watchlist": list(cfg.watchlist),
        "live_watchlist_changed": False,
        "market_data_provider": cfg.market_data_provider,
        "configured_interval_minutes": cfg.interval_minutes,
        "bar_context": {
            "enabled": cfg.bar_context_enabled,
            "configured": cfg.bar_context_configured,
            "feed": cfg.bar_context_feed,
            "timeframe": cfg.bar_context_timeframe,
            "lookback_days": cfg.bar_context_lookback_days,
            "sip_delay_buffer_seconds": cfg.sip_delay_buffer_seconds,
        },
        "signa": {
            "legacy_enabled": cfg.signa_api_enabled,
            "v2_observe_enabled": signa_v2_observe_enabled(cfg),
            "v2_timeframe": signa_v2_timeframe(cfg),
        },
        "scope": "normalized-data build only; serial; no contracts/storage/alerts",
        "contract_fetches": 0,
        "storage_writes": 0,
        "alerts_sent": 0,
    }

    if preconditions:
        base.update({"verdict": "FAIL", "reasons": preconditions, "report": None})
        return 1, base

    # Capacity proof must represent a scheduled live cycle, not a stale after-
    # hours quote path.  The scanner method is calendar-aware and read-only.
    async with create_market_data_client(cfg) as provider:
        bar_context = create_bar_context(cfg)
        if bar_context is None:  # defensive: preconditions above should catch this
            base.update({"verdict": "FAIL", "reasons": ["bar_context_unconfigured"], "report": None})
            return 1, base
        bar_probe = _RecordingBarProvider(bar_context.provider)
        bar_context.provider = bar_probe
        scanner = OptionsScanner(
            cfg,
            _SnapshotOnlyMarketData(provider),
            _ForbiddenSideEffect("storage"),
            _ForbiddenSideEffect("discord"),
            bar_context=bar_context,
        )
        if not scanner.is_market_hours(now):
            base.update({"verdict": "FAIL", "reasons": ["outside_market_hours"], "report": None})
            return 1, base

        report = await run_serial_capacity_preflight(
            _CapacityScannerView(scanner, bar_probe),
            tickers,
            now=now,
            interval_seconds=float(cfg.interval_minutes * 60),
        )

    payload = dict(base)
    payload["verdict"] = report.verdict
    payload["reasons"] = list(report.reasons)
    payload["report"] = report.to_dict()
    return (0 if report.verdict == "PASS" else 1), payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tickers",
        help="Optional comma-separated subset from the validated 149-symbol candidate universe",
    )
    parser.add_argument("--json", action="store_true", help="Print the full machine-readable report")
    parser.add_argument("--out", help="Also write the full JSON report to this path")
    args = parser.parse_args(argv)

    try:
        code, payload = asyncio.run(_run(_parse_tickers(args.tickers)))
    except Exception as exc:  # fail closed; never turn a probe crash into PASS
        payload = {
            "preflight_id": PREFLIGHT_ID,
            "preflight_version": PREFLIGHT_VERSION,
            "source": _git_source(),
            "verdict": "FAIL",
            "reasons": [f"exception:{type(exc).__name__}:{exc}"],
            "live_watchlist_changed": False,
            "contract_fetches": 0,
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
            f"{payload['verdict']}: {payload.get('candidate_count')} symbols in "
            f"{report.get('total_elapsed_seconds')}s / "
            f"{report.get('configured_interval_seconds')}s; "
            f"critical_failures={report.get('critical_failures')} "
            f"rate_limited={len(report.get('rate_limited_symbols') or [])} "
            f"timeouts={len(report.get('timed_out_symbols') or [])}; "
            "live watchlist unchanged"
        )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
