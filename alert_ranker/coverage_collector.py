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
* Every evidence record carries the exact source commit.  Under
  ``require_pinned`` that commit must come from an immutable release manifest
  in the tree being executed, never from a mutable working directory.
* A daily outcome file is bound to the observer run that produced it (run id,
  raw event count, reducer episode count).  A repaired observer dataset
  therefore invalidates the older outcome file instead of coexisting with it.
* Catch-up: without ``--date`` every settled, uncollected session since the
  collection start is a candidate, oldest first, stopping at the first failure.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

from alert_ranker.coverage_episodes import REDUCER_VERSION, reduce_events
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
    "SourceProvenance",
    "source_provenance",
    "resolve_target",
    "settled_sessions",
    "sessions_between",
    "OutcomeBinding",
    "binding_path",
    "reduced_episode_count",
    "write_binding",
    "refuse_v1_database",
    "observer_completion",
    "outcomes_completion",
    "provider_error_lines",
    "daily_outcome_stem",
    "aggregate_stem",
    "append_ledger",
    "read_ledger",
    "outcome_from_row",
    "daily_provenance",
    "load_daily_outcomes",
    "stamp_reducer_aggregate",
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
RELEASE_MANIFEST = "release_manifest.json"
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
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


def settled_sessions(
    now: datetime,
    start: date,
    *,
    session_for: SessionLookup = nyse_session_for,
    settle: timedelta = SETTLE_AFTER_CLOSE,
) -> list[Session]:
    """Every session from ``start`` whose close + ``settle`` has passed, oldest first."""
    if now.tzinfo is None:
        raise CollectorError("now_naive", "now must be timezone-aware")
    return [s for s in sessions_between(start, _exchange_date(now), session_for=session_for) if now >= s.close + settle]



# --------------------------------------------------------------------------- #
# provenance
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SourceProvenance:
    """Exactly which code produced a record."""

    sha: str
    provenance: str  # release_manifest | working_tree
    root: str
    dirty: bool = False
    manifest_fingerprint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def source_provenance(root: Path, *, require_pinned: bool = False) -> SourceProvenance:
    """The commit that ``root`` was built from.

    Pinned: ``<root>/release_manifest.json`` (written into an immutable release
    tree at build time) names the commit, and the release directory itself is
    named after that commit.  Unpinned: ``git rev-parse HEAD`` of a working
    tree, labelled as such with its dirty flag — allowed for local runs only;
    ``require_pinned`` (the systemd unit) fails closed on it.
    """
    root = Path(root).resolve()
    manifest = root / RELEASE_MANIFEST
    if manifest.exists():
        try:
            payload = json.loads(manifest.read_text())
        except ValueError as exc:
            raise CollectorError("release_manifest_unreadable", f"{manifest}: {exc}") from exc
        repo = payload.get("repo") if isinstance(payload, dict) else None
        sha = str((repo or {}).get("commit") or "")
        if not _SHA40.match(sha):
            raise CollectorError("release_manifest_no_commit", str(manifest))
        if bool((repo or {}).get("dirty")):
            raise CollectorError("release_manifest_dirty", f"{manifest} was built from a dirty tree")
        if require_pinned and root.name != sha:
            raise CollectorError("release_dir_not_pinned", f"{root} is not named after commit {sha}")
        return SourceProvenance(sha=sha, provenance="release_manifest", root=str(root), dirty=False, manifest_fingerprint=payload.get("fingerprint_sha256"))
    if require_pinned:
        raise CollectorError("provenance_unpinned", f"no {RELEASE_MANIFEST} in {root}; refusing to run from a working tree")
    try:
        sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, text=True, capture_output=True, check=True).stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, text=True, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CollectorError("provenance_unavailable", f"{root}: {exc}") from exc
    if not _SHA40.match(sha):
        raise CollectorError("provenance_unavailable", f"unexpected HEAD {sha!r}")
    return SourceProvenance(sha=sha, provenance="working_tree", root=str(root), dirty=bool(status.strip()))


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


@dataclass(frozen=True)
class OutcomeBinding:
    """Ties one daily outcome file to the observer state that produced it."""

    session_date: str
    observer_version: str
    observer_run_id: int
    observer_ran_at: str
    raw_events: int
    reducer_version: str
    reducer_episodes: int
    outcome_version: str
    outcome_episodes: int
    source_sha: str
    source_provenance: str
    manifest_fingerprint: str | None
    collector_version: str
    written_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def binding_path(daily_dir: Path, session_date: date) -> Path:
    return daily_outcome_stem(daily_dir, session_date).with_suffix(".binding.json")


def reduced_episode_count(sqlite_path: Path, session_date: date) -> int:
    """Episodes the ep-v0.1 reducer produces from the stored events of one session (local, no provider)."""
    if not sqlite_path.exists():
        return 0
    conn = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
    try:
        if "coverage_events" not in _tables(conn):
            return 0
        rows = conn.execute(
            "SELECT row_json FROM coverage_events WHERE observer_version=? AND session_date=? ORDER BY symbol, bar_start",
            (OBSERVER_VERSION, session_date.isoformat()),
        ).fetchall()
    finally:
        conn.close()
    return len(reduce_events([json.loads(r[0]) for r in rows]))


def write_binding(daily_dir: Path, session_date: date, coverage: CoverageCheck, reducer_episodes: int, outcome_episodes: int, source: SourceProvenance) -> OutcomeBinding:
    if coverage.run_id is None or coverage.ran_at is None:
        raise CollectorError("binding_without_observer_run", session_date.isoformat())
    binding = OutcomeBinding(
        session_date=session_date.isoformat(), observer_version=OBSERVER_VERSION, observer_run_id=coverage.run_id, observer_ran_at=coverage.ran_at,
        raw_events=coverage.events, reducer_version=REDUCER_VERSION, reducer_episodes=reducer_episodes, outcome_version=OUTCOME_VERSION,
        outcome_episodes=outcome_episodes, source_sha=source.sha, source_provenance=source.provenance, manifest_fingerprint=source.manifest_fingerprint,
        collector_version=COLLECTOR_VERSION, written_at=datetime.now(timezone.utc).isoformat(),
    )
    binding_path(daily_dir, session_date).write_text(json.dumps(binding.to_dict(), indent=1, sort_keys=True))
    return binding


def outcomes_completion(daily_dir: Path, session_date: date, coverage: CoverageCheck | None = None, reducer_episodes: int | None = None) -> OutcomesCheck:
    """Is the one-session outcome study for ``session_date`` present, untainted and bound to the current observer state?

    With ``coverage`` (the current observer check) the binding sidecar must
    name the same observer run id and raw event count, the daily summary's
    ``raw_events`` must equal it, and with ``reducer_episodes`` the stored
    episode count must equal what the reducer produces from the stored events
    now.  Any mismatch means the observer dataset moved after the outcomes
    were measured, and the file is treated as incomplete.
    """
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

    if coverage is not None:
        bind_file = binding_path(daily_dir, session_date)
        if not bind_file.exists():
            check.problems.append("no_binding")
        else:
            try:
                bound = json.loads(bind_file.read_text())
            except ValueError:
                bound = None
            if not isinstance(bound, dict):
                check.problems.append("binding_unparseable")
            else:
                if bound.get("observer_run_id") != coverage.run_id:
                    check.problems.append(f"binding_observer_run:{bound.get('observer_run_id')}!={coverage.run_id}")
                if bound.get("raw_events") != coverage.events:
                    check.problems.append(f"binding_raw_events:{bound.get('raw_events')}!={coverage.events}")
                if bound.get("observer_version") != OBSERVER_VERSION or bound.get("outcome_version") != OUTCOME_VERSION:
                    check.problems.append("binding_version")
                if bound.get("outcome_episodes") != check.episodes:
                    check.problems.append(f"binding_outcome_episodes:{bound.get('outcome_episodes')}!={check.episodes}")
                if reducer_episodes is not None and bound.get("reducer_episodes") != reducer_episodes:
                    check.problems.append(f"binding_reducer_episodes:{bound.get('reducer_episodes')}!={reducer_episodes}")
        if summary.get("raw_events") != coverage.events:
            check.problems.append(f"daily_raw_events:{summary.get('raw_events')}!={coverage.events}")
    if reducer_episodes is not None and check.episodes != reducer_episodes:
        check.problems.append(f"episodes_vs_reducer:{check.episodes}!={reducer_episodes}")
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


def base_record(session: Session | None, status: str, source: SourceProvenance | None = None, **extra: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "collector_id": COLLECTOR_ID,
        "collector_version": COLLECTOR_VERSION,
        "source": source.to_dict() if source else None,
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


_BINDING_PROVENANCE_FIELDS = ("session_date", "observer_version", "observer_run_id", "observer_ran_at", "raw_events", "reducer_version", "reducer_episodes", "outcome_version", "outcome_episodes", "source_sha", "source_provenance", "manifest_fingerprint", "collector_version", "written_at")


def daily_provenance(daily_dir: Path, session_date: date) -> dict[str, Any]:
    """The immutable provenance of one stored daily outcome file, read from its binding sidecar.

    Fails closed when the sidecar is missing, unparseable, for another session,
    or lacks a well-formed 40-hex ``source_sha`` — a daily file with unknown
    origin is never folded into an aggregate.
    """
    day = session_date.isoformat()
    path = binding_path(daily_dir, session_date)
    if not path.exists():
        raise CollectorError("daily_provenance_missing", f"{day}: no {path.name}")
    try:
        bound = json.loads(path.read_text())
    except ValueError as exc:
        raise CollectorError("daily_provenance_invalid", f"{day}: {path.name} unparseable: {exc}") from exc
    if not isinstance(bound, dict):
        raise CollectorError("daily_provenance_invalid", f"{day}: {path.name} is not an object")
    missing = [k for k in _BINDING_PROVENANCE_FIELDS if k not in bound]
    if missing:
        raise CollectorError("daily_provenance_invalid", f"{day}: {path.name} missing {missing}")
    if bound.get("session_date") != day:
        raise CollectorError("daily_provenance_invalid", f"{day}: {path.name} is for {bound.get('session_date')}")
    if not isinstance(bound.get("source_sha"), str) or not _SHA40.match(bound["source_sha"]):
        raise CollectorError("daily_provenance_invalid", f"{day}: source_sha {bound.get('source_sha')!r} is not a commit")
    return {k: bound[k] for k in _BINDING_PROVENANCE_FIELDS}


def load_daily_outcomes(daily_dir: Path, sessions: Sequence[Session]) -> tuple[list[EpisodeOutcome], list[str], list[str], dict[str, str], dict[str, dict[str, Any]]]:
    """Load every stored one-session outcome file for ``sessions``.

    Returns ``(outcomes, present_dates, missing_dates, provider_errors,
    provenance_by_session)``.  A present file is used as stored and MUST carry
    a valid binding sidecar (see :func:`daily_provenance`); a session without
    a file is reported, not fabricated.
    """
    outcomes: list[EpisodeOutcome] = []
    present: list[str] = []
    missing: list[str] = []
    errors: dict[str, str] = {}
    provenance: dict[str, dict[str, Any]] = {}
    for session in sessions:
        json_path = daily_outcome_stem(daily_dir, session.date).with_suffix(".json")
        if not json_path.exists():
            missing.append(session.date.isoformat())
            continue
        provenance[session.date.isoformat()] = daily_provenance(daily_dir, session.date)
        payload = json.loads(json_path.read_text())
        for key, value in (payload.get("summary", {}).get("provider_errors") or {}).items():
            errors[str(key)] = str(value)
        for row in payload.get("episodes", []):
            outcomes.append(outcome_from_row(row))
        present.append(session.date.isoformat())
    return outcomes, present, missing, errors, provenance


def stamp_reducer_aggregate(path: Path, source: SourceProvenance, date_from: date, date_to: date) -> dict[str, Any]:
    """Make the reducer's ``episodes_<from>_<to>.json`` self-describing.

    The ep-v0.1 script writes ``{summary, answers, episodes}`` and nothing
    about who ran it; the collector adds a top-level ``provenance`` block with
    the exact source commit, how it was established, the release manifest
    fingerprint and every lane version, so the file stands on its own without
    the ledger or the filename.
    """
    try:
        payload = json.loads(path.read_text())
    except ValueError as exc:
        raise CollectorError("aggregate_unparseable", f"{path}: {exc}") from exc
    if not isinstance(payload, dict) or "episodes" not in payload:
        raise CollectorError("aggregate_malformed", str(path))
    block = {
        "source": source.to_dict(),
        "collector_id": COLLECTOR_ID,
        "collector_version": COLLECTOR_VERSION,
        "reducer_version": REDUCER_VERSION,
        "observer_version": OBSERVER_VERSION,
        "date_from": date_from.isoformat(),
        "date_to": date_to.isoformat(),
        "episodes": len(payload.get("episodes") or []),
        "stamped_at": datetime.now(timezone.utc).isoformat(),
    }
    payload["provenance"] = block
    path.write_text(json.dumps(payload, indent=1, sort_keys=True))
    return block
