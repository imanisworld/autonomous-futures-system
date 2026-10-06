"""Setup-capture collector engine: one oneshot cycle, no scanner event loop.

Injectable ``now``, bar oracle, IEX/SIP prints, and SPX index minutes. The
engine never imports Signa, GEX, chain, paper_v1, Discord, or scanner_legacy.
TRIGGERED/MISSED_LATE/GAP_THROUGH_OPEN is journalled before any optional hook.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from strategy.strat_classifier import TWO_DOWN, TWO_UP

from .causal_bars import MINUTE_30, Bar
from .session_calendar import Session, nyse_session_for
from .setup_capture import (
    CAPTURE_ID,
    CAPTURE_VERSION,
    CLOCK_SKEW_LIMIT_SECONDS,
    MAX_CAPTURE_LAG_SECONDS,
    OPTION_ROOT_SYMBOLS,
    SIP_RECONCILE_DELAY,
    SPX_MAX_BAR_AGE_SECONDS,
    STATUS_DATA_BLOCKED,
    STATUS_EXPIRED,
    STATUS_GAP_THROUGH_OPEN,
    STATUS_MISSED_LATE,
    STATUS_NO_TRIGGER,
    STATUS_TRIGGERED,
    STATUS_WATCHING,
    WATCHER_UNIVERSE,
    ArmedStructure,
    CaptureRecord,
    IndexMinuteBar,
    TapePrint,
    _aware,
    _iso,
    arm_pending_structures,
    catch_count,
    classify_iex_sip_pair,
    consume_risk_budget,
    first_boundary_from_index_minutes,
    first_boundary_from_prints,
    quote_instrument_type,
    rebuild_session_dailies,
    rebuild_session_hours,
    record_watching,
    reject_vendor_hour_bars,
    resolve_break,
    submit_broker_order,
    watcher_universe,
)
from .setup_capture_store import JournalLocked, SetupCaptureJournal

CAPTURE_TIMEFRAMES = ("30m", "1H", "1D")
FORBIDDEN_IMPORTS = frozenset(
    {
        "alert_ranker.paper_v1",
        "alert_ranker.scanner_legacy",
        "alert_ranker.discord",
        "alert_ranker.contract_marks",
        "alert_ranker.v1_evidence_hardening",
        "alert_ranker.trigger_time",
        "sources.signa_client",
        "sources.signa_discovery",
        "sources.gex_client",
        "execution",
        "execution.broker_interface",
        "options_manager.order_ticket",
    }
)


def sessions_covering(start: datetime, end: datetime) -> list[Session]:
    day = start.astimezone(timezone.utc).date()
    last = end.astimezone(timezone.utc).date()
    out: list[Session] = []
    while day <= last + timedelta(days=3):
        session = nyse_session_for(day)
        if session is not None:
            out.append(session)
        day += timedelta(days=1)
        if len(out) >= 12:
            break
    return out


class BarAvailabilityOracle:
    """Serve a bar only once ``sim_now >= bar_end + publication_lag``."""

    def __init__(
        self,
        bars: Sequence[Bar],
        *,
        publication_lag: timedelta = timedelta(0),
        bar_delta: timedelta = timedelta(minutes=30),
        session_closes: Mapping[datetime, datetime] | None = None,
    ):
        self._bars = list(bars)
        self.lag = publication_lag
        self.bar_delta = bar_delta
        self.session_closes = dict(session_closes or {})
        self.removed: set[int] = set()

    def remove(self, bar: Bar) -> None:
        self.removed.add(id(bar))

    def _end(self, bar: Bar) -> datetime:
        end = bar.start_utc + self.bar_delta
        for close in self.session_closes.values():
            if bar.start_utc < close < end:
                return close
        return end

    def serve(self, now: datetime) -> list[Bar]:
        point = _aware(now)
        out: list[Bar] = []
        for bar in self._bars:
            if id(bar) in self.removed:
                continue
            if self._end(bar) + self.lag <= point:
                out.append(bar)
        return out


@dataclass
class SetupCaptureEngine:
    journal: SetupCaptureJournal
    bar_oracle: Callable[[str, datetime], Sequence[Bar]]
    iex_prints: Callable[[str, datetime, datetime], Sequence[TapePrint]] | None = None
    sip_prints: Callable[[str, datetime, datetime], Sequence[TapePrint]] | None = None
    index_minutes: Callable[[str, datetime, datetime], Sequence[IndexMinuteBar]] | None = None
    session_lookup: Callable = nyse_session_for
    clock_offset_s: float = 0.0
    fetch_delay: timedelta = timedelta(0)
    enabled: bool = True
    last_error: str | None = None
    last_run_at: str | None = None
    cycle_seconds: float | None = None
    after_triggered: Callable[[CaptureRecord], None] | None = None
    chain_hook: Callable[[], None] | None = None
    _armed_cache: dict[str, ArmedStructure] = field(default_factory=dict)

    def telemetry(self) -> dict[str, Any]:
        counts = self.journal.counts()
        return {
            "capture_id": CAPTURE_ID,
            "capture_version": CAPTURE_VERSION,
            "enabled": self.enabled,
            "observation_only": True,
            "execution_authority": False,
            "trade_authority": False,
            "spxw_execution_enabled": False,
            "universe": list(watcher_universe()),
            "timeframes": list(CAPTURE_TIMEFRAMES),
            "journal": str(self.journal.path),
            "counts": counts,
            "watching_count": counts["watching"],
            "missed_late_count": counts["missed_late"],
            "structure_count": counts["structure_count"],
            "catch_count": catch_count(self.journal.list_all(limit=10_000)),
            "last_run_at": self.last_run_at,
            "cycle_seconds": self.cycle_seconds,
            "last_error": self.last_error,
            "integrity_note": (
                "122 collector WorkingDirectory is /root/autonomous-futures-system, "
                "not the integrity-pinned release path. This watcher journals to an "
                "absolute path under /root/afs-shared/logs."
            ),
        }

    def run(self, *, now: datetime) -> dict[str, Any]:
        started = _aware(now)
        self.last_run_at = started.isoformat()
        if not self.enabled:
            return {"ok": True, "skipped": "disabled"}
        if abs(self.clock_offset_s) > CLOCK_SKEW_LIMIT_SECONDS:
            try:
                self.journal.acquire()
            except JournalLocked:
                return {"ok": False, "status": "LOCKED"}
            try:
                self.journal.append(
                    {
                        "record_type": "COLLECTOR_ERROR",
                        "structure_key": "_clock",
                        "status": STATUS_DATA_BLOCKED,
                        "status_reason": "clock_unsynced",
                        "clock_offset_s": self.clock_offset_s,
                        "observed_at": started.isoformat(),
                    }
                )
            finally:
                self.journal.release()
            return {"ok": False, "status": STATUS_DATA_BLOCKED, "reason": "clock_unsynced"}
        try:
            self.journal.acquire()
        except JournalLocked:
            return {"ok": False, "status": "LOCKED"}
        try:
            summary = self._run_locked(started)
        finally:
            self.journal.release()
        summary["cycle_seconds"] = (_aware(now) + self.fetch_delay - started).total_seconds()
        self.cycle_seconds = summary["cycle_seconds"]
        summary["cycle_over_cadence"] = summary["cycle_seconds"] > 45
        return summary

    def _run_locked(self, now: datetime) -> dict[str, Any]:
        state = self.journal.load_state()
        summary: dict[str, Any] = {
            "ok": True,
            "armed_written": 0,
            "resolutions_written": 0,
            "source_blocked": 0,
        }
        for symbol in watcher_universe():
            if symbol in OPTION_ROOT_SYMBOLS:
                continue
            try:
                armed_n, res_n, blocked_n = self._cycle_symbol(symbol, now, state)
            except Exception as exc:  # noqa: BLE001 - observer fail-closed
                self.last_error = f"{symbol}:{type(exc).__name__}"
                self.journal.append(
                    {
                        "record_type": "COLLECTOR_ERROR",
                        "structure_key": f"{symbol}|cycle",
                        "ticker": symbol,
                        "status": STATUS_DATA_BLOCKED,
                        "status_reason": f"source_error:{type(exc).__name__}",
                        "observed_at": now.isoformat(),
                    }
                )
                summary["source_blocked"] += 1
                continue
            summary["armed_written"] += armed_n
            summary["resolutions_written"] += res_n
            summary["source_blocked"] += blocked_n
        return summary

    def _cycle_symbol(self, symbol: str, now: datetime, state: dict[str, Any]) -> tuple[int, int, int]:
        bars = list(self.bar_oracle(symbol, now))
        sessions = sessions_covering(now - timedelta(days=10), now + timedelta(days=1))
        current_session = self.session_lookup(now.astimezone(timezone.utc).date())
        if current_session is None:
            # Weekend/holiday: still arm from completed history and watch Monday.
            for session in reversed(sessions):
                if session.close.astimezone(timezone.utc) <= now:
                    current_session = session
                    break
        if current_session is None:
            return 0, 0, 0
        armed_n = res_n = blocked_n = 0
        series = {
            "30m": bars,
            "1H": rebuild_session_hours(bars, sessions=sessions, cutoff=now),
            "1D": rebuild_session_dailies(bars, sessions=sessions, cutoff=now),
        }
        for timeframe, completed in series.items():
            if timeframe == "1H":
                try:
                    reject_vendor_hour_bars(completed)
                except ValueError:
                    blocked_n += 1
                    self.journal.append(
                        {
                            "record_type": "COLLECTOR_ERROR",
                            "structure_key": f"{symbol}|1H|vendor",
                            "status": STATUS_DATA_BLOCKED,
                            "status_reason": "vendor_hour_bars_unused",
                            "observed_at": now.isoformat(),
                        }
                    )
                    continue
            if len(completed) < 3:
                continue
            # Session of the last completed bar.
            last = completed[-1]
            bar_session = self.session_lookup(last.start_utc.astimezone(timezone.utc).date())
            if bar_session is None:
                continue
            for item in arm_pending_structures(
                completed,
                ticker=symbol,
                timeframe=timeframe,
                now=now,
                session=bar_session,
                lookup=self.session_lookup,
                level_source="public_index_30m" if symbol == "SPX" else "public_regular_30m",
            ):
                armed_n += self._persist_arm(item, now, state)
        for rec in list(state["watching"].values()):
            if rec.ticker != symbol:
                continue
            armed = self._armed_cache.get(rec.structure_key) or self._rehydrate(rec)
            res, blocked = self._resolve(armed, now, state)
            res_n += res
            blocked_n += blocked
        return armed_n, res_n, blocked_n

    def _rehydrate(self, rec: CaptureRecord) -> ArmedStructure:
        return ArmedStructure(
            ticker=rec.ticker,
            timeframe=rec.timeframe,
            pattern=rec.pattern,
            two_back_type=TWO_UP if "2U" in rec.pattern.split(":")[1:2] else TWO_DOWN,
            previous_type=TWO_UP if rec.pattern.endswith("2U") else TWO_DOWN,
            boundary_high=rec.boundary_high,
            boundary_low=rec.boundary_low,
            structure_close=datetime.fromisoformat(rec.structure_close),
            knowable_at=datetime.fromisoformat(rec.knowable_at or rec.structure_close),
            watch_start=datetime.fromisoformat(rec.watch_start),
            watch_until=datetime.fromisoformat(rec.watch_until),
            setup_bar_start=datetime.fromisoformat(rec.structure_close) - timedelta(minutes=30),
            level_source=rec.level_source,
            revision=rec.revision,
        )

    def _persist_arm(self, armed: ArmedStructure, now: datetime, state: dict[str, Any]) -> int:
        self._armed_cache[armed.structure_key] = armed
        current = state["current"].get(armed.structure_key)
        if current is not None and current.status in {
            STATUS_TRIGGERED,
            STATUS_MISSED_LATE,
            STATUS_GAP_THROUGH_OPEN,
            STATUS_EXPIRED,
            STATUS_DATA_BLOCKED,
            STATUS_NO_TRIGGER,
        }:
            return 0
        fp = armed.fingerprint()
        current_levels_differ = current is not None and (
            current.boundary_high != armed.boundary_high or current.boundary_low != armed.boundary_low
        )
        if current_levels_differ and current is not None and current.status == STATUS_WATCHING:
            armed = replace(armed, revision=current.revision + 1)
            self.journal.append(
                {
                    "record_type": "SOURCE_DRIFT",
                    "structure_key": armed.structure_key,
                    "fingerprint": armed.fingerprint(),
                    "status": STATUS_WATCHING,
                    "revision": armed.revision,
                    "boundary_high": armed.boundary_high,
                    "boundary_low": armed.boundary_low,
                    "observed_at": now.isoformat(),
                    "status_reason": "completed_bar_revision",
                }
            )
            watching = record_watching(armed, now=now, clock_offset_s=self.clock_offset_s)
            watching = replace(watching, first_seen_at=current.first_seen_at, revision=armed.revision)
            self.journal.append_record("WATCHING", watching, fingerprint=fp, observed_at=now.isoformat())
            state["watching"][armed.structure_key] = watching
            state["current"][armed.structure_key] = watching
            return 0
        if current is not None and current.status == STATUS_WATCHING:
            return 0
        record = record_watching(armed, now=now, clock_offset_s=self.clock_offset_s)
        self.journal.append_record(
            "WATCHING",
            record,
            fingerprint=armed.fingerprint(),
            observed_at=now.isoformat(),
        )
        state["watching"][armed.structure_key] = record
        state["current"][armed.structure_key] = record
        state["fingerprints"][armed.structure_key] = armed.fingerprint()
        return 1

    def _resolve(self, armed: ArmedStructure, now: datetime, state: dict[str, Any]) -> tuple[int, int]:
        watching = state["current"].get(armed.structure_key)
        if watching is None or watching.status != STATUS_WATCHING:
            return 0, 0
        if now < _aware(armed.watch_start):
            return 0, 0
        detected_at = now + self.fetch_delay
        window_end = min(detected_at, _aware(armed.watch_until))
        high_water = state["high_water"].get(armed.structure_key)
        query_start = _aware(armed.watch_start)
        if high_water:
            hw = datetime.fromisoformat(high_water)
            if hw > query_start:
                query_start = hw
        if armed.ticker == "SPX":
            if self.index_minutes is None:
                self.journal.append(
                    {
                        "record_type": "SOURCE_BLOCKED",
                        "structure_key": armed.structure_key,
                        "status": STATUS_DATA_BLOCKED,
                        "status_reason": "spx_index_minutes_unavailable",
                        "observed_at": detected_at.isoformat(),
                    }
                )
                return 0, 1
            bars = list(self.index_minutes(armed.ticker, _aware(armed.watch_start), window_end))
            result = first_boundary_from_index_minutes(
                bars,
                armed=armed,
                window_start=_aware(armed.watch_start),
                window_end=window_end,
                now=detected_at,
                max_age_seconds=SPX_MAX_BAR_AGE_SECONDS,
            )
            sip_result = None
        else:
            if self.iex_prints is None:
                return 0, 0
            try:
                iex_rows = list(self.iex_prints(armed.ticker, query_start, window_end))
                result = first_boundary_from_prints(
                    iex_rows,
                    armed=armed,
                    window_start=_aware(armed.watch_start),
                    window_end=window_end,
                    )
            except Exception as exc:  # noqa: BLE001
                self.journal.append(
                    {
                        "record_type": "SOURCE_BLOCKED",
                        "structure_key": armed.structure_key,
                        "status": STATUS_DATA_BLOCKED,
                        "status_reason": str(exc),
                        "observed_at": detected_at.isoformat(),
                    }
                )
                if now >= _aware(armed.watch_until):
                    blocked = replace(
                        watching,
                        status=STATUS_DATA_BLOCKED,
                        status_reason=str(exc),
                        persisted_at=_iso(detected_at) or "",
                        detected_at=_iso(detected_at),
                    )
                    self.journal.append_record("RESOLUTION", blocked, observed_at=detected_at.isoformat())
                    state["terminal"][armed.structure_key] = blocked
                    state["current"][armed.structure_key] = blocked
                    return 1, 1
                return 0, 1
            sip_result = None
            if self.sip_prints is not None and (
                result.get("status") in {"NO_BREAK", "PROVEN"}
                and detected_at >= _aware(armed.watch_until) + SIP_RECONCILE_DELAY
                or result.get("status") == "PROVEN"
            ):
                sip_rows = list(self.sip_prints(armed.ticker, _aware(armed.watch_start), window_end))
                sip_result = first_boundary_from_prints(
                    sip_rows,
                    armed=armed,
                    window_start=_aware(armed.watch_start),
                    window_end=min(window_end, _aware(armed.watch_until)),
                )
            pair = classify_iex_sip_pair(
                iex=result,
                sip=sip_result or {"status": "NO_BREAK"},
                watch_elapsed=detected_at >= _aware(armed.watch_until),
            )
            if pair == "iex_no_cross_sip_cross":
                result = dict(sip_result or {})
                result["reason_override"] = "iex_no_cross_sip_cross"
            elif pair == "expired_or_no_trigger":
                expired = replace(
                    watching,
                    status=STATUS_EXPIRED,
                    status_reason="watch_window_elapsed_without_trigger",
                    persisted_at=_iso(detected_at) or "",
                    detected_at=_iso(detected_at),
                    prospective_catch=False,
                )
                self.journal.append_record("RESOLUTION", expired, observed_at=detected_at.isoformat())
                state["terminal"][armed.structure_key] = expired
                state["current"][armed.structure_key] = expired
                state["watching"].pop(armed.structure_key, None)
                return 1, 0
        if result.get("status") == "NO_BREAK":
            if detected_at >= _aware(armed.watch_until):
                expired = replace(
                    watching,
                    status=STATUS_EXPIRED,
                    status_reason="watch_window_elapsed_without_trigger",
                    persisted_at=_iso(detected_at) or "",
                    detected_at=_iso(detected_at),
                    prospective_catch=False,
                )
                self.journal.append_record("RESOLUTION", expired, observed_at=detected_at.isoformat())
                state["terminal"][armed.structure_key] = expired
                state["current"][armed.structure_key] = expired
                state["watching"].pop(armed.structure_key, None)
                return 1, 0
            state["high_water"][armed.structure_key] = window_end.isoformat()
            return 0, 0
        had_pre = watching.status == STATUS_WATCHING
        if result.get("reason_override") == "iex_no_cross_sip_cross" and had_pre:
            # IEX never printed through; SIP did. That is MISSED_LATE, not a catch.
            resolved = replace(
                watching,
                status=STATUS_MISSED_LATE,
                status_reason="iex_no_cross_sip_cross",
                prospective_catch=False,
                capture_late=True,
                detected_at=_iso(detected_at),
                persisted_at=_iso(detected_at) or "",
                sip_crossed_at=_iso(result["print"].ts_utc) if result.get("print") else None,
                trigger_crossed_at=_iso(result["print"].ts_utc) if result.get("print") else None,
                trigger_trade_price=result["print"].price if result.get("print") else None,
                trigger_trade_id=result["print"].trade_id if result.get("print") else None,
                trigger_feed="sip",
                trigger_source="alpaca_sip",
                trigger_resolution="TRADE",
                direction=result.get("direction"),
                setup_type=(
                    armed.long_setup_type()
                    if result.get("direction") == "LONG"
                    else armed.short_setup_type()
                ),
            )
        else:
            resolved = resolve_break(
                watching,
                armed,
                break_result=result,
                now=now,
                detected_at=detected_at,
                sip_result=sip_result,
                had_pre_cross_watching=had_pre,
            )
        self.journal.append_record(
            "RESOLUTION",
            resolved,
            observed_at=detected_at.isoformat(),
            fingerprint=armed.fingerprint(),
        )
        state["terminal"][armed.structure_key] = resolved
        state["current"][armed.structure_key] = resolved
        state["watching"].pop(armed.structure_key, None)
        if self.chain_hook is not None:
            self.chain_hook()
        if self.after_triggered is not None:
            self.after_triggered(resolved)
        return 1, 0

    def submit_broker_order(self, *args: Any, **kwargs: Any) -> None:
        submit_broker_order(*args, **kwargs)

    def consume_risk_budget(self, *args: Any, **kwargs: Any) -> bool:
        return consume_risk_budget(*args, **kwargs)


def module_has_no_execution_imports(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_IMPORTS or alias.name.startswith("execution."):
                    return False
                if "signa" in alias.name.lower() or "gex" in alias.name.lower():
                    return False
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in FORBIDDEN_IMPORTS or module.startswith("execution"):
                return False
            if "signa" in module.lower() or "gex" in module.lower():
                return False
            if module in {"alert_ranker.paper_v1", "alert_ranker.discord", "alert_ranker.scanner_legacy"}:
                return False
    return True


def assert_no_forbidden_imports(path: Path) -> None:
    source = path.read_text(encoding="utf-8")
    if not module_has_no_execution_imports(source):
        raise AssertionError(f"forbidden_import:{path}")


def alpaca_spx_forbidden(symbol: str) -> None:
    if symbol.upper() == "SPX":
        raise RuntimeError("spx_alpaca_unsupported")
