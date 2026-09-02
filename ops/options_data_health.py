"""Read-only provider / data health for the options advisory lane.

    python -m ops.options_data_health --ticker XYZ --observations obs.json
    python -m ops.options_data_health --ticker XYZ --collect

READY / DEGRADED / BLOCKED from what the read-only sources actually returned:
quote, bars, prior close, option chain/Greeks, timestamps, calendar alignment,
and Signa provenance. Missing required evidence fails closed. GEX is always
UNAVAILABLE unless an independently verified feed exists.
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
REQUIRED_SOURCES = ("quote", "prior_close", "bars", "chain")
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

    prior = observations.get("prior_close")
    if prior is not None and prior.ok is True:
        value = _finite(prior.fields.get("close"))
        close_date = str(prior.fields.get("date") or "")
        if value is None or value <= 0:
            blocked.append("prior_close: no finite positive close")
            status["prior_close"] = BLOCKED
        elif not close_date:
            degraded.append("prior_close: close date missing")
            status["prior_close"] = DEGRADED
        elif close_date >= now.astimezone(ET).date().isoformat():
            blocked.append(f"prior_close: dated {close_date}, not a prior session")
            status["prior_close"] = BLOCKED
        else:
            status["prior_close"] = READY

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
                if age is not None and age > 2 * interval_min + 1:
                    degraded.append(f"bars: last bar {age:.0f} min old during session")
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
                error=str(data.get("error") or "Signa request failed"),
                provider="signa",
            )
        return SourceObservation(
            name="signa",
            ok=True,
            observed_at=data.get("retrieved_at") or now.isoformat(),
            provider="signa",
            fields={
                "direction": data.get("direction"),
                "grade": data.get("grade"),
                "score": data.get("score"),
                "technicals_as_of": data.get("technicals_as_of"),
                "stale": data.get("stale"),
                "requested_tf": data.get("requested_tf"),
            },
        )
    except Exception as exc:
        return _obs_from_exception("signa", exc, "signa")


def public_quote_collector(ticker: str, now: datetime) -> SourceObservation:
    """Reuse the existing Public read-only quote client.

    A retrieval timestamp is never substituted for provider time. If Public's
    quote payload does not carry a real source timestamp, the observation is
    returned with ``observed_at=None`` and the evaluator blocks it.
    """
    try:
        from alert_ranker.config import load_config
        from alert_ranker.market_data import PublicMarketDataClient

        cfg = load_config()
        client = PublicMarketDataClient(
            cfg,
            api_key=cfg.public_api_secret,
            account_id=cfg.public_account_id,
        )
        if not client.configured:
            return SourceObservation(
                name="quote",
                ok=False,
                error="Public quote credentials/account not configured",
                provider="public",
            )
        snapshot = client.fetch_snapshot(ticker)
        if snapshot is None:
            return SourceObservation(
                name="quote",
                ok=False,
                error=client.last_error or "Public snapshot unavailable",
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
                "staleness": snapshot.staleness,
            },
        )
    except Exception as exc:
        return _obs_from_exception("quote", exc, "public")


def alpaca_bars_collector(ticker: str, now: datetime) -> SourceObservation:
    """Reuse the existing causal Alpaca SIP bar transport.

    The transport may itself be delayed. We still apply the repo's completed-bar
    cutoff and explicitly retain only regular-session bars so a delayed provider
    becomes DEGRADED/BLOCKED rather than being backfilled or guessed.
    """
    try:
        from alert_ranker.bar_provider import AlpacaBarProvider
        from alert_ranker.causal_bars import MINUTE_5, completed_bars
        from alert_ranker.config import load_config, resolve_alpaca_credentials

        cfg = load_config()
        key, secret = resolve_alpaca_credentials()
        if not (key and secret):
            return SourceObservation(
                name="bars",
                ok=False,
                error="Alpaca data credentials not configured",
                provider="alpaca-sip",
            )
        transport = AlpacaBarProvider(
            api_key=key,
            secret_key=secret,
            data_url=cfg.alpaca_data_url,
            feed="sip",
        )
        # Ask only for enough history to cover today's regular session plus one
        # preceding interval. Causal completion is enforced after retrieval.
        start = now.astimezone(ET).replace(hour=9, minute=25, second=0, microsecond=0)
        raw = transport.get_bars(
            ticker,
            MINUTE_5,
            start.astimezone(timezone.utc),
            now.astimezone(timezone.utc),
        )
        safe = completed_bars(
            raw,
            timeframe=MINUTE_5,
            now=now.astimezone(timezone.utc),
            delay_seconds=cfg.sip_delay_buffer_seconds,
        )
        regular = [
            bar for bar in safe
            if time(9, 30) <= bar.timestamp.astimezone(ET).time() < time(16, 0)
        ]
        if not regular:
            return SourceObservation(
                name="bars",
                ok=False,
                error="no causally completed regular-session 5m Alpaca SIP bars",
                provider="alpaca-sip",
            )
        first = regular[0]
        last = regular[-1]
        return SourceObservation(
            name="bars",
            ok=True,
            observed_at=now.isoformat(),
            provider="alpaca-sip",
            fields={
                "count": len(regular),
                "interval_minutes": 5,
                "first_bar_start": first.timestamp.isoformat(),
                "last_bar_end": (last.timestamp + timedelta(minutes=5)).isoformat(),
                "bounds": "regular",
                "causal_delay_seconds": cfg.sip_delay_buffer_seconds,
            },
        )
    except Exception as exc:
        return _obs_from_exception("bars", exc, "alpaca-sip")


def polygon_prior_close_collector(ticker: str, now: datetime) -> SourceObservation:
    try:
        from alert_ranker.config import load_config
        from alert_ranker.market_data import PolygonMarketDataClient

        cfg = load_config()
        client = PolygonMarketDataClient(cfg.polygon_api_key)
        if not client.configured:
            return SourceObservation(
                name="prior_close",
                ok=False,
                error="Polygon API key not configured",
                provider="polygon",
            )
        snapshot = client.fetch_snapshot(ticker)
        if snapshot is None or snapshot.prev_close is None:
            return SourceObservation(
                name="prior_close",
                ok=False,
                error=client.last_error or "Polygon prior close unavailable",
                provider="polygon",
            )
        prior_day = now.astimezone(ET).date() - timedelta(days=1)
        return SourceObservation(
            name="prior_close",
            ok=True,
            observed_at=now.isoformat(),
            provider="polygon",
            fields={"close": snapshot.prev_close, "date": prior_day.isoformat()},
        )
    except Exception as exc:
        return _obs_from_exception("prior_close", exc, "polygon")


def public_chain_collector(ticker: str, now: datetime) -> SourceObservation:
    """Use the existing Public read-only chain provider; never synthesize Greeks."""
    try:
        import asyncio

        from alert_ranker.config import load_config
        from options_companion.chain_provider import PublicChainProvider

        cfg = load_config()
        provider = PublicChainProvider(
            base_url=cfg.public_base_url,
            api_key=cfg.public_api_secret,
            account_id=cfg.public_account_id,
        )

        async def _run():
            async with provider as p:
                return await p.fetch_chain(ticker, max_dte=90)

        snapshot = asyncio.run(_run())
        if snapshot.error:
            return SourceObservation(
                name="chain",
                ok=False,
                error=snapshot.error,
                provider="public",
            )
        if not snapshot.contracts:
            return SourceObservation(
                name="chain",
                ok=False,
                error="Public chain returned no contracts",
                provider="public",
            )
        contracts = list(snapshot.contracts)
        expirations = sorted({c.expiry.isoformat() for c in contracts})

        def _complete(c: Any) -> bool:
            return all(
                getattr(c, field_name, None) not in (None, "")
                for field_name in ("bid", "ask", "volume", "open_interest", "iv", "delta", "theta", "updated_at")
            )

        sample = next((c for c in contracts if _complete(c)), contracts[0])
        return SourceObservation(
            name="chain",
            ok=True,
            observed_at=now.isoformat(),
            provider="public",
            fields={
                "expirations": expirations,
                "contract_count": len(contracts),
                "sample_contract": {
                    "symbol": sample.symbol,
                    "expiration": sample.expiry.isoformat(),
                    "strike": sample.strike,
                    "bid": sample.bid,
                    "ask": sample.ask,
                    "volume": sample.volume,
                    "open_interest": sample.open_interest,
                    "iv": sample.iv,
                    "delta": sample.delta,
                    "theta": sample.theta,
                    "updated_at": sample.updated_at,
                },
            },
        )
    except Exception as exc:
        return _obs_from_exception("chain", exc, "public")


def default_collectors() -> Mapping[str, Collector]:
    return {
        "calendar": calendar_collector,
        "quote": public_quote_collector,
        "prior_close": polygon_prior_close_collector,
        "bars": alpaca_bars_collector,
        "chain": public_chain_collector,
        "signa": signa_collector,
    }


def collect_live_observations(
    ticker: str,
    *,
    now: datetime,
    collectors: Optional[Mapping[str, Collector]] = None,
) -> dict[str, SourceObservation]:
    selected = collectors or default_collectors()
    out: dict[str, SourceObservation] = {}
    for name, collector in selected.items():
        try:
            out[name] = collector(ticker, now)
        except Exception as exc:  # fail closed even for injected/custom collectors
            out[name] = _obs_from_exception(name, exc)
    return out


def _load_observations(path: Path) -> dict[str, SourceObservation]:
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("observation file must contain an object")
    out: dict[str, SourceObservation] = {}
    for name, value in raw.items():
        if not isinstance(value, dict):
            raise ValueError(f"observation {name!r} must be an object")
        out[name] = SourceObservation(
            name=name,
            ok=value.get("ok"),
            observed_at=value.get("observed_at"),
            fields=value.get("fields") if isinstance(value.get("fields"), dict) else {},
            error=value.get("error"),
            provider=str(value.get("provider") or ""),
        )
    return out


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--observations", type=Path, help="JSON observation fixture")
    group.add_argument("--collect", action="store_true", help="collect using wired read-only sources")
    parser.add_argument("--now", help="timezone-aware ISO timestamp (fixture mode only)")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.collect and args.now:
        print(json.dumps({"error": "--now is fixture-only; live collection uses the real clock"}, sort_keys=True))
        return 2
    if args.collect:
        now = datetime.now(timezone.utc)
        observations = collect_live_observations(args.ticker, now=now)
    else:
        now = _ts(args.now) if args.now else datetime.now(timezone.utc)
        if now is None:
            print(json.dumps({"error": "--now must be timezone-aware"}, sort_keys=True))
            return 2
        observations = _load_observations(args.observations)
    report = evaluate_data_health(observations, ticker=args.ticker, now=now)
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    return 0 if report.status == READY else 1


if __name__ == "__main__":
    sys.exit(main())
