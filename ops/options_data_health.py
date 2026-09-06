"""Read-only provider / data health for the options advisory lane.

    python -m ops.options_data_health --ticker XYZ --observations obs.json
    python -m ops.options_data_health --ticker XYZ --collect

READY / DEGRADED / BLOCKED from what the read-only sources actually returned:
quote, bars, option chain/Greeks, timestamps, calendar alignment, and Signa
provenance. Missing required evidence fails closed. GEX is always UNAVAILABLE
unless an independently verified feed exists.

A prior-session close was evaluated and removed (2026-09-03): nothing in this
package or the wider options advisory lane consumes it -- it fed no
sanity-check, no display, no downstream decision, only its own presence in
REQUIRED_SOURCES. Its only provider (Polygon, via polygon_prior_close_collector)
was not otherwise used anywhere in this codebase or configured in this
deployment, and this PR's own history already found Polygon's related
intraday-bars path returns 403 here. Requiring a new, unused-elsewhere
credential to satisfy a check with no consumer is not a real readiness
requirement; if a genuine prior-close need appears later, the already-wired,
already-authenticated Alpaca bar provider (used for bars/calendar in this same
module) is the provider to reach for, not Polygon.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
READY, DEGRADED, BLOCKED = "READY", "DEGRADED", "BLOCKED"
GEX_STATUS = "UNAVAILABLE (no independently verified GEX feed)"
REQUIRED_SOURCES = ("quote", "bars", "chain")
REQUIRED_CONTRACT_FIELDS = (
    "bid", "ask", "volume", "open_interest", "iv", "delta", "theta", "updated_at"
)
QUOTE_STALE_DEGRADED_MIN = 5
QUOTE_STALE_BLOCKED_MIN = 30
CHAIN_STALE_DEGRADED_MIN = 15
SIGNA_STALE_DAYS = 5


@dataclass(frozen=True)
class SourceObservation:
    name: str
    ok: Optional[bool] = None
    observed_at: Optional[str] = None
    fields: Mapping[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    provider: str = ""


@dataclass(frozen=True)
class DataHealthReport:
    ticker: str
    status: str
    checked_at: str
    source_status: Mapping[str, str]
    reasons: tuple[str, ...]
    gex: str = GEX_STATUS
    in_session: Optional[bool] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ts(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else None
    if not isinstance(value, str) or not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return None if (out != out or out in (float("inf"), float("-inf"))) else out


def _age_minutes(now: datetime, when: Optional[datetime]) -> Optional[float]:
    return None if when is None else (now - when).total_seconds() / 60.0


def session_window(day: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day, time(9, 30), tzinfo=ET),
        datetime.combine(day, time(16, 0), tzinfo=ET),
    )


def _calendar_bounds(calendar: SourceObservation) -> tuple[Optional[datetime], Optional[datetime]]:
    return _ts(calendar.fields.get("session_open")), _ts(calendar.fields.get("session_close"))


def evaluate_data_health(
    observations: Mapping[str, SourceObservation], *, ticker: str, now: datetime
) -> DataHealthReport:
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    blocked: list[str] = []
    degraded: list[str] = []
    status: dict[str, str] = {}

    calendar = observations.get("calendar")
    in_session: Optional[bool] = None
    session_open: Optional[datetime] = None
    session_close: Optional[datetime] = None
    if calendar is None or calendar.ok is not True:
        degraded.append(
            "calendar: session/calendar alignment unknown"
            + (f" ({calendar.error})" if calendar and calendar.error else "")
        )
        status["calendar"] = DEGRADED
    elif not bool(calendar.fields.get("is_trading_day")):
        blocked.append("calendar: not a trading day")
        status["calendar"] = BLOCKED
    else:
        session_open, session_close = _calendar_bounds(calendar)
        if session_open is None or session_close is None or session_close <= session_open:
            degraded.append("calendar: verified session open/close unavailable")
            status["calendar"] = DEGRADED
        else:
            in_session = session_open <= now < session_close
            status["calendar"] = READY

    for name in REQUIRED_SOURCES:
        obs = observations.get(name)
        if obs is None or obs.ok is not True:
            blocked.append(
                f"{name}: unavailable"
                + (f" ({obs.error})" if obs and obs.error else "")
            )
            status[name] = BLOCKED

    quote = observations.get("quote")
    if quote is not None and quote.ok is True:
        last = _finite(quote.fields.get("last"))
        when = _ts(quote.observed_at)
        if last is None or last <= 0:
            blocked.append("quote: no finite positive last price")
            status["quote"] = BLOCKED
        elif when is None:
            blocked.append("quote: no source timestamp")
            status["quote"] = BLOCKED
        else:
            age = _age_minutes(now, when)
            if age is not None and age < -1:
                blocked.append(f"quote: timestamp {age:.1f} min in the future")
                status["quote"] = BLOCKED
            elif in_session and age is not None and age > QUOTE_STALE_BLOCKED_MIN:
                blocked.append(f"quote: {age:.0f} min stale during session")
                status["quote"] = BLOCKED
            elif in_session and age is not None and age > QUOTE_STALE_DEGRADED_MIN:
                degraded.append(f"quote: {age:.0f} min stale during session")
                status["quote"] = DEGRADED
            else:
                status["quote"] = READY

    bars = observations.get("bars")
    if bars is not None and bars.ok is True:
        count = bars.fields.get("count")
        interval_min = _finite(bars.fields.get("interval_minutes"))
        last_end = _ts(bars.fields.get("last_bar_end"))
        first_start = _ts(bars.fields.get("first_bar_start"))
        if not isinstance(count, int) or count <= 0:
            blocked.append("bars: no bars returned")
            status["bars"] = BLOCKED
        elif interval_min is None or last_end is None:
            blocked.append("bars: interval or last bar timestamp missing")
            status["bars"] = BLOCKED
        elif last_end > now + timedelta(minutes=1):
            blocked.append(
                f"bars: last bar ends {last_end.isoformat()} after now (future leakage)"
            )
            status["bars"] = BLOCKED
        else:
            s = READY
            if in_session:
                if (
                    first_start is not None
                    and session_open is not None
                    and first_start > session_open + timedelta(minutes=interval_min)
                ):
                    degraded.append(
                        "bars: today's first bar starts after the session open (misaligned)"
                    )
                    s = DEGRADED
                age = _age_minutes(now, last_end)
                if age is not None:
                    # The collector (alpaca_bars_collector) never returns a bar
                    # less than delay_buffer_seconds old by design -- it only
                    # trusts SIP data at or before now - delay_buffer_seconds,
                    # so the last eligible bar's age is causally guaranteed to
                    # fall in [delay_buffer_min, delay_buffer_min + interval_min)
                    # even when everything is healthy. A fixed threshold below
                    # that floor (the old 2*interval_min+1 = 11 min) is
                    # unsatisfiable whenever delay_buffer_min exceeds it, which
                    # it always does at the configured 16 min -- proven live on
                    # 2026-09-03 (18 min old, in-range, wrongly flagged
                    # DEGRADED). Derive the threshold from the buffer that
                    # actually governs the data instead of a fixed number that
                    # ignores it; a missing buffer (e.g. --observations JSON
                    # without it) falls back to the old, more conservative
                    # bound rather than silently trusting an unknown delay.
                    delay_buffer_min = _finite(bars.fields.get("delay_buffer_seconds"))
                    if delay_buffer_min is not None:
                        delay_buffer_min = delay_buffer_min / 60.0
                        max_healthy_age = delay_buffer_min + interval_min + 1
                    else:
                        max_healthy_age = 2 * interval_min + 1
                    if age > max_healthy_age:
                        degraded.append(
                            f"bars: last bar {age:.0f} min old during session "
                            f"(expected <= {max_healthy_age:.0f} min given the causal delay)"
                        )
                        s = DEGRADED
            if bars.fields.get("bounds") not in (None, "regular"):
                degraded.append(
                    f"bars: bounds={bars.fields.get('bounds')!r}, not regular session"
                )
                s = DEGRADED
            status["bars"] = s

    chain = observations.get("chain")
    if chain is not None and chain.ok is True:
        expirations = chain.fields.get("expirations") or []
        sample = chain.fields.get("sample_contract") or {}
        s = READY
        if not isinstance(expirations, list) or not expirations:
            blocked.append("chain: no expirations")
            s = BLOCKED
        else:
            today = now.astimezone(ET).date()
            valid_expirations = []
            for expiry in expirations:
                if not isinstance(expiry, str):
                    continue
                try:
                    parsed = date.fromisoformat(expiry)
                except ValueError:
                    continue
                if parsed >= today:
                    valid_expirations.append(expiry)
            if not valid_expirations:
                blocked.append("chain: no parseable current/future expiration")
                s = BLOCKED

        missing = [
            name for name in REQUIRED_CONTRACT_FIELDS
            if sample.get(name) in (None, "")
        ]
        if missing:
            blocked.append(f"chain: sampled contract missing {', '.join(missing)}")
            s = BLOCKED
        else:
            for name in ("bid", "ask", "volume", "open_interest", "iv", "delta", "theta"):
                if _finite(sample.get(name)) is None:
                    blocked.append(f"chain: sampled contract {name} is not finite")
                    s = BLOCKED
            bid = _finite(sample.get("bid"))
            ask = _finite(sample.get("ask"))
            if bid is not None and ask is not None:
                if bid <= 0 or ask <= 0:
                    blocked.append("chain: sampled contract bid/ask must be positive")
                    s = BLOCKED
                elif ask <= bid:
                    degraded.append("chain: sampled quote is crossed or locked")
                    if s != BLOCKED:
                        s = DEGRADED
            updated = _ts(sample.get("updated_at"))
            age = _age_minutes(now, updated)
            if updated is None:
                blocked.append("chain: sampled contract updated_at unparseable")
                s = BLOCKED
            elif age is not None and age < -1:
                blocked.append(f"chain: sampled quote timestamp {age:.1f} min in the future")
                s = BLOCKED
            elif (
                in_session
                and age is not None
                and age > CHAIN_STALE_DEGRADED_MIN
                and s != BLOCKED
            ):
                degraded.append(f"chain: sampled quote {age:.0f} min stale during session")
                s = DEGRADED
        status["chain"] = s
        status["greeks"] = (
            BLOCKED
            if any(reason.startswith("chain: sampled contract") for reason in blocked)
            else READY
        )
    else:
        status["greeks"] = BLOCKED

    signa = observations.get("signa")
    if signa is None or signa.ok is not True:
        degraded.append(
            "signa: unavailable (observational only)"
            + (f" ({signa.error})" if signa and signa.error else "")
        )
        status["signa"] = DEGRADED
    else:
        as_of = str(signa.fields.get("technicals_as_of") or "")[:10]
        s = READY
        if not signa.fields.get("grade") or signa.fields.get("score") is None:
            degraded.append("signa: grade/score missing (observational only)")
            s = DEGRADED
        if not as_of:
            degraded.append("signa: technicals_as_of missing (observational only)")
            s = DEGRADED
        else:
            try:
                age_days = (now.astimezone(ET).date() - date.fromisoformat(as_of)).days
            except ValueError:
                age_days = None
            if age_days is None or age_days > SIGNA_STALE_DAYS:
                degraded.append(
                    f"signa: technicals_as_of {as_of} is stale "
                    f"({age_days} days; observational only)"
                )
                s = DEGRADED
        if signa.fields.get("stale") is True:
            degraded.append(
                "signa: provider marks the signal stale (observational only)"
            )
            s = DEGRADED
        status["signa"] = s

    status["gex"] = "UNAVAILABLE"
    overall = BLOCKED if blocked else DEGRADED if degraded else READY
    return DataHealthReport(
        ticker=ticker.upper(),
        status=overall,
        checked_at=now.isoformat(timespec="seconds"),
        source_status=dict(status),
        reasons=tuple(blocked) + tuple(degraded),
        in_session=in_session,
    )


Collector = Callable[[str, datetime], SourceObservation]


def _obs_from_exception(
    name: str, exc: BaseException, provider: str = ""
) -> SourceObservation:
    return SourceObservation(
        name=name,
        ok=False,
        error=f"{type(exc).__name__}: {exc}",
        provider=provider,
    )


def calendar_collector(ticker: str, now: datetime) -> SourceObservation:
    """Reuse the repo's exchange calendar; never infer a session from weekday/bars."""
    try:
        import asyncio

        from alert_ranker.config import load_config, resolve_alpaca_credentials
        from alert_ranker.session_calendar import AlpacaSessionCalendar

        cfg = load_config()
        key, secret = resolve_alpaca_credentials()
        if not (key and secret):
            return SourceObservation(
                name="calendar",
                ok=False,
                error="Alpaca calendar credentials not configured",
                provider="alpaca-calendar",
            )
        calendar = AlpacaSessionCalendar(
            cfg.alpaca_trading_base_url,
            key,
            secret,
        )

        async def _run():
            return await calendar.session_for(now.astimezone(ET).date())

        session = asyncio.run(_run())
        if session is None:
            return SourceObservation(
                name="calendar",
                ok=True,
                observed_at=now.isoformat(),
                provider="alpaca-calendar",
                fields={"is_trading_day": False},
            )
        return SourceObservation(
            name="calendar",
            ok=True,
            observed_at=now.isoformat(),
            provider="alpaca-calendar",
            fields={
                "is_trading_day": True,
                "session_open": session.open.isoformat(),
                "session_close": session.close.isoformat(),
                "is_early_close": session.is_early_close,
            },
        )
    except Exception as exc:
        return _obs_from_exception("calendar", exc, "alpaca-calendar")


def signa_collector(ticker: str, now: datetime) -> SourceObservation:
    try:
        from sources.signa_client import SignaClient

        client = SignaClient(
            api_key=os.getenv("SIGNA_API_KEY", ""),
            base_url=os.getenv("SIGNA_BASE_URL", "https://app.getsigna.ai"),
        )
        if not client.configured:
            return SourceObservation(
                name="signa",
                ok=False,
                error="SIGNA_API_KEY not configured",
                provider="signa",
            )
        signal = client.fetch_signal(ticker)
        data = signal.to_dict()
        if not data.get("ok"):
            return SourceObservation(
                name="signa",
                ok=False,
                error=str(data.get("error") or "signal not ok"),
                provider="signa",
            )
        return SourceObservation(
            name="signa",
            ok=True,
            observed_at=data.get("retrieved_at"),
            provider="signa",
            fields={
                key: data.get(key)
                for key in (
                    "grade",
                    "score",
                    "daily_direction",
                    "technicals_as_of",
                    "engine_run_at",
                    "stale",
                    "retrieved_at",
                )
            },
        )
    except Exception as exc:
        return _obs_from_exception("signa", exc, "signa")


def public_quote_collector(ticker: str, now: datetime) -> SourceObservation:
    """Reuse the existing Public read-only quote client; never invent a timestamp."""
    try:
        import asyncio

        from alert_ranker.config import load_config
        from alert_ranker.market_data import PublicMarketDataClient

        cfg = load_config()
        if not (cfg.public_api_key_configured and cfg.public_account_id):
            return SourceObservation(
                name="quote",
                ok=False,
                error="Public market-data credentials/account id not configured",
                provider="public",
            )

        async def _run():
            async with PublicMarketDataClient(cfg) as provider:
                return await provider.fetch_market_snapshot(ticker)

        snapshot = asyncio.run(_run())
        if snapshot.price is None:
            return SourceObservation(
                name="quote",
                ok=False,
                error=snapshot.error or "quote returned no price",
                provider="public",
            )
        if not snapshot.quote_timestamp:
            return SourceObservation(
                name="quote",
                ok=False,
                error="Public quote returned no provider timestamp",
                provider="public",
            )
        return SourceObservation(
            name="quote",
            ok=True,
            observed_at=snapshot.quote_timestamp,
            provider="public",
            fields={
                "last": snapshot.price,
                "bid": snapshot.bid,
                "ask": snapshot.ask,
                "volume": snapshot.volume,
                "provider_stale": snapshot.stale,
            },
            error=snapshot.error,
        )
    except Exception as exc:
        return _obs_from_exception("quote", exc, "public")


def alpaca_bars_collector(ticker: str, now: datetime) -> SourceObservation:
    """Use the existing SIP transport and its measured delay boundary."""
    try:
        import asyncio

        from alert_ranker.bar_provider import AlpacaBarProvider
        from alert_ranker.causal_bars import MINUTE_5, completed_bars
        from alert_ranker.config import load_config, resolve_alpaca_credentials

        cfg = load_config()
        key, secret = resolve_alpaca_credentials()
        if not (key and secret):
            return SourceObservation(
                name="bars",
                ok=False,
                error="Alpaca market-data credentials not configured",
                provider="alpaca-sip",
            )

        local_day = now.astimezone(ET).date()
        open_at, close_at = session_window(local_day)
        cutoff = now.astimezone(timezone.utc) - timedelta(
            seconds=cfg.sip_delay_buffer_seconds
        )
        start = open_at.astimezone(timezone.utc)
        if cutoff <= start:
            return SourceObservation(
                name="bars",
                ok=False,
                error="no causally available regular-session bars yet",
                provider="alpaca-sip",
                fields={"information_cutoff": cutoff.isoformat()},
            )

        provider = AlpacaBarProvider(
            base_url=cfg.alpaca_data_base_url,
            api_key=key,
            secret_key=secret,
            feed="sip",
        )

        async def _run():
            return await provider.fetch_bars([ticker], MINUTE_5, start, cutoff)

        raw = asyncio.run(_run()).get(ticker.upper(), [])
        closed = completed_bars(raw, MINUTE_5, cutoff)
        regular = [
            bar
            for bar in closed
            if open_at <= bar.start_utc.astimezone(ET) < close_at
        ]
        if not regular:
            return SourceObservation(
                name="bars",
                ok=False,
                error="no completed regular-session bars before information cutoff",
                provider="alpaca-sip",
                fields={"information_cutoff": cutoff.isoformat()},
            )
        first = regular[0].start_utc
        last_end = regular[-1].start_utc + MINUTE_5.delta
        return SourceObservation(
            name="bars",
            ok=True,
            observed_at=now.isoformat(),
            provider="alpaca-sip",
            fields={
                "count": len(regular),
                "interval_minutes": 5,
                "bounds": "regular",
                "first_bar_start": first.isoformat(),
                "last_bar_end": last_end.isoformat(),
                "information_cutoff": cutoff.isoformat(),
                "delay_buffer_seconds": cfg.sip_delay_buffer_seconds,
            },
        )
    except Exception as exc:
        return _obs_from_exception("bars", exc, "alpaca-sip")


def _contract_has_health_fields(contract: Any) -> bool:
    return all(
        getattr(contract, name, None) is not None
        for name in ("bid", "ask", "volume", "open_interest", "iv", "delta", "theta", "updated_at")
    )


def public_chain_collector(ticker: str, now: datetime) -> SourceObservation:
    """Read-only Public chain; absent provider fields stay absent and BLOCK."""
    try:
        import asyncio

        from options_companion.chain_provider import PublicChainProvider

        api_key = os.getenv("PUBLIC_API_SECRET_KEY", "") or os.getenv(
            "PUBLIC_API_KEY", ""
        )
        account_id = os.getenv("PUBLIC_ACCOUNT_ID", "")
        if not api_key:
            return SourceObservation(
                name="chain",
                ok=False,
                error="PUBLIC_API_SECRET_KEY/PUBLIC_API_KEY not configured",
                provider="public",
            )
        if not account_id:
            return SourceObservation(
                name="chain",
                ok=False,
                error="PUBLIC_ACCOUNT_ID not configured",
                provider="public",
            )

        async def _run():
            async with PublicChainProvider(
                api_key=api_key, account_id=account_id
            ) as provider:
                return await provider.fetch_chain(ticker, max_dte=45)

        snapshot = asyncio.run(_run())
        if snapshot.error:
            return SourceObservation(
                name="chain",
                ok=False,
                error=snapshot.error,
                provider="public",
            )
        contracts = snapshot.contracts or []
        if not contracts:
            return SourceObservation(
                name="chain",
                ok=False,
                error="chain returned no contracts",
                provider="public",
            )
        expirations = sorted({contract.expiry.isoformat() for contract in contracts})
        sample = next((c for c in contracts if _contract_has_health_fields(c)), contracts[0])
        return SourceObservation(
            name="chain",
            ok=True,
            observed_at=now.isoformat(),
            provider="public",
            fields={
                "expirations": expirations,
                "contract_count": len(contracts),
                "underlying_price": snapshot.underlying_price,
                "sample_contract": {
                    "symbol": sample.symbol,
                    "bid": sample.bid,
                    "ask": sample.ask,
                    "iv": sample.iv,
                    "delta": sample.delta,
                    "theta": sample.theta,
                    "volume": sample.volume,
                    "open_interest": sample.open_interest,
                    "updated_at": sample.updated_at,
                },
            },
        )
    except Exception as exc:
        return _obs_from_exception("chain", exc, "public")


DEFAULT_COLLECTORS: dict[str, Collector] = {
    "calendar": calendar_collector,
    "quote": public_quote_collector,
    "bars": alpaca_bars_collector,
    "chain": public_chain_collector,
    "signa": signa_collector,
}


def collect_observations(
    ticker: str,
    *,
    now: datetime,
    collectors: Optional[Mapping[str, Collector]] = None,
) -> dict[str, SourceObservation]:
    out: dict[str, SourceObservation] = {}
    for name, collector in (collectors or DEFAULT_COLLECTORS).items():
        try:
            out[name] = collector(ticker, now)
        except Exception as exc:
            out[name] = _obs_from_exception(name, exc)
    return out


def observations_from_json(
    payload: Mapping[str, Any],
) -> dict[str, SourceObservation]:
    out: dict[str, SourceObservation] = {}
    for name, raw in payload.items():
        if not isinstance(raw, Mapping):
            out[name] = SourceObservation(
                name=name, ok=False, error="observation is not an object"
            )
            continue
        out[name] = SourceObservation(
            name=name,
            ok=raw.get("ok") if isinstance(raw.get("ok"), bool) else None,
            observed_at=raw.get("observed_at"),
            fields=dict(raw.get("fields") or {}),
            error=raw.get("error"),
            provider=str(raw.get("provider") or ""),
        )
    return out


def render(
    report: DataHealthReport,
    observations: Mapping[str, SourceObservation],
) -> str:
    lines = [
        f"DATA HEALTH {report.ticker}  {report.status}  "
        f"at {report.checked_at}  in_session={report.in_session}"
    ]
    for name, source_state in report.source_status.items():
        if name == "gex":
            continue
        obs = observations.get(name)
        provider = f" via {obs.provider}" if obs and obs.provider else ""
        lines.append(f"  {name:<12}{source_state}{provider}")
    lines.append(f"  gex         {report.gex}")
    for reason in report.reasons:
        lines.append(f"    - {reason}")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ops.options_data_health", description=__doc__
    )
    parser.add_argument("--ticker", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--observations",
        help="JSON file of {source: {ok, observed_at, fields, error, provider}}",
    )
    group.add_argument(
        "--collect",
        action="store_true",
        help="collect via the repo's read-only providers",
    )
    parser.add_argument(
        "--now",
        default=None,
        help="ISO timestamp (default: current UTC time)",
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    now = _ts(args.now) if args.now else datetime.now(timezone.utc)
    if now is None:
        print(
            "--now must be a timezone-aware ISO timestamp",
            file=sys.stderr,
        )
        return 2

    if args.observations:
        observations = observations_from_json(
            json.loads(
                Path(args.observations).read_text(encoding="utf-8")
            )
        )
    else:
        observations = collect_observations(args.ticker, now=now)

    report = evaluate_data_health(
        observations, ticker=args.ticker, now=now
    )
    if args.json:
        print(
            json.dumps(
                {
                    "report": report.to_dict(),
                    "observations": {
                        key: asdict(value)
                        for key, value in observations.items()
                    },
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
        )
    else:
        print(render(report, observations))
    return {READY: 0, DEGRADED: 1, BLOCKED: 2}[report.status]


if __name__ == "__main__":
    sys.exit(main())
