#!/usr/bin/env python3
"""Read-only probe for a timely 212R trigger-evidence source.

The probe calls only Public market-data/historicdata endpoints and, optionally,
Alpaca historical SIP bars for delayed cross-provider comparison.  It never
calls trading/account/order endpoints and never writes unless --out is given.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv  # noqa: E402

from alert_ranker.bar_provider import AlpacaBarProvider, BarProviderError  # noqa: E402
from alert_ranker.causal_bars import MINUTE_5, MINUTE_30, build_session_timeframe  # noqa: E402
from alert_ranker.config import load_config, resolve_alpaca_credentials  # noqa: E402
from alert_ranker.market_data import PublicMarketDataClient  # noqa: E402
from alert_ranker.public_chart_bars import PUBLIC_CHART_SOURCE, parse_regular_market_bars  # noqa: E402
from alert_ranker.session_calendar import nyse_session_for  # noqa: E402
from strategy.strat_classifier import StratBar, classify_bar  # noqa: E402

DEFAULT_TICKERS = ("SPY", "QQQ", "AMZN")
HISTORICDATA_PREFIX = "/userapigateway/historicdata"


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _age_seconds(reference: datetime, value: Any) -> float | None:
    parsed = _parse_ts(value)
    return round((reference - parsed).total_seconds(), 3) if parsed else None


def _is_future(reference: datetime, value: Any) -> bool | None:
    parsed = _parse_ts(value)
    return parsed > reference if parsed else None


def _scenario(current: Any, previous: Any) -> str:
    return classify_bar(
        StratBar(high=current.high, low=current.low),
        StratBar(high=previous.high, low=previous.low),
    )


async def _public_chart(pub: PublicMarketDataClient, ticker: str, period: str) -> dict[str, Any]:
    if period not in {"DAY", "WEEK"}:
        raise ValueError("unsupported Public chart period")
    token = await pub._ensure_token()
    if token is None:
        raise RuntimeError(pub.last_error or "public_auth_failed")
    path = f"{HISTORICDATA_PREFIX}/EQUITY/{ticker}/{period}"
    # The path is fixed to the read-only historicdata surface; no caller input
    # can alter the prefix or instrument type.
    client = pub._ensure_client()
    response = await client.get(
        path,
        headers={"Authorization": f"Bearer {token}"},
        params={"tradingSessionToggle": "REGULAR_HOURS"},
    )
    if not response.is_success:
        raise RuntimeError(f"public_historicdata_http_{response.status_code}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("public_historicdata_bad_shape")
    return payload


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.env_file:
        load_dotenv(args.env_file, override=True)
    cfg = load_config()
    now = datetime.now(timezone.utc)
    session = nyse_session_for(now.date())
    if session is None:
        raise RuntimeError("not_a_nyse_session")
    tickers = tuple(dict.fromkeys(item.upper() for item in args.ticker))

    report: dict[str, Any] = {
        "probe_id": "OPTIONS_212R_TRIGGER_SOURCE_PROBE",
        "captured_at": now.isoformat(),
        "public_chart_source": PUBLIC_CHART_SOURCE,
        "tickers": list(tickers),
        "public": {},
        "sip_comparison": None,
        "claims_not_made": [
            "Public bars are byte-identical to Alpaca SIP",
            "212R has positive option expectancy",
            "strategy promotion",
            "DEMO or live authorization",
        ],
    }

    public5: dict[str, tuple[Any, ...]] = {}
    async with PublicMarketDataClient(cfg) as pub:
        for ticker in tickers:
            quote_body = await pub._post_marketdata(
                pub._marketdata_path("quotes"),
                {"instruments": [{"symbol": ticker, "type": "EQUITY"}]},
            )
            quote_received_at = datetime.now(timezone.utc)
            quote_rows = (quote_body or {}).get("quotes") if isinstance(quote_body, dict) else None
            quote = quote_rows[0] if isinstance(quote_rows, list) and quote_rows else {}
            day = await _public_chart(pub, ticker, "DAY")
            week = await _public_chart(pub, ticker, "WEEK")
            day_bars = parse_regular_market_bars(
                day, timeframe=MINUTE_5, decision_ts=now, session=session
            )
            week_bars = parse_regular_market_bars(
                week, timeframe=MINUTE_30, decision_ts=now, session=session
            )
            public5[ticker] = day_bars.bars
            report["public"][ticker] = {
                "quote": {
                    "outcome": quote.get("outcome"),
                    "received_at": quote_received_at.isoformat(),
                    "last_timestamp": quote.get("lastTimestamp"),
                    "last_age_seconds": _age_seconds(quote_received_at, quote.get("lastTimestamp")),
                    "last_timestamp_future": _is_future(quote_received_at, quote.get("lastTimestamp")),
                    "bid_timestamp": quote.get("bidTimestamp"),
                    "bid_age_seconds": _age_seconds(quote_received_at, quote.get("bidTimestamp")),
                    "bid_timestamp_future": _is_future(quote_received_at, quote.get("bidTimestamp")),
                    "ask_timestamp": quote.get("askTimestamp"),
                    "ask_age_seconds": _age_seconds(quote_received_at, quote.get("askTimestamp")),
                    "ask_timestamp_future": _is_future(quote_received_at, quote.get("askTimestamp")),
                },
                "day_5m": {
                    "complete_bars": len(day_bars.bars),
                    "latest_complete_start": day_bars.bars[-1].start_utc.isoformat() if day_bars.bars else None,
                    "ignored_partial": day_bars.ignored_live_or_partial_rows,
                    "ignored_off_grid": day_bars.ignored_off_grid_rows,
                },
                "week_current_session_30m": {
                    "complete_bars": len(week_bars.bars),
                    "latest_complete_start": week_bars.bars[-1].start_utc.isoformat() if week_bars.bars else None,
                    "ignored_partial": week_bars.ignored_live_or_partial_rows,
                    "ignored_outside_session": week_bars.ignored_outside_session_rows,
                },
            }

    quote_rows_report = [item["quote"] for item in report["public"].values()]
    report["public_quote_summary"] = {
        "tickers": len(quote_rows_report),
        "missing_last_timestamp": sum(item.get("last_timestamp") is None for item in quote_rows_report),
        "missing_bid_timestamp": sum(item.get("bid_timestamp") is None for item in quote_rows_report),
        "missing_ask_timestamp": sum(item.get("ask_timestamp") is None for item in quote_rows_report),
        "future_last_timestamp": sum(item.get("last_timestamp_future") is True for item in quote_rows_report),
        "future_bid_timestamp": sum(item.get("bid_timestamp_future") is True for item in quote_rows_report),
        "future_ask_timestamp": sum(item.get("ask_timestamp_future") is True for item in quote_rows_report),
        "max_last_age_seconds": max(
            (float(item["last_age_seconds"]) for item in quote_rows_report if item.get("last_age_seconds") is not None),
            default=None,
        ),
        "max_bid_age_seconds": max(
            (float(item["bid_age_seconds"]) for item in quote_rows_report if item.get("bid_age_seconds") is not None),
            default=None,
        ),
        "max_ask_age_seconds": max(
            (float(item["ask_age_seconds"]) for item in quote_rows_report if item.get("ask_age_seconds") is not None),
            default=None,
        ),
    }

    if args.compare_sip:
        key, secret = resolve_alpaca_credentials()
        if not key or not secret:
            raise RuntimeError("alpaca_credentials_missing")
        cutoff = now - timedelta(minutes=args.sip_delay_minutes)
        provider = AlpacaBarProvider(
            base_url=cfg.alpaca_data_base_url,
            api_key=key,
            secret_key=secret,
            feed="sip",
        )
        try:
            sip5 = await provider.fetch_bars(tickers, MINUTE_5, session.open, cutoff)
        except BarProviderError as exc:
            raise RuntimeError(f"sip_compare_blocked:{exc.reason}") from exc

        exact_hilo = scenario_total = scenario_match = 0
        max_hilo_diff = 0.0
        per_ticker: dict[str, Any] = {}
        for ticker in tickers:
            p30 = build_session_timeframe(public5[ticker], MINUTE_5, MINUTE_30, session.open)
            s30 = build_session_timeframe(sip5[ticker], MINUTE_5, MINUTE_30, session.open)
            pmap = {bar.start_utc: bar for bar in p30}
            smap = {bar.start_utc: bar for bar in s30}
            common = sorted(set(pmap) & set(smap))
            local_exact = local_total = local_scen = local_match = 0
            diffs: list[float] = []
            for start in common:
                if start + MINUTE_30.delta > cutoff:
                    continue
                local_total += 1
                diff = max(
                    abs(pmap[start].high - smap[start].high),
                    abs(pmap[start].low - smap[start].low),
                )
                diffs.append(diff)
                max_hilo_diff = max(max_hilo_diff, diff)
                if diff < 1e-9:
                    local_exact += 1
            for previous, current in zip(common, common[1:]):
                if current - previous != MINUTE_30.delta or current + MINUTE_30.delta > cutoff:
                    continue
                local_scen += 1
                if _scenario(pmap[current], pmap[previous]) == _scenario(smap[current], smap[previous]):
                    local_match += 1
            exact_hilo += local_exact
            scenario_total += local_scen
            scenario_match += local_match
            per_ticker[ticker] = {
                "comparable_30m_bars": local_total,
                "exact_high_low": local_exact,
                "scenario_pairs": local_scen,
                "scenario_matches": local_match,
                "max_high_low_abs_diff": round(max(diffs), 6) if diffs else None,
            }
        total_bars = sum(item["comparable_30m_bars"] for item in per_ticker.values())
        report["sip_comparison"] = {
            "cutoff": cutoff.isoformat(),
            "sip_delay_minutes": args.sip_delay_minutes,
            "comparable_30m_bars": total_bars,
            "exact_high_low": exact_hilo,
            "exact_high_low_percent": round(100 * exact_hilo / total_bars, 2) if total_bars else None,
            "scenario_pairs": scenario_total,
            "scenario_matches": scenario_match,
            "scenario_match_percent": round(100 * scenario_match / scenario_total, 2) if scenario_total else None,
            "max_high_low_abs_diff": round(max_hilo_diff, 6),
            "per_ticker": per_ticker,
        }
    report["completed_at"] = datetime.now(timezone.utc).isoformat()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--env-file")
    parser.add_argument("--compare-sip", action="store_true")
    parser.add_argument("--sip-delay-minutes", type=int, default=16)
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    if not args.ticker:
        args.ticker = list(DEFAULT_TICKERS)
    report = asyncio.run(run(args))
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        Path(args.out).write_text(payload + "\n")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
