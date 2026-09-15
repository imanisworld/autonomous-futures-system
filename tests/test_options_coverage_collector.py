"""After-close coverage collector: session choice, fail-closed checks, idempotent append-only runs, no forbidden side effects."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from alert_ranker.causal_bars import Bar
from alert_ranker.coverage_collector import (
    COLLECTOR_ID,
    COLLECTOR_VERSION,
    SETTLE_AFTER_CLOSE,
    STATUS_ALREADY_COLLECTED,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_SKIPPED,
    CollectorError,
    SourceProvenance,
    append_ledger,
    binding_path,
    daily_outcome_stem,
    load_daily_outcomes,
    observer_completion,
    outcome_from_row,
    outcomes_completion,
    provider_error_lines,
    read_ledger,
    reduced_episode_count,
    refuse_v1_database,
    resolve_target,
    sessions_between,
    settled_sessions,
    source_provenance,
    write_binding,
)
from alert_ranker.coverage_episodes import REDUCER_VERSION, Episode
from alert_ranker.coverage_observer import OBSERVED_TIMEFRAME, OBSERVER_VERSION
from alert_ranker.coverage_outcomes import OUTCOME_VERSION, measure_episode, summarize_outcomes
from alert_ranker.session_calendar import nyse_session_for
from scripts import options_coverage_collect as cli
from scripts.options_coverage_observer import SCHEMA

UTC = timezone.utc
DAY = date(2026, 9, 16)
SESSION = nyse_session_for(DAY)
SETTLED = SESSION.close + SETTLE_AFTER_CLOSE + timedelta(minutes=1)
UNIVERSE = ["AAPL", "NVDA", "SQ"]
ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
SOURCE = SourceProvenance(sha=SHA, provenance="release_manifest", root="/pinned/" + SHA)


# --------------------------------------------------------------------------- #
# fixtures / builders
# --------------------------------------------------------------------------- #


def write_universe(path: Path, symbols=UNIVERSE) -> Path:
    path.write_text("Ticker\n" + "\n".join(symbols) + "\n")
    return path


def seed_observer(sqlite_path: Path, day: date, *, symbols: dict[str, tuple[int, str]], events: int = 3, index_ok=(True, True)) -> None:
    conn = sqlite3.connect(sqlite_path)
    conn.executescript(SCHEMA)
    d = day.isoformat()
    for symbol, (observable, reason) in symbols.items():
        conn.execute(
            "INSERT OR REPLACE INTO coverage_symbols (observer_version, session_date, symbol, observable, reason, bars_current_session, expected_current_bars, sessions_loaded) VALUES (?,?,?,?,?,?,?,?)",
            (OBSERVER_VERSION, d, symbol, observable, reason, 13 if observable else 0, 13, 5),
        )
    for i in range(events):
        conn.execute(
            "INSERT OR REPLACE INTO coverage_events (observer_id, observer_version, symbol, timeframe, session_date, bar_start, bar_close, family, sequence, requested_family, v1_supported, direction, entry_trigger, invalidation, risk, nearest_geometry_ok, floor_geometry_ok, floor_rescued, alignment_ok, alignment_failures, first_sight_at, first_sight_after_close, row_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("OPTIONS_COVERAGE_OBSERVER", OBSERVER_VERSION, "AAPL", "30Min", d, f"{d}T1{i}:00:00+00:00", f"{d}T1{i}:30:00+00:00", "STRAT_222_CONTINUATION", "2-2-2", 1, 0, "LONG", 101.0, 100.0, 1.0, 1, 1, 0, 0, "spy", f"{d}T1{i}:47:57+00:00", 0, json.dumps(event_row(d, i))),
        )
    funnel = {"index_context": {"SPY": index_ok[0], "QQQ": index_ok[1]}}
    conn.execute(
        "INSERT INTO coverage_runs (observer_id, observer_version, session_date, ran_at, universe_source, symbols_requested, symbols_observable, events, funnel_json) VALUES (?,?,?,?,?,?,?,?,?)",
        ("OPTIONS_COVERAGE_OBSERVER", OBSERVER_VERSION, d, datetime.now(UTC).isoformat(), "test", len(symbols), sum(v[0] for v in symbols.values()), events, json.dumps(funnel)),
    )
    conn.commit()
    conn.close()


GOOD_SYMBOLS = {"AAPL": (1, "ok"), "NVDA": (1, "ok"), "SQ": (0, "no_bars")}


def event_row(d: str, i: int) -> dict:
    """A minimal observer event row_json the ep-v0.1 reducer accepts; bars 2h apart → one episode each."""
    return {
        "observer_id": "OPTIONS_COVERAGE_OBSERVER", "observer_version": OBSERVER_VERSION, "symbol": "AAPL", "timeframe": OBSERVED_TIMEFRAME, "session_date": d,
        "bar_start": f"{d}T1{i}:00:00+00:00", "bar_close": f"{d}T1{i}:30:00+00:00", "family": "STRAT_222_CONTINUATION", "sequence": "2-2-2",
        "requested_family": True, "v1_supported": False, "direction": "LONG", "entry_trigger": 101.0, "invalidation": 100.0, "risk": 1.0,
        "nearest_target_1": 102.0, "nearest_rr_1": 1.0, "nearest_reason": "valid_targets", "nearest_geometry_ok": True,
        "floor_target_1": 103.0, "floor_rr_1": 2.0, "floor_reason": "valid_targets", "floor_geometry_ok": True, "floor_rescued": False,
        "spy_trend": "bullish", "qqq_trend": "bullish", "hourly_candle_type": "two_up", "daily_candle_type": "two_up",
        "spy_aligned": True, "qqq_aligned": True, "hourly_aligned": True, "daily_aligned": True, "alignment_ok": True, "alignment_failures": "",
        "first_sight_at": f"{d}T1{i}:47:57+00:00", "first_sight_after_close": False, "first_sight_price": 101.4,
        "nearest_remaining_rr": 0.43, "floor_remaining_rr": 1.14, "late_nearest": True, "late_floor": False,
        "would_qualify_v1_rule": False, "would_qualify_floor_rule": False,
    }


def _bar(minutes: int, o: float, h: float, l: float, c: float, open_at: datetime) -> Bar:
    return Bar(start=open_at + timedelta(minutes=minutes), open=o, high=h, low=l, close=c, volume=1.0, vwap=(h + l) / 2)


def real_outcome(day: date):
    session = nyse_session_for(day)
    open_at = session.open
    ep = Episode(
        reducer_version=REDUCER_VERSION, symbol="AAPL", session_date=day.isoformat(), family="STRAT_222_CONTINUATION", direction="LONG",
        v1_supported=False, requested_family=True, n_events=1,
        first_bar_start=(open_at + timedelta(minutes=90)).isoformat(), first_bar_close=(open_at + timedelta(minutes=120)).isoformat(),
        last_bar_start=(open_at + timedelta(minutes=90)).isoformat(),
        entry_trigger=101.0, invalidation=100.0, risk=1.0,
        nearest_target_1=102.0, nearest_rr_1=1.0, nearest_reason="valid_targets", nearest_geometry_ok=True,
        floor_target_1=103.0, floor_rr_1=2.0, floor_reason="valid_targets", floor_geometry_ok=True, floor_rescued=False,
        spy_trend="bullish", qqq_trend="bullish", hourly_candle_type="two_up", daily_candle_type="two_up",
        spy_aligned=True, qqq_aligned=True, hourly_aligned=True, daily_aligned=True, alignment_ok=True, alignment_failures="",
        first_sight_at=(open_at + timedelta(minutes=137, seconds=57)).isoformat(), first_sight_after_close=False, first_sight_price=101.4,
        nearest_remaining_rr=0.43, floor_remaining_rr=1.14, late_nearest=True, late_floor=False,
        would_qualify_v1_rule=False, would_qualify_floor_rule=False,
    )
    bars = [_bar(m, 100.5, 100.55, 100.45, 100.5, open_at) for m in range(0, 90, 5)]
    bars += [_bar(90, 100.6, 100.9, 100.5, 100.8, open_at), _bar(95, 100.8, 101.2, 100.7, 101.1, open_at)]
    bars += [_bar(m, 101.3, 101.35, 101.25, 101.3, open_at) for m in range(100, 140, 5)]
    bars += [_bar(140, 101.4, 102.1, 101.3, 102.0, open_at)]
    bars += [_bar(m, 102.0, 102.05, 101.95, 102.0, open_at) for m in range(145, session.minutes, 5)]
    return measure_episode(ep, session.open, session.close, bars)


def write_daily(daily_dir: Path, day: date, *, provider_errors: dict | None = None, outcomes=None, raw_events: int = 3, bind: Path | None = None) -> Path:
    """A one-session outcome file as the out-v0.1 script writes it; ``bind=<sqlite>`` also writes the collector's binding sidecar."""
    daily_dir.mkdir(parents=True, exist_ok=True)
    outcomes = [real_outcome(day)] if outcomes is None else outcomes
    summary = summarize_outcomes(outcomes)
    summary.update({"date_from": day.isoformat(), "date_to": day.isoformat(), "provider_errors": provider_errors or {}, "episodes": len(outcomes), "raw_events": raw_events})
    stem = daily_outcome_stem(daily_dir, day)
    stem.with_suffix(".json").write_text(json.dumps({"summary": summary, "episodes": [o.to_row() for o in outcomes]}, indent=1, sort_keys=True))
    stem.with_suffix(".csv").write_text("symbol\nAAPL\n")
    stem.with_suffix(".md").write_text("# outcomes\n")
    if bind is not None:
        coverage = observer_completion(bind, day, UNIVERSE, ("SQ",))
        write_binding(daily_dir, day, coverage, reduced_episode_count(bind, day), len(outcomes), SOURCE)
    return stem


def seed_complete(sqlite_path: Path, daily_dir: Path, day: date) -> None:
    """Observer evidence + a daily file bound to it: what a finished session looks like on disk."""
    seed_observer(sqlite_path, day, symbols=GOOD_SYMBOLS)
    n = reduced_episode_count(sqlite_path, day)
    write_daily(daily_dir, day, outcomes=[real_outcome(day)] * n, raw_events=3, bind=sqlite_path)


class FakeRunner:
    """Stands in for the three subprocesses; records every argv it was handed."""

    def __init__(self, sqlite_path: Path, daily_dir: Path, *, observer_ok=True, observer_stdout="", outcomes_ok=True, outcomes_errors=None, exit_code=0):
        self.sqlite_path, self.daily_dir = sqlite_path, daily_dir
        self.observer_ok, self.observer_stdout = observer_ok, observer_stdout
        self.outcomes_ok, self.outcomes_errors = outcomes_ok, outcomes_errors
        self.exit_code = exit_code
        self.calls: list[list[str]] = []

    def __call__(self, cmd, log_path: Path) -> int:
        self.calls.append(list(cmd))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        script = Path(cmd[1]).name
        day = date.fromisoformat(cmd[cmd.index("--date") + 1]) if "--date" in cmd else date.fromisoformat(cmd[cmd.index("--to") + 1])
        text = f"$ {' '.join(cmd)}\n"
        if self.exit_code:
            log_path.write_text(text + "boom\n")
            return self.exit_code
        if script == "options_coverage_observer.py":
            symbols = GOOD_SYMBOLS if self.observer_ok else {"AAPL": (1, "ok"), "NVDA": (0, "session_gap"), "SQ": (0, "no_bars")}
            seed_observer(self.sqlite_path, day, symbols=symbols)
            text += self.observer_stdout
        elif script == "options_coverage_outcomes.py":
            if self.outcomes_ok:
                conn = sqlite3.connect(self.sqlite_path)
                raw = conn.execute("SELECT COUNT(*) FROM coverage_events WHERE session_date=?", (day.isoformat(),)).fetchone()[0]
                conn.close()
                n = reduced_episode_count(self.sqlite_path, day)
                write_daily(Path(cmd[cmd.index("--out") + 1]), day, provider_errors=self.outcomes_errors, outcomes=[real_outcome(day)] * n, raw_events=raw)
        elif script == "options_coverage_episodes.py":
            Path(cmd[cmd.index("--json") + 1]).write_text(json.dumps({"summary": {}, "episodes": []}))
        log_path.write_text(text)
        return 0


def make_cli_args(tmp_path: Path, **extra):
    universe = write_universe(tmp_path / "universe.csv")
    return {
        "data_dir": tmp_path / "data",
        "sqlite_path": tmp_path / "observer.sqlite",
        "universe_path": universe,
        "collection_start": DAY,
        "allow_unobservable": ("SQ",),
        "pause_seconds": 0.0,
        "calendar_check": False,
        "source": SOURCE,
        "sleep": lambda _s: None,
        "now": SETTLED,
        **extra,
    }


# --------------------------------------------------------------------------- #
# which session
# --------------------------------------------------------------------------- #


def test_resolve_latest_waits_for_settle_and_walks_back_over_closed_days():
    assert resolve_target(SETTLED).date == DAY
    just_closed = SESSION.close + timedelta(minutes=5)
    assert resolve_target(just_closed).date == date(2026, 9, 15)  # today not settled → previous session
    # Thanksgiving 2026-11-26 is closed; Friday 11-27 is a 13:00 ET early close.
    friday = nyse_session_for(date(2026, 11, 27))
    assert friday.is_early_close and friday.close == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
    assert resolve_target(friday.close + timedelta(minutes=10)).date == date(2026, 11, 25)
    assert resolve_target(friday.close + SETTLE_AFTER_CLOSE).date == date(2026, 11, 27)
    assert resolve_target(datetime(2026, 11, 26, 23, 0, tzinfo=UTC)).date == date(2026, 11, 25)


def test_resolve_explicit_skips_holidays_and_refuses_unsettled():
    assert resolve_target(SETTLED, date(2026, 9, 19)) is None  # Saturday
    assert resolve_target(SETTLED, date(2026, 11, 26)) is None  # Thanksgiving
    with pytest.raises(CollectorError) as exc:
        resolve_target(SESSION.close + timedelta(minutes=10), DAY)
    assert exc.value.reason == "session_not_settled"
    with pytest.raises(CollectorError):
        resolve_target(datetime(2026, 9, 16, 21, 0), DAY)  # naive now


def test_sessions_between_skips_weekend():
    days = [s.date.isoformat() for s in sessions_between(date(2026, 9, 11), date(2026, 9, 15))]
    assert days == ["2026-09-11", "2026-09-14", "2026-09-15"]


# --------------------------------------------------------------------------- #
# guards and checks
# --------------------------------------------------------------------------- #


def test_refuse_v1_database_by_name_and_schema(tmp_path):
    with pytest.raises(CollectorError) as exc:
        refuse_v1_database(tmp_path / "options_scanner.sqlite")
    assert exc.value.reason == "v1_database_refused"
    v1 = tmp_path / "other.sqlite"
    conn = sqlite3.connect(v1)
    conn.execute("CREATE TABLE scans (id INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(CollectorError):
        refuse_v1_database(v1)
    refuse_v1_database(tmp_path / "absent.sqlite")  # fine: will be created by the observer


def test_observer_completion_fails_closed(tmp_path):
    db = tmp_path / "obs.sqlite"
    assert observer_completion(db, DAY, UNIVERSE).problems == ["no_observer_sqlite"]
    seed_observer(db, DAY, symbols=GOOD_SYMBOLS)
    assert observer_completion(db, date(2026, 9, 15), UNIVERSE).problems == ["no_observer_run"]
    ok = observer_completion(db, DAY, UNIVERSE, allow_unobservable=("SQ",))
    assert ok.ok and ok.observable == 2 and ok.requested == 3 and ok.events == 3 and ok.unobservable == {"SQ": "no_bars"}
    strict = observer_completion(db, DAY, UNIVERSE, allow_unobservable=())
    assert not strict.ok and strict.disallowed == ["SQ"] and strict.problems == ["unobservable_not_allowed:SQ"]
    missing = observer_completion(db, DAY, [*UNIVERSE, "TSLA"], allow_unobservable=("SQ",))
    assert not missing.ok and missing.missing_rows == ["TSLA"]
    db2 = tmp_path / "obs2.sqlite"
    seed_observer(db2, DAY, symbols=GOOD_SYMBOLS, index_ok=(True, False), events=0)
    bad = observer_completion(db2, DAY, UNIVERSE, allow_unobservable=("SQ",))
    assert set(bad.problems) == {"index_context_unobservable:QQQ", "no_events"}


def test_outcomes_completion_fails_closed(tmp_path):
    daily = tmp_path / "daily"
    assert outcomes_completion(daily, DAY).problems == ["no_daily_json"]
    write_daily(daily, DAY)
    good = outcomes_completion(daily, DAY)
    assert good.ok and good.episodes == 1
    write_daily(daily, date(2026, 9, 15), provider_errors={"2026-09-15:AAPL": "provider_error:429"})
    tainted = outcomes_completion(daily, date(2026, 9, 15))
    assert not tainted.ok and tainted.problems == ["provider_errors:1"]
    write_daily(daily, date(2026, 9, 14), outcomes=[])
    assert "no_episodes" in outcomes_completion(daily, date(2026, 9, 14)).problems
    stem = write_daily(daily, date(2026, 9, 11))
    stem.with_suffix(".csv").unlink()
    assert outcomes_completion(daily, date(2026, 9, 11)).problems == [f"missing_output:{stem.name}.csv"]


def test_provider_error_lines():
    text = "symbols: requested=3 observable=2\n  not observable: SQ: no_bars\n  provider error: NVDA: provider_error:429\n"
    assert provider_error_lines(text) == ["provider error: NVDA: provider_error:429"]
    assert provider_error_lines("all fine\n") == []


def test_ledger_is_append_only(tmp_path):
    path = tmp_path / "ledger.jsonl"
    append_ledger(path, {"a": 1})
    first = path.read_text()
    append_ledger(path, {"b": 2})
    assert path.read_text().startswith(first)
    assert [r for r in read_ledger(path)] == [{"a": 1}, {"b": 2}]


def test_outcome_row_round_trip_and_local_rollup(tmp_path):
    outcome = real_outcome(DAY)
    rebuilt = outcome_from_row(outcome.to_row())
    assert rebuilt == outcome
    assert summarize_outcomes([rebuilt]) == summarize_outcomes([outcome])
    daily = tmp_path / "daily"
    write_daily(daily, DAY)
    sessions = sessions_between(date(2026, 9, 15), DAY)
    outcomes, present, missing, errors = load_daily_outcomes(daily, sessions)
    assert (present, missing, errors) == ([DAY.isoformat()], ["2026-09-15"], {})
    assert [o.symbol for o in outcomes] == ["AAPL"]
    with pytest.raises(CollectorError):
        outcome_from_row({"symbol": "X"})


# --------------------------------------------------------------------------- #
# the oneshot end to end (subprocesses faked)
# --------------------------------------------------------------------------- #


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv("ALPACA_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET", "s")


def test_full_run_then_idempotent_rerun(tmp_path, creds):
    kw = make_cli_args(tmp_path)
    runner = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily")
    code = cli.main(["--pause-seconds", "0"], runner=runner, **kw)
    assert code == 0
    scripts = [Path(c[1]).name for c in runner.calls]
    assert scripts == ["options_coverage_observer.py", "options_coverage_outcomes.py", "options_coverage_episodes.py"]
    ledger = read_ledger(kw["data_dir"] / "ledger.jsonl")
    assert [r["status"] for r in ledger] == ["STARTED", STATUS_DONE, "AGGREGATED"]
    done, agg_rec = ledger[1], ledger[2]
    for r in ledger:
        assert r["source"] == SOURCE.to_dict()  # exact commit on every record
        assert r["collector_id"] == COLLECTOR_ID and r["collector_version"] == COLLECTOR_VERSION
        assert r["observer_version"] == OBSERVER_VERSION and r["outcome_version"] == OUTCOME_VERSION
    assert done["session_date"] == DAY.isoformat() and done["steps"] == {"calendar": "disabled", "observer": "ran", "outcomes": "ran"}
    assert done["coverage"]["observable"] == 2 and done["coverage"]["unobservable"] == {"SQ": "no_bars"}
    binding = json.loads(binding_path(kw["data_dir"] / "daily", DAY).read_text())
    assert binding["observer_run_id"] == done["coverage"]["run_id"] and binding["raw_events"] == 3
    assert binding["reducer_episodes"] == 3 and binding["outcome_episodes"] == 3 and binding["source_sha"] == SHA
    agg = kw["data_dir"] / "aggregate"
    assert (agg / f"episodes_{DAY}_{DAY}.json").exists()
    rollup = json.loads((agg / f"outcomes_{DAY}_{DAY}.json").read_text())
    assert rollup["summary"]["sessions_present"] == [DAY.isoformat()] and rollup["summary"]["provider_errors"] == {}
    assert rollup["summary"]["aggregated_by"]["source"]["sha"] == SHA
    assert (agg / f"outcomes_{DAY}_{DAY}.md").read_text().startswith("# OPTIONS_COVERAGE_OUTCOMES")
    for path in done["outputs"] + done["logs"] + agg_rec["outputs"]:
        assert Path(path).exists()
    before = {p: p.read_bytes() for p in (kw["data_dir"] / "daily").iterdir()}

    rerun = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily")
    assert cli.main([], runner=rerun, **kw) == 0
    assert rerun.calls == []  # nothing fetched again, aggregate not rebuilt
    assert read_ledger(kw["data_dir"] / "ledger.jsonl")[-1]["status"] == STATUS_ALREADY_COLLECTED
    assert {p: p.read_bytes() for p in (kw["data_dir"] / "daily").iterdir()} == before


def test_catch_up_collects_every_missed_session_oldest_first(tmp_path, creds):
    # Collection started 09-14 (complete). Box missed Tue 09-15 and Wed 09-16; the timer fires once after 09-16 settles.
    kw = make_cli_args(tmp_path, collection_start=date(2026, 9, 14))
    seed_complete(kw["sqlite_path"], kw["data_dir"] / "daily", date(2026, 9, 14))
    runner = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily")
    assert cli.main([], runner=runner, **kw) == 0
    dated = [(Path(c[1]).name, c[c.index("--date") + 1] if "--date" in c else c[c.index("--to") + 1]) for c in runner.calls]
    assert dated == [
        ("options_coverage_observer.py", "2026-09-15"), ("options_coverage_outcomes.py", "2026-09-15"),
        ("options_coverage_observer.py", "2026-09-16"), ("options_coverage_outcomes.py", "2026-09-16"),
        ("options_coverage_episodes.py", "2026-09-16"),
    ]
    ledger = read_ledger(kw["data_dir"] / "ledger.jsonl")
    assert [(r["status"], r["session_date"]) for r in ledger] == [
        ("STARTED", "2026-09-15"), (STATUS_DONE, "2026-09-15"), ("STARTED", "2026-09-16"), (STATUS_DONE, "2026-09-16"), ("AGGREGATED", "2026-09-16"),
    ]
    rollup = json.loads((kw["data_dir"] / "aggregate" / "outcomes_2026-09-14_2026-09-16.json").read_text())
    assert rollup["summary"]["sessions_present"] == ["2026-09-14", "2026-09-15", "2026-09-16"] and rollup["summary"]["sessions_missing"] == []


def test_catch_up_stops_at_first_failure_and_never_aggregates(tmp_path, creds):
    kw = make_cli_args(tmp_path, collection_start=date(2026, 9, 15))
    runner = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily", observer_ok=False)
    assert cli.main([], runner=runner, **kw) == 1
    assert [(Path(c[1]).name, c[c.index("--date") + 1]) for c in runner.calls] == [("options_coverage_observer.py", "2026-09-15")]
    ledger = read_ledger(kw["data_dir"] / "ledger.jsonl")
    assert [(r["status"], r["session_date"]) for r in ledger] == [("STARTED", "2026-09-15"), (STATUS_FAILED, "2026-09-15")]
    assert not (kw["data_dir"] / "aggregate").exists()


def test_catch_up_defers_beyond_max_sessions(tmp_path, creds):
    kw = make_cli_args(tmp_path, collection_start=date(2026, 9, 14), max_sessions=2)
    runner = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily")
    assert cli.main([], runner=runner, **kw) == 0
    dates = sorted({c[c.index("--date") + 1] for c in runner.calls if "--date" in c})
    assert dates == ["2026-09-14", "2026-09-15"]
    ledger = read_ledger(kw["data_dir"] / "ledger.jsonl")
    assert ledger[-1]["status"] == "DEFERRED" and ledger[-1]["sessions"] == ["2026-09-16"]
    assert "AGGREGATED" not in [r["status"] for r in ledger]
    again = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily")
    assert cli.main([], runner=again, **kw) == 0
    assert [c[c.index("--date") + 1] for c in again.calls if "--date" in c] == ["2026-09-16"]
    assert read_ledger(kw["data_dir"] / "ledger.jsonl")[-1]["status"] == "AGGREGATED"


def test_repaired_observer_dataset_invalidates_bound_daily_file(tmp_path, creds):
    kw = make_cli_args(tmp_path)
    daily = kw["data_dir"] / "daily"
    seed_complete(kw["sqlite_path"], daily, DAY)
    assert cli.main([], runner=FakeRunner(kw["sqlite_path"], daily), **kw) == 0  # complete: only the aggregate is built
    # Observer re-run for the same date (a repair): new coverage_runs row, one more event.
    seed_observer(kw["sqlite_path"], DAY, symbols=GOOD_SYMBOLS, events=4)
    coverage = observer_completion(kw["sqlite_path"], DAY, UNIVERSE, ("SQ",))
    stale = outcomes_completion(daily, DAY, coverage, reduced_episode_count(kw["sqlite_path"], DAY))
    assert not stale.ok and any(p.startswith("binding_observer_run:") for p in stale.problems) and any(p.startswith("binding_raw_events:3!=4") for p in stale.problems)
    assert outcomes_completion(daily, DAY).ok  # unbound view alone would have passed — that is the gap being closed
    runner = FakeRunner(kw["sqlite_path"], daily)
    assert cli.main([], runner=runner, **kw) == 0
    assert [Path(c[1]).name for c in runner.calls] == ["options_coverage_outcomes.py", "options_coverage_episodes.py"]
    done = [r for r in read_ledger(kw["data_dir"] / "ledger.jsonl") if r["status"] == STATUS_DONE][-1]
    assert done["steps"]["observer"] == "already_complete" and "outcomes_tainted_moved" in done["steps"]
    assert "binding_observer_run" in done["steps"]["outcomes_rerun_reason"]
    assert sum(1 for p in daily.iterdir() if ".tainted." in p.name) == 4  # json, csv, md, binding kept
    fresh = json.loads(binding_path(daily, DAY).read_text())
    assert fresh["observer_run_id"] == coverage.run_id and fresh["raw_events"] == 4 and fresh["reducer_episodes"] == 4


def test_no_session_is_a_skip_not_a_failure(tmp_path):
    kw = make_cli_args(tmp_path)
    runner = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily")
    assert cli.main(["--date", "2026-09-19"], runner=runner, **kw) == 0
    assert runner.calls == []
    assert read_ledger(kw["data_dir"] / "ledger.jsonl")[-1]["status"] == STATUS_SKIPPED


def test_unsettled_explicit_session_fails_closed(tmp_path):
    kw = make_cli_args(tmp_path, now=SESSION.close + timedelta(minutes=5))
    runner = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily")
    assert cli.main(["--date", DAY.isoformat()], runner=runner, **kw) == 1
    assert runner.calls == []
    assert read_ledger(kw["data_dir"] / "ledger.jsonl")[-1]["reason"] == "session_not_settled"


def test_unsettled_today_is_simply_not_a_candidate_yet(tmp_path, creds):
    kw = make_cli_args(tmp_path, collection_start=date(2026, 9, 15), now=SESSION.close + timedelta(minutes=5))
    runner = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily")
    assert cli.main([], runner=runner, **kw) == 0
    assert sorted({c[c.index("--date") + 1] for c in runner.calls if "--date" in c}) == ["2026-09-15"]


def test_missing_credentials_fail_closed_before_any_fetch(tmp_path, monkeypatch):
    for name in ("ALPACA_API_KEY", "ALPACA_KEY", "ALPACA_SECRET_KEY", "ALPACA_SECRET"):
        monkeypatch.delenv(name, raising=False)
    kw = make_cli_args(tmp_path)
    runner = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily")
    assert cli.main([], runner=runner, **kw) == 1
    assert runner.calls == []
    assert read_ledger(kw["data_dir"] / "ledger.jsonl")[-1]["reason"] == "credentials_missing"


def test_incomplete_symbol_coverage_fails_closed(tmp_path, creds):
    kw = make_cli_args(tmp_path)
    runner = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily", observer_ok=False)
    assert cli.main([], runner=runner, **kw) == 1
    assert [Path(c[1]).name for c in runner.calls] == ["options_coverage_observer.py"]
    last = read_ledger(kw["data_dir"] / "ledger.jsonl")[-1]
    assert last["status"] == STATUS_FAILED and last["reason"] == "observer_coverage_incomplete"
    assert "unobservable_not_allowed:NVDA" in last["detail"]


def test_provider_error_in_observer_output_fails_closed(tmp_path, creds):
    kw = make_cli_args(tmp_path)
    runner = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily", observer_stdout="  provider error: NVDA: provider_error:429\n")
    assert cli.main([], runner=runner, **kw) == 1
    assert read_ledger(kw["data_dir"] / "ledger.jsonl")[-1]["reason"] == "observer_provider_errors"


def test_subprocess_failure_fails_closed(tmp_path, creds):
    kw = make_cli_args(tmp_path)
    runner = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily", exit_code=3)
    assert cli.main([], runner=runner, **kw) == 1
    last = read_ledger(kw["data_dir"] / "ledger.jsonl")[-1]
    assert last["reason"] == "observer_exit_3" and last["logs"]


def test_missing_outcome_output_fails_closed(tmp_path, creds):
    kw = make_cli_args(tmp_path)
    runner = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily", outcomes_ok=False)
    assert cli.main([], runner=runner, **kw) == 1
    last = read_ledger(kw["data_dir"] / "ledger.jsonl")[-1]
    assert last["reason"] == "outcomes_incomplete" and "no_daily_json" in last["detail"]


def test_tainted_daily_file_is_moved_aside_then_replaced(tmp_path, creds):
    kw = make_cli_args(tmp_path)
    seed_observer(kw["sqlite_path"], DAY, symbols=GOOD_SYMBOLS)
    daily = kw["data_dir"] / "daily"
    write_daily(daily, DAY, provider_errors={"x": "provider_error:429"}, bind=kw["sqlite_path"])
    runner = FakeRunner(kw["sqlite_path"], daily)
    assert cli.main([], runner=runner, **kw) == 0
    assert [Path(c[1]).name for c in runner.calls] == ["options_coverage_outcomes.py", "options_coverage_episodes.py"]  # observer reused
    names = sorted(p.name for p in daily.iterdir())
    assert any(".tainted." in n for n in names) and f"outcomes_{DAY}_{DAY}.json" in names
    done = [r for r in read_ledger(kw["data_dir"] / "ledger.jsonl") if r["status"] == STATUS_DONE][-1]
    assert done["steps"]["observer"] == "already_complete" and "outcomes_tainted_moved" in done["steps"]


def test_plan_runs_nothing_and_writes_nothing(tmp_path, capsys):
    kw = make_cli_args(tmp_path)
    runner = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily")
    assert cli.main(["--plan"], runner=runner, **kw) == 0
    assert runner.calls == []
    assert not (kw["data_dir"] / "ledger.jsonl").exists()
    plan = json.loads(capsys.readouterr().out)
    assert plan["source"]["sha"] == SHA
    assert [p["session_date"] for p in plan["would_collect"]] == [DAY.isoformat()] and plan["settled_since_start"] == [DAY.isoformat()]


def test_v1_database_path_is_refused(tmp_path, creds):
    kw = make_cli_args(tmp_path, sqlite_path=tmp_path / "options_scanner.sqlite")
    runner = FakeRunner(kw["sqlite_path"], kw["data_dir"] / "daily")
    assert cli.main([], runner=runner, **kw) == 1
    assert runner.calls == []
    assert read_ledger(kw["data_dir"] / "ledger.jsonl")[-1]["reason"] == "v1_database_refused"


# --------------------------------------------------------------------------- #
# provenance
# --------------------------------------------------------------------------- #


def _manifest(root: Path, sha: str, dirty: bool = False) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "release_manifest.json").write_text(json.dumps({"repo": {"commit": sha, "dirty": dirty, "branch": "main"}, "fingerprint_sha256": "f" * 64}))


def test_source_provenance_pinned_release_manifest(tmp_path):
    root = tmp_path / "releases" / SHA
    _manifest(root, SHA)
    src = source_provenance(root, require_pinned=True)
    assert src == SourceProvenance(sha=SHA, provenance="release_manifest", root=str(root.resolve()), dirty=False, manifest_fingerprint="f" * 64)
    # a symlink like coverage/current resolves to the pinned dir
    link = tmp_path / "current"
    link.symlink_to(root)
    assert source_provenance(link, require_pinned=True).sha == SHA
    wrong_dir = tmp_path / "somewhere"
    _manifest(wrong_dir, SHA)
    with pytest.raises(CollectorError) as exc:
        source_provenance(wrong_dir, require_pinned=True)
    assert exc.value.reason == "release_dir_not_pinned"
    assert source_provenance(wrong_dir).sha == SHA  # unpinned runs may use any manifest-bearing tree
    dirty = tmp_path / "dirty"
    _manifest(dirty, SHA, dirty=True)
    with pytest.raises(CollectorError) as exc:
        source_provenance(dirty)
    assert exc.value.reason == "release_manifest_dirty"


def test_source_provenance_working_tree_is_labelled_and_refused_when_pinned_required():
    src = source_provenance(ROOT)
    assert src.provenance == "working_tree" and len(src.sha) == 40
    with pytest.raises(CollectorError) as exc:
        source_provenance(ROOT, require_pinned=True)
    assert exc.value.reason == "provenance_unpinned"


def test_require_pinned_from_cli_fails_closed_in_a_working_tree(tmp_path, capsys):
    kw = make_cli_args(tmp_path)
    kw.pop("source")
    assert cli.main(["--plan", "--require-pinned"], **kw) == 1
    assert "provenance_unpinned" in capsys.readouterr().err
    assert not (kw["data_dir"] / "ledger.jsonl").exists()


# --------------------------------------------------------------------------- #
# what would run on the box
# --------------------------------------------------------------------------- #


def test_commands_contain_no_forbidden_side_effects(tmp_path):
    kw = make_cli_args(tmp_path)
    collector = cli.Collector(**kw)
    argv = " ".join(" ".join(cmd) for cmd in (collector.observer_cmd(SESSION), collector.outcomes_cmd(SESSION), collector.episodes_cmd(SESSION)))
    for forbidden in ("git", "systemctl", "options_scanner.sqlite", "discord", "restart"):
        assert forbidden not in argv
    assert "--from 2026-09-16 --to 2026-09-16" in " ".join(collector.outcomes_cmd(SESSION))  # exactly one session fetched
    import scripts.options_coverage_collect as module
    assert not any(name in module.__dict__ for name in ("subprocess_git", "restart", "alert", "promote"))


def test_systemd_units_run_a_pinned_isolated_oneshot():
    service = (ROOT / "deploy" / "systemd" / "afs-coverage-collector.service").read_text()
    timer = (ROOT / "deploy" / "systemd" / "afs-coverage-collector.timer").read_text()
    assert "Type=oneshot" in service
    assert "WorkingDirectory=/root/afs-shared/coverage/current" in service
    assert "/root/afs-shared/coverage/current/.venv/bin/python /root/afs-shared/coverage/current/scripts/options_coverage_collect.py" in service
    assert "--require-pinned" in service
    assert "--sqlite /root/afs-shared/coverage/options_coverage_observer.sqlite" in service
    for line in service.splitlines():
        if line.startswith(("ExecStart", "WorkingDirectory", "Environment")):
            assert "/root/autonomous-futures-system" not in line, line  # never the production tree
    for forbidden in ("git ", "systemctl", "futures-bot.service", "options_scanner.sqlite", "ExecStartPre"):
        assert forbidden not in service, forbidden
    assert "OnCalendar=Mon..Fri *-*-* 16:35:00 America/New_York" in timer
    assert "Persistent=true" in timer and "Unit=afs-coverage-collector.service" in timer
    assert timedelta(minutes=35) > SETTLE_AFTER_CLOSE


def test_install_script_touches_only_the_coverage_release():
    script = (ROOT / "deploy" / "coverage" / "install_coverage_release.sh").read_text()
    assert "set -euo pipefail" in script
    assert "release_manifest.json" in script and "--require-pinned" in script and "chmod -R a-w" in script
    assert "ln -sfn '$RELEASES/$REF' '$CURRENT'" in script
    body = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    for forbidden in ("autonomous-futures-system", "afs-releases", "futures-bot", "afs-watcher", "scanner", "git pull", "git push"):
        assert forbidden not in body, forbidden
    for line in body.splitlines():
        if "systemctl" in line:
            assert "'$TIMER'" in line or "daemon-reload" in line, line
            assert "restart" not in line, line
