"""After-close options coverage collector — the daily oneshot.

    python scripts/options_coverage_collect.py                       # every settled, uncollected session (oldest first)
    python scripts/options_coverage_collect.py --date 2026-09-16     # one explicit session
    python scripts/options_coverage_collect.py --plan                # resolve + report, run nothing

For each candidate session the three read-only coverage scripts run as
subprocesses and are verified:

    1. observer   scripts/options_coverage_observer.py --date D      (provider fetch → observer sqlite)
    2. outcomes   scripts/options_coverage_outcomes.py --from D --to D --out <data>/daily
                  + a binding sidecar tying the daily file to the observer run/event/episode counts
    then, once:
    3. aggregate  scripts/options_coverage_episodes.py --from F --to D --json <data>/aggregate/…
                  + a LOCAL roll-up of every stored one-session outcome file (no provider call)

Every ledger line carries the exact source commit.  Under ``--require-pinned``
(the systemd unit) that commit must come from the release manifest of the
immutable tree being executed.  Idempotent: a session whose observer evidence
and bound daily outcome file are complete is not fetched again.  Append-only:
nothing under ``<data>`` is deleted or rewritten except that a tainted daily
file is moved aside before a retry.  Catch-up: after downtime every settled
uncollected session since the collection start is processed oldest → newest,
stopping at the first failure.

Deliberately absent: git, systemctl, the V1 scanner database, Discord/alerts,
any promotion or policy logic.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.config import resolve_alpaca_credentials  # noqa: E402
from alert_ranker.coverage_collector import (  # noqa: E402
    COLLECTOR_ID,
    COLLECTOR_VERSION,
    DEFAULT_ALLOW_UNOBSERVABLE,
    DEFAULT_COLLECTION_START,
    STATUS_ALREADY_COLLECTED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_SKIPPED,
    CollectorError,
    SourceProvenance,
    aggregate_stem,
    append_ledger,
    base_record,
    daily_outcome_stem,
    load_daily_outcomes,
    observer_completion,
    outcomes_completion,
    provider_error_lines,
    reduced_episode_count,
    refuse_v1_database,
    resolve_target,
    sessions_between,
    settled_sessions,
    source_provenance,
    stamp_reducer_aggregate,
    write_binding,
)
from alert_ranker.coverage_outcomes import summarize_outcomes  # noqa: E402
from alert_ranker.session_calendar import AlpacaSessionCalendar, Session, SessionCalendarError  # noqa: E402
from scripts.options_coverage_observer import DEFAULT_SQLITE, DEFAULT_UNIVERSE, load_universe  # noqa: E402
from scripts.options_coverage_outcomes import write_markdown  # noqa: E402

DEFAULT_DATA_DIR = ROOT / "logs" / "coverage_collector"
OBSERVER_SCRIPT = ROOT / "scripts" / "options_coverage_observer.py"
EPISODES_SCRIPT = ROOT / "scripts" / "options_coverage_episodes.py"
OUTCOMES_SCRIPT = ROOT / "scripts" / "options_coverage_outcomes.py"
DEFAULT_MAX_SESSIONS = 10

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2

Runner = Callable[[Sequence[str], Path], int]


def subprocess_runner(cmd: Sequence[str], log_path: Path) -> int:
    """Run ``cmd`` with stdout+stderr teed into a fresh per-attempt log file."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        handle.write(f"$ {' '.join(cmd)}\n")
        handle.flush()
        proc = subprocess.run(list(cmd), stdout=handle, stderr=subprocess.STDOUT, cwd=str(ROOT), check=False)
    return proc.returncode


class Collector:
    def __init__(
        self,
        *,
        data_dir: Path,
        sqlite_path: Path,
        universe_path: Path,
        collection_start: date,
        allow_unobservable: Sequence[str],
        pause_seconds: float,
        source: SourceProvenance,
        runner: Runner = subprocess_runner,
        python: str = sys.executable,
        calendar_check: bool = True,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        sleep: Callable[[float], None] = time.sleep,
        now: datetime | None = None,
    ) -> None:
        self.data_dir = data_dir
        self.sqlite_path = sqlite_path
        self.universe_path = universe_path
        self.collection_start = collection_start
        self.allow_unobservable = tuple(allow_unobservable)
        self.pause_seconds = pause_seconds
        self.source = source
        self.runner = runner
        self.python = python
        self.calendar_check = calendar_check
        self.max_sessions = max_sessions
        self.sleep = sleep
        self.now = now or datetime.now(timezone.utc)
        self.daily_dir = data_dir / "daily"
        self.aggregate_dir = data_dir / "aggregate"
        self.runs_dir = data_dir / "runs"
        self.ledger_path = data_dir / "ledger.jsonl"
        self._reset_session_state()

    def _reset_session_state(self) -> None:
        self.steps: dict[str, str] = {}
        self.outputs: list[str] = []
        self.logs: list[str] = []

    # ------------------------------------------------------------------ #
    # commands (pure, so tests can assert what would run)
    # ------------------------------------------------------------------ #

    def observer_cmd(self, session: Session) -> list[str]:
        return [
            self.python, str(OBSERVER_SCRIPT),
            "--date", session.date.isoformat(),
            "--sqlite", str(self.sqlite_path),
            "--universe", str(self.universe_path),
        ]

    def outcomes_cmd(self, session: Session) -> list[str]:
        day = session.date.isoformat()
        return [
            self.python, str(OUTCOMES_SCRIPT),
            "--from", day, "--to", day,
            "--sqlite", str(self.sqlite_path),
            "--out", str(self.daily_dir),
            "--pause-seconds", "5",
        ]

    def episodes_cmd(self, session: Session) -> list[str]:
        return [
            self.python, str(EPISODES_SCRIPT),
            "--from", self.collection_start.isoformat(), "--to", session.date.isoformat(),
            "--sqlite", str(self.sqlite_path),
            "--json", str(self.aggregate_dir / f"episodes_{self.collection_start.isoformat()}_{session.date.isoformat()}.json"),
        ]

    # ------------------------------------------------------------------ #
    # completeness
    # ------------------------------------------------------------------ #

    def status_of(self, session: Session, universe: Sequence[str]) -> tuple[Any, Any, int]:
        """(observer check, outcomes check bound to it, reducer episode count) for one session."""
        coverage = observer_completion(self.sqlite_path, session.date, universe, self.allow_unobservable)
        reducer = reduced_episode_count(self.sqlite_path, session.date) if coverage.ok else 0
        outcomes = outcomes_completion(self.daily_dir, session.date, coverage if coverage.ok else None, reducer if coverage.ok else None)
        return coverage, outcomes, reducer

    def candidates(self, universe: Sequence[str]) -> tuple[list[Session], list[Session]]:
        """(settled sessions since the collection start, those not yet complete) — oldest first."""
        settled = settled_sessions(self.now, self.collection_start)
        pending = [s for s in settled if not (lambda c, o, _r: c.ok and o.ok)(*self.status_of(s, universe))]
        return settled, pending

    # ------------------------------------------------------------------ #
    # steps
    # ------------------------------------------------------------------ #

    def _log_path(self, session: Session, step: str) -> Path:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.runs_dir / session.date.isoformat() / f"{step}.{stamp}.log"
        self.logs.append(str(path))
        return path

    def _run(self, cmd: Sequence[str], log_path: Path, step: str) -> str:
        code = self.runner(cmd, log_path)
        text = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        if code != 0:
            raise CollectorError(f"{step}_exit_{code}", f"see {log_path}")
        return text

    def authoritative_session(self, session: Session) -> Session | None:
        """Cross-check the static calendar against the broker's read-only calendar.

        ``None`` means the broker reports no session (unscheduled closure) and
        the day is skipped.  A close that differs from the static calendar is
        a fail-closed error because the observer windows bars on the static
        close.  Skipped entirely without credentials (the observer would fail
        closed on them anyway) or with ``--no-calendar-check``.
        """
        if not self.calendar_check:
            self.steps["calendar"] = "disabled"
            return session
        api_key, secret_key = resolve_alpaca_credentials()
        if not api_key or not secret_key:
            self.steps["calendar"] = "no_credentials"
            return session
        import asyncio

        base_url = os.environ.get("ALPACA_ENDPOINT", "https://paper-api.alpaca.markets")
        calendar = AlpacaSessionCalendar(base_url=base_url, api_key=api_key, secret_key=secret_key)
        try:
            broker = asyncio.run(calendar.session_for(session.date))
        except SessionCalendarError as exc:
            raise CollectorError("calendar_unavailable", f"{exc.reason}: {exc.detail}") from exc
        if broker is None:
            self.steps["calendar"] = "broker_reports_closed"
            return None
        if broker.close != session.close:
            raise CollectorError("calendar_mismatch", f"static close {session.close.isoformat()} vs broker {broker.close.isoformat()}")
        self.steps["calendar"] = "confirmed"
        return session

    def collect_observer(self, session: Session, universe: Sequence[str]) -> Any:
        before = observer_completion(self.sqlite_path, session.date, universe, self.allow_unobservable)
        if before.ok:
            self.steps["observer"] = "already_complete"
            return before
        text = self._run(self.observer_cmd(session), self._log_path(session, "observer"), "observer")
        errors = provider_error_lines(text)
        if errors:
            raise CollectorError("observer_provider_errors", "; ".join(errors)[:400])
        after = observer_completion(self.sqlite_path, session.date, universe, self.allow_unobservable)
        if not after.ok:
            raise CollectorError("observer_coverage_incomplete", "; ".join(after.problems))
        self.steps["observer"] = "ran"
        return after

    def collect_outcomes(self, session: Session, coverage: Any) -> Any:
        reducer = reduced_episode_count(self.sqlite_path, session.date)
        before = outcomes_completion(self.daily_dir, session.date, coverage, reducer)
        if before.ok:
            self.steps["outcomes"] = "already_complete"
            return before
        stem = daily_outcome_stem(self.daily_dir, session.date)
        if stem.with_suffix(".json").exists():
            # Tainted, partial or bound to an earlier observer run: keep it (append-only), move it out of the way.
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            for suffix in (".json", ".csv", ".md", ".binding.json"):
                path = stem.with_suffix(suffix)
                if path.exists():
                    path.rename(path.with_name(f"{path.name}.tainted.{stamp}"))
            self.steps["outcomes_tainted_moved"] = stamp
            self.steps["outcomes_rerun_reason"] = "; ".join(before.problems)[:300]
        self._run(self.outcomes_cmd(session), self._log_path(session, "outcomes"), "outcomes")
        unbound = outcomes_completion(self.daily_dir, session.date, None, reducer)
        if not unbound.ok:
            raise CollectorError("outcomes_incomplete", "; ".join(unbound.problems))
        write_binding(self.daily_dir, session.date, coverage, reducer, unbound.episodes, self.source)
        after = outcomes_completion(self.daily_dir, session.date, coverage, reducer)
        if not after.ok:
            raise CollectorError("outcomes_binding_failed", "; ".join(after.problems))
        for suffix in (".json", ".csv", ".md", ".binding.json"):
            self.outputs.append(str(stem.with_suffix(suffix)))
        self.steps["outcomes"] = "ran"
        return after

    def aggregate(self, session: Session) -> dict[str, Any]:
        self.aggregate_dir.mkdir(parents=True, exist_ok=True)
        episodes_cmd = self.episodes_cmd(session)
        episodes_json = Path(episodes_cmd[-1])
        self._run(episodes_cmd, self._log_path(session, "episodes"), "episodes")
        if not episodes_json.exists() or episodes_json.stat().st_size == 0:
            raise CollectorError("aggregate_missing_output", str(episodes_json))
        reducer_provenance = stamp_reducer_aggregate(episodes_json, self.source, self.collection_start, session.date)
        self.outputs.append(str(episodes_json))

        sessions = sessions_between(self.collection_start, session.date)
        outcomes, present, missing, errors, provenance = load_daily_outcomes(self.daily_dir, sessions)
        if errors:
            raise CollectorError("aggregate_tainted_daily", json.dumps(errors)[:400])
        if session.date.isoformat() not in present:
            raise CollectorError("aggregate_missing_target_session", session.date.isoformat())
        summary = summarize_outcomes(outcomes)
        summary["date_from"], summary["date_to"] = self.collection_start.isoformat(), session.date.isoformat()
        summary["episodes"] = len(outcomes)
        summary["sessions_present"], summary["sessions_missing"] = present, missing
        summary["generated_at"] = datetime.now(timezone.utc).isoformat()
        summary["provider_errors"] = {}
        # The roll-up was assembled by THIS commit, but each daily file keeps the commit that produced it.
        summary["aggregated_by"] = {
            "collector_id": COLLECTOR_ID, "collector_version": COLLECTOR_VERSION, "source": self.source.to_dict(),
            "method": "stored daily outcome files; no provider call",
        }
        summary["sessions_provenance"] = provenance
        summary["constituent_source_shas"] = sorted({p["source_sha"] for p in provenance.values()})
        summary["reducer_aggregate"] = {"path": str(episodes_json), "provenance": reducer_provenance}
        stem = aggregate_stem(self.aggregate_dir, self.collection_start, session.date)
        stem.with_suffix(".json").write_text(json.dumps({"summary": summary, "episodes": [o.to_row() for o in outcomes]}, indent=1, sort_keys=True))
        write_markdown(stem.with_suffix(".md"), summary, summary["date_from"], summary["date_to"], {})
        for suffix in (".json", ".md"):
            path = stem.with_suffix(suffix)
            if not path.exists() or path.stat().st_size == 0:
                raise CollectorError("aggregate_missing_output", str(path))
            self.outputs.append(str(path))
        self.steps["aggregate"] = "ran"
        return {"sessions_present": present, "sessions_missing": missing, "episodes": len(outcomes), "clean": summary["total"]["clean_episodes"],
                "constituent_source_shas": summary["constituent_source_shas"], "sessions_provenance": provenance}

    # ------------------------------------------------------------------ #
    # orchestration
    # ------------------------------------------------------------------ #

    def _record(self, session: Session | None, status: str, **extra: Any) -> dict[str, Any]:
        record = base_record(session, status, self.source, **extra)
        append_ledger(self.ledger_path, record)
        return record

    def collect_session(self, session: Session, universe: Sequence[str]) -> str:
        """Collect one session end to end. Returns the ledger status. Raises CollectorError on any failure."""
        self._reset_session_state()
        coverage_before, outcomes_before, _ = self.status_of(session, universe)
        if coverage_before.ok and outcomes_before.ok:
            self._record(session, STATUS_ALREADY_COLLECTED, coverage=coverage_before.to_dict(), outcomes=outcomes_before.to_dict())
            print(f"{STATUS_ALREADY_COLLECTED}: {session.date} observer run {coverage_before.run_id}, {outcomes_before.episodes} episodes")
            return STATUS_ALREADY_COLLECTED
        if self.authoritative_session(session) is None:
            self._record(session, STATUS_SKIPPED, reason="broker_calendar_closed")
            print(f"{STATUS_SKIPPED}: broker calendar reports no session on {session.date}")
            return STATUS_SKIPPED
        api_key, secret_key = resolve_alpaca_credentials()
        if not api_key or not secret_key:
            raise CollectorError("credentials_missing", "ALPACA_API_KEY/ALPACA_KEY + secret not set")
        started = datetime.now(timezone.utc)
        self._record(session, "STARTED", started_at=started.isoformat(), observer_before=coverage_before.to_dict(), outcomes_before=outcomes_before.to_dict())
        coverage = self.collect_observer(session, universe)
        if self.steps.get("observer") == "ran" and self.pause_seconds > 0:
            self.sleep(self.pause_seconds)
        outcomes = self.collect_outcomes(session, coverage)
        self._record(
            session, STATUS_DONE, started_at=started.isoformat(), finished_at=datetime.now(timezone.utc).isoformat(),
            steps=dict(self.steps), coverage=coverage.to_dict(), outcomes=outcomes.to_dict(), outputs=list(self.outputs), logs=list(self.logs),
        )
        print(f"{STATUS_DONE}: {session.date} observable {coverage.observable}/{coverage.requested} events {coverage.events}; episodes {outcomes.episodes} (clean {outcomes.clean})")
        return STATUS_DONE

    def run(self, explicit: date | None, *, plan_only: bool = False) -> int:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        session: Session | None = None
        try:
            refuse_v1_database(self.sqlite_path)
            universe = load_universe(self.universe_path)
            if not universe:
                raise CollectorError("universe_empty", str(self.universe_path))

            if explicit is not None:
                session = resolve_target(self.now, explicit)
                if session is None:
                    if not plan_only:
                        self._record(None, STATUS_SKIPPED, reason="no_session", requested_date=explicit.isoformat())
                    print(f"{STATUS_SKIPPED}: {explicit} is not a session (weekend/holiday)")
                    return EXIT_OK
                settled, targets = [session], [session]
                newest = session
            else:
                settled, targets = self.candidates(universe)
                if not settled:
                    raise CollectorError("no_settled_session", f"nothing settled since {self.collection_start} as of {self.now.isoformat()}")
                newest = settled[-1]
            deferred = targets[self.max_sessions:]
            targets = targets[: self.max_sessions]

            if plan_only:
                plan = []
                for s in targets:
                    c, o, r = self.status_of(s, universe)
                    plan.append({"session_date": s.date.isoformat(), "close": s.close.isoformat(), "early_close": s.is_early_close,
                                 "observer": c.to_dict(), "outcomes": o.to_dict(), "reducer_episodes": r,
                                 "commands": [self.observer_cmd(s), self.outcomes_cmd(s)]})
                print(json.dumps({
                    "source": self.source.to_dict(), "settled_since_start": [s.date.isoformat() for s in settled],
                    "would_collect": plan, "deferred_beyond_max_sessions": [s.date.isoformat() for s in deferred],
                    "aggregate": {"exists": aggregate_stem(self.aggregate_dir, self.collection_start, newest.date).with_suffix(".json").exists(), "command": self.episodes_cmd(newest)},
                }, indent=1, sort_keys=True))
                return EXIT_OK

            if not targets:
                session = newest
                self.collect_session(newest, universe)  # records ALREADY_COLLECTED
            else:
                collected = []
                for session in targets:
                    status = self.collect_session(session, universe)
                    if status == STATUS_DONE:
                        collected.append(session.date.isoformat())
                if deferred:
                    print(f"deferred beyond --max-sessions {self.max_sessions}: {[s.date.isoformat() for s in deferred]}; rerun to continue")
                    self._record(None, "DEFERRED", sessions=[s.date.isoformat() for s in deferred], collected=collected)
                    return EXIT_OK

            aggregate_json = aggregate_stem(self.aggregate_dir, self.collection_start, newest.date).with_suffix(".json")
            _, newest_outcomes, _ = self.status_of(newest, universe)
            if newest_outcomes.ok and (targets or not aggregate_json.exists()):
                session = newest
                self._reset_session_state()
                aggregate = self.aggregate(newest)
                self._record(newest, "AGGREGATED", steps=dict(self.steps), aggregate=aggregate, outputs=list(self.outputs), logs=list(self.logs))
                print(f"AGGREGATED: {aggregate['episodes']} episodes over {len(aggregate['sessions_present'])} sessions {self.collection_start}..{newest.date}"
                      + (f"; sessions without daily outcomes: {aggregate['sessions_missing']}" if aggregate["sessions_missing"] else ""))
            return EXIT_OK
        except CollectorError as exc:
            if not plan_only:
                self._record(session, STATUS_FAILED, reason=exc.reason, detail=exc.detail, steps=dict(self.steps), outputs=list(self.outputs), logs=list(self.logs))
            print(f"{STATUS_FAILED}: {exc.reason}: {exc.detail}", file=sys.stderr)
            return EXIT_FAILED


def _parse_symbols(value: str | None) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_ALLOW_UNOBSERVABLE
    return tuple(s.strip().upper() for s in value.split(",") if s.strip())


def build_collector(args: argparse.Namespace, **overrides: Any) -> Collector:
    env = os.environ
    kwargs: dict[str, Any] = dict(
        data_dir=Path(args.data_dir or env.get("OPTIONS_COVERAGE_DATA_DIR") or DEFAULT_DATA_DIR),
        sqlite_path=Path(args.sqlite or env.get("OPTIONS_COVERAGE_SQLITE_PATH") or DEFAULT_SQLITE),
        universe_path=Path(args.universe or DEFAULT_UNIVERSE),
        collection_start=date.fromisoformat(args.collection_start or env.get("OPTIONS_COVERAGE_FROM") or DEFAULT_COLLECTION_START.isoformat()),
        allow_unobservable=_parse_symbols(args.allow_unobservable if args.allow_unobservable is not None else env.get("OPTIONS_COVERAGE_ALLOW_UNOBSERVABLE")),
        pause_seconds=args.pause_seconds,
        calendar_check=not args.no_calendar_check,
        max_sessions=args.max_sessions,
        now=datetime.fromisoformat(args.now) if args.now else None,
    )
    kwargs.update(overrides)
    if "source" not in kwargs:
        kwargs["source"] = source_provenance(ROOT, require_pinned=args.require_pinned)
    return Collector(**kwargs)


def main(argv: Sequence[str] | None = None, **overrides: Any) -> int:
    parser = argparse.ArgumentParser(description="After-close options coverage collector (read-only evidence oneshot)")
    parser.add_argument("--date", help="Session YYYY-MM-DD to collect (default: every settled, uncollected session since --from, oldest first)")
    parser.add_argument("--from", dest="collection_start", help="First session of the collection (default: 2026-09-09 or OPTIONS_COVERAGE_FROM)")
    parser.add_argument("--data-dir", help="Collector data dir (default: logs/coverage_collector or OPTIONS_COVERAGE_DATA_DIR)")
    parser.add_argument("--sqlite", help="Observer sqlite (default: logs/options_coverage_observer.sqlite or OPTIONS_COVERAGE_SQLITE_PATH)")
    parser.add_argument("--universe", help="Universe CSV (default: research/coverage/options_watchlist_150.csv)")
    parser.add_argument("--allow-unobservable", help="Comma list of universe symbols allowed to have no bars (default: SQ,VIX or OPTIONS_COVERAGE_ALLOW_UNOBSERVABLE)")
    parser.add_argument("--pause-seconds", type=float, default=15.0, help="Pause between the observer and outcome fetches (provider rate limit)")
    parser.add_argument("--max-sessions", type=int, default=DEFAULT_MAX_SESSIONS, help="Catch-up cap per run; the rest is deferred to the next run")
    parser.add_argument("--no-calendar-check", action="store_true", help="Skip the broker calendar cross-check")
    parser.add_argument("--require-pinned", action="store_true", help="Refuse to run unless this tree carries a release manifest naming its commit (systemd unit)")
    parser.add_argument("--plan", action="store_true", help="Resolve the targets and report what would run; run nothing, write nothing")
    parser.add_argument("--now", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        explicit = date.fromisoformat(args.date) if args.date else None
    except ValueError:
        parser.error(f"--date must be YYYY-MM-DD, got {args.date!r}")
    try:
        collector = build_collector(args, **overrides)
    except CollectorError as exc:
        print(f"{STATUS_FAILED}: {exc.reason}: {exc.detail}", file=sys.stderr)
        return EXIT_FAILED

    lock_path = collector.data_dir / ".collector.lock"
    collector.data_dir.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"{STATUS_FAILED}: another collector holds {lock_path}", file=sys.stderr)
            return EXIT_FAILED
        return collector.run(explicit, plan_only=args.plan)


if __name__ == "__main__":
    raise SystemExit(main())
