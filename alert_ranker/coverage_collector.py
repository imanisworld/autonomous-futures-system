"""After-close coverage collector — pure rules for the daily oneshot.

Identity ``OPTIONS_COVERAGE_COLLECTOR / col-v0.1``.  This module decides *which*
session to collect, whether that session's evidence is already complete, and
whether a finished step is acceptable.  It fetches nothing and runs nothing;
:mod:`scripts.options_coverage_collect` wires it to the three existing
read-only scripts (observer ``cov-v0.1``, reducer ``ep-v0.1``, outcome study
``out-v0.1``) as subprocesses.

Fail-closed contract
--------------------
* A session is collected only after its authoritative close plus a settle
  buffer (the observer prices first sight from 5Min bars up to close + 26 min).
* Holidays and weekends are skips, never failures.  Early closes settle
  earlier and are otherwise identical.
* Symbol coverage must be complete: every universe symbol has a row, every
  unobservable symbol is on the explicit allow-list, SPY and QQQ (the
  alignment context) are observable, and the session produced events.
* Any provider error, non-zero exit, tainted output or missing output fails
  the run; nothing is guessed around.
* Evidence is append-only: the observer sqlite gains rows, the daily outcome
  files are written once per session, the ledger is a JSONL that is only ever
  appended to.  A tainted daily file is moved aside, never deleted.
* The V1 scanner database is refused by name and by schema.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

from alert_ranker.coverage_episodes import REDUCER_VERSION
from alert_ranker.coverage_observer import OBSERVER_VERSION
from alert_ranker.coverage_outcomes import OUTCOME_VERSION, EpisodeOutcome, PathView
from alert_ranker.session_calendar import EXCHANGE_TIMEZONE, Session, nyse_session_for

__all__ = [
    "COLLECTOR_ID",
    "COLLECTOR_VERSION",
    "SETTLE_AFTER_CLOSE",
    "DEFAULT_ALLOW_UNOBSERVABLE",
    "DEFAULT_COLLECTION_START",
    "INDEX_SYMBOLS",
    "STATUS_DONE",
    "STATUS_ALREADY_COLLECTED",
    "STATUS_SKIPPED",
    "STATUS_FAILED",
    "CollectorError",
    "CoverageCheck",
    "OutcomesCheck",
    "resolve_target",
    "sessions_between",
    "refuse_v1_database",
    "observer_completion",
    "outcomes_completion",
    "provider_error_lines",
    "daily_outcome_stem",
    "aggregate_stem",
    "append_ledger",
    "read_ledger",
    "outcome_from_row",
    "load_daily_outcomes",
]

COLLECTOR_ID = "OPTIONS_COVERAGE_COLLECTOR"
COLLECTOR_VERSION = "col-v0.1"

# The observer prices first sight from 5Min bars that run to close + 960 s +
# 10 min; the consolidated feed needs a little longer to finalise them.
SETTLE_AFTER_CLOSE = timedelta(minutes=30)
# Watchlist tickers with no equity bars (measured 2026-09-15): SQ, VIX.
DEFAULT_ALLOW_UNOBSERVABLE: tuple[str, ...] = ("SQ", "VIX")
# First session with observer evidence (cov-v0.1 collection started 2026-09-09).
DEFAULT_COLLECTION_START = date(2026, 9, 9)
INDEX_SYMBOLS = ("SPY", "QQQ")
LATEST_LOOKBACK_DAYS = 10
V1_DATABASE_NAMES = ("options_scanner.sqlite",)
V1_TABLES = ("scans", "options_shadow_journal", "alerts")

STATUS_DONE = "DONE"
STATUS_ALREADY_COLLECTED = "ALREADY_COLLECTED"
STATUS_SKIPPED = "SKIPPED"
STATUS_FAILED = "FAILED"

SessionLookup = Callable[[date], Session | None]


class CollectorError(RuntimeError):
    """A fail-closed stop. ``reason`` is a stable token for the ledger."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


# --------------------------------------------------------------------------- #
# which session
# --------------------------------------------------------------------------- #


def _exchange_date(now: datetime) -> date:
    return now.astimezone(ZoneInfo(EXCHANGE_TIMEZONE)).date()


def resolve_target(
    now: datetime,
    explicit: date | None = None,
    *,
    session_for: SessionLookup = nyse_session_for,
    settle: timedelta = SETTLE_AFTER_CLOSE,
) -> Session | None:
    """The session to collect, ``None`` when the explicit day has no session.

    With ``explicit`` the day must be a session whose close + ``settle`` has
    passed; an unsettled session is a fail-closed error, never a fallback to
    an earlier day.  Without it, the newest session that has settled as of
    ``now`` is chosen (exchange-local day, walking back over holidays and
    weekends) — this is what the timer relies on, including a persistent
    timer catching up after a reboot.
    """
    if now.tzinfo is None:
        raise CollectorError("now_naive", "now must be timezone-aware")
    if explicit is not None:
        session = session_for(explicit)
        if session is None:
            return None
        if now < session.close + settle:
            raise CollectorError(
                "session_not_settled",
                f"{explicit} closes {session.close.isoformat()}; settled at {(session.close + settle).isoformat()}",
            )
        return session
    cursor = _exchange_date(now)
    for _ in range(LATEST_LOOKBACK_DAYS):
        session = session_for(cursor)
        if session is not None and now >= session.close + settle:
            return session
        cursor -= timedelta(days=1)
    raise CollectorError("no_settled_session", f"no session settled within {LATEST_LOOKBACK_DAYS} days of {now.isoformat()}")


def sessions_between(start: date, end: date, *, session_for: SessionLookup = nyse_session_for) -> list[Session]:
    """Every calendar session in ``[start, end]``, in order."""
    out: list[Session] = []
    cursor = start
    while cursor <= end:
        session = session_for(cursor)
        if session is not None:
            out.append(session)
        cursor += timedelta(days=1)
    return out


# --------------------------------------------------------------------------- #
# guards
# --------------------------------------------------------------------------- #


def refuse_v1_database(path: Path) -> None:
    """Refuse, by name and by schema, to treat the V1 scanner database as ours."""
    if path.name in V1_DATABASE_NAMES:
        raise CollectorError("v1_database_refused", f"{path} is the V1 scanner database by name")
    if not path.exists():
        return
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise CollectorError("sqlite_unreadable", f"{path}: {exc}") from exc
    try:
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()
    hits = sorted(names.intersection(V1_TABLES))
    if hits:
        raise CollectorError("v1_database_refused", f"{path} holds V1 tables {hits}")


def provider_error_lines(text: str) -> list[str]:
    """Observer stdout lines that report a provider failure."""
    return [line.strip() for line in text.splitlines() if "provider error:" in line]


# --------------------------------------------------------------------------- #
# completion checks
# --------------------------------------------------------------------------- #


@dataclass
class CoverageCheck:
    ok: bool
    requested: int = 0
    observable: int = 0
    events: int = 0
    run_id: int | None = None
    ran_at: str | None = None
    missing_rows: list[str] = field(default_factory=list)
    unobservable: dict[str, str] = field(default_factory=dict)
    disallowed: list[str] = field(default_factory=list)
    index_context: dict[str, bool] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "requested": self.requested,
            "observable": self.observable,
            "events": self.events,
            "run_id": self.run_id,
            "ran_at": self.ran_at,
            "missing_rows": list(self.missing_rows),
            "unobservable": dict(self.unobservable),
            "disallowed": list(self.disallowed),
            "index_context": dict(self.index_context),
            "problems": list(self.problems),
        }


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def observer_completion(
    sqlite_path: Path,
    session_date: date,
    universe: Sequence[str],
    allow_unobservable: Iterable[str] = DEFAULT_ALLOW_UNOBSERVABLE,
) -> CoverageCheck:
    """Is the observer's evidence for ``session_date`` complete and acceptable?

    Complete means: a ``coverage_runs`` row for this observer version, a
    ``coverage_symbols`` row for every universe symbol, no unobservable symbol
    outside the allow-list, SPY and QQQ observable, and at least one event.
    """
    day = session_date.isoformat()
    wanted = list(dict.fromkeys(s.strip().upper() for s in universe if s.strip()))
    allowed = {s.strip().upper() for s in allow_unobservable if s.strip()}
    check = CoverageCheck(ok=False, requested=len(wanted))
    if not sqlite_path.exists():
        check.problems.append("no_observer_sqlite")
        return check
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        tables = _tables(conn)
        if not {"coverage_runs", "coverage_symbols", "coverage_events"}.issubset(tables):
            check.problems.append("no_observer_tables")
            return check
        run = conn.execute(
            "SELECT id, ran_at, funnel_json FROM coverage_runs WHERE observer_version=? AND session_date=? ORDER BY id DESC LIMIT 1",
            (OBSERVER_VERSION, day),
        ).fetchone()
        if run is None:
            check.problems.append("no_observer_run")
            return check
        check.run_id, check.ran_at = int(run[0]), str(run[1])
        try:
            funnel = json.loads(run[2] or "{}")
        except ValueError:
            funnel = {}
        context = funnel.get("index_context") if isinstance(funnel, dict) else None
        check.index_context = {s: bool((context or {}).get(s, False)) for s in INDEX_SYMBOLS}
        rows = conn.execute(
            "SELECT symbol, observable, reason FROM coverage_symbols WHERE observer_version=? AND session_date=?",
            (OBSERVER_VERSION, day),
        ).fetchall()
        check.events = int(
            conn.execute(
                "SELECT COUNT(*) FROM coverage_events WHERE observer_version=? AND session_date=?",
                (OBSERVER_VERSION, day),
            ).fetchone()[0]
        )
    finally:
        conn.close()

    seen = {str(r[0]).upper(): (int(r[1]), str(r[2])) for r in rows}
    check.missing_rows = [s for s in wanted if s not in seen]
    check.unobservable = {s: seen[s][1] for s in wanted if s in seen and not seen[s][0]}
    check.observable = sum(1 for s in wanted if s in seen and seen[s][0])
    check.disallowed = sorted(s for s in check.unobservable if s not in allowed)

    if check.missing_rows:
        check.problems.append(f"missing_symbol_rows:{len(check.missing_rows)}")
    if check.disallowed:
        check.problems.append(f"unobservable_not_allowed:{','.join(check.disallowed)}")
    bad_index = [s for s, ok in check.index_context.items() if not ok]
    if bad_index:
        check.problems.append(f"index_context_unobservable:{','.join(bad_index)}")
    if check.events <= 0:
        check.problems.append("no_events")
    check.ok = not check.problems
    return check


@dataclass
class OutcomesCheck:
    ok: bool
    episodes: int = 0
    clean: int = 0
    provider_errors: int = 0
    problems: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "episodes": self.episodes, "clean": self.clean, "provider_errors": self.provider_errors, "problems": list(self.problems)}


def daily_outcome_stem(daily_dir: Path, session_date: date) -> Path:
    """``<daily>/outcomes_<D>_<D>`` — the outcome script's own naming for a one-session range."""
    day = session_date.isoformat()
    return daily_dir / f"outcomes_{day}_{day}"


def aggregate_stem(aggregate_dir: Path, date_from: date, date_to: date) -> Path:
    return aggregate_dir / f"outcomes_{date_from.isoformat()}_{date_to.isoformat()}"


def outcomes_completion(daily_dir: Path, session_date: date) -> OutcomesCheck:
    """Is the one-session outcome study for ``session_date`` present and untainted?"""
    stem = daily_outcome_stem(daily_dir, session_date)
    check = OutcomesCheck(ok=False)
    json_path = stem.with_suffix(".json")
    if not json_path.exists():
        check.problems.append("no_daily_json")
        return check
    try:
        payload = json.loads(json_path.read_text())
    except ValueError:
        check.problems.append("daily_json_unparseable")
        return check
    summary = payload.get("summary") if isinstance(payload, dict) else None
    episodes = payload.get("episodes") if isinstance(payload, dict) else None
    if not isinstance(summary, dict) or not isinstance(episodes, list):
        check.problems.append("daily_json_malformed")
        return check
    day = session_date.isoformat()
    if summary.get("date_from") != day or summary.get("date_to") != day:
        check.problems.append(f"daily_json_wrong_range:{summary.get('date_from')}..{summary.get('date_to')}")
    if summary.get("outcome_version", OUTCOME_VERSION) != OUTCOME_VERSION:
        check.problems.append(f"daily_json_version:{summary.get('outcome_version')}")
    errors = summary.get("provider_errors") or {}
    check.provider_errors = len(errors)
    if errors:
        check.problems.append(f"provider_errors:{len(errors)}")
    check.episodes = len(episodes)
    if check.episodes <= 0:
        check.problems.append("no_episodes")
    total = summary.get("total") or {}
    check.clean = int(total.get("clean_episodes", 0) or 0)
    for suffix in (".csv", ".md"):
        sibling = stem.with_suffix(suffix)
        if not sibling.exists() or sibling.stat().st_size == 0:
            check.problems.append(f"missing_output:{sibling.name}")
    check.ok = not check.problems
    return check


# --------------------------------------------------------------------------- #
# ledger (append-only JSONL)
# --------------------------------------------------------------------------- #


def append_ledger(path: Path, record: dict[str, Any]) -> None:
    """Append one JSON line. Never truncates, never rewrites earlier lines."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, default=str)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def read_ledger(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append({"malformed": line})
    return out


def base_record(session: Session | None, status: str, **extra: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "collector_id": COLLECTOR_ID,
        "collector_version": COLLECTOR_VERSION,
        "observer_version": OBSERVER_VERSION,
        "reducer_version": REDUCER_VERSION,
        "outcome_version": OUTCOME_VERSION,
        "session_date": session.date.isoformat() if session else None,
        "session_close": session.close.isoformat() if session else None,
        "early_close": bool(session.is_early_close) if session else None,
        "status": status,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    record.update(extra)
    return record


# --------------------------------------------------------------------------- #
# local aggregation of stored daily evidence
# --------------------------------------------------------------------------- #


_OUTCOME_FIELDS = {f.name for f in fields(EpisodeOutcome)}
_VIEW_FIELDS = {f.name for f in fields(PathView)}


def outcome_from_row(row: dict[str, Any]) -> EpisodeOutcome:
    """Inverse of :meth:`EpisodeOutcome.to_row` (drops the derived ``clean``)."""
    data = {k: v for k, v in row.items() if k in _OUTCOME_FIELDS and k != "views"}
    missing = _OUTCOME_FIELDS - set(data) - {"views"}
    if missing:
        raise CollectorError("daily_row_malformed", f"missing fields {sorted(missing)}")
    views = [PathView(**{k: v for k, v in item.items() if k in _VIEW_FIELDS}) for item in row.get("views") or []]
    return EpisodeOutcome(views=views, **data)


def load_daily_outcomes(daily_dir: Path, sessions: Sequence[Session]) -> tuple[list[EpisodeOutcome], list[str], list[str], dict[str, str]]:
    """Load every stored one-session outcome file for ``sessions``.

    Returns ``(outcomes, present_dates, missing_dates, provider_errors)``.
    A present file is used as stored; a session without a file is reported,
    not fabricated.
    """
    outcomes: list[EpisodeOutcome] = []
    present: list[str] = []
    missing: list[str] = []
    errors: dict[str, str] = {}
    for session in sessions:
        json_path = daily_outcome_stem(daily_dir, session.date).with_suffix(".json")
        if not json_path.exists():
            missing.append(session.date.isoformat())
            continue
        payload = json.loads(json_path.read_text())
        for key, value in (payload.get("summary", {}).get("provider_errors") or {}).items():
            errors[str(key)] = str(value)
        for row in payload.get("episodes", []):
            outcomes.append(outcome_from_row(row))
        present.append(session.date.isoformat())
    return outcomes, present, missing, errors
