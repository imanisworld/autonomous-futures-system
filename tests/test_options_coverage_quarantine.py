"""Blind-window quarantine: presentation-only isolation of a registered trial population.

Synthetic data only. No provider, no observer sqlite, no real session.
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from alert_ranker.causal_bars import Bar
from alert_ranker.coverage_collector import (
    daily_quarantine_blocks,
    outcomes_completion,
    stamp_reducer_aggregate,
    CollectorError,
    SourceProvenance,
)
from alert_ranker.coverage_episodes import Episode, REDUCER_VERSION
from alert_ranker.coverage_outcomes import measure_episode
from alert_ranker.coverage_quarantine import (
    DEFAULT_POLICY_RELPATH,
    QUARANTINE_VERSION,
    BlindWindow,
    QuarantinePolicyError,
    canonical_bytes,
    load_blind_windows,
    partition_rows,
    public_block_fields,
    quarantine_record,
    verify_quarantine_block,
    write_quarantine,
)
from alert_ranker.session_calendar import nyse_session_for
from ops.options_212c_floor_outcome_monitor import COMPANION_TRIAL_ID, TRIAL_ID, V1_UNIVERSE
from scripts.options_coverage_episodes import apply_quarantine
from scripts.options_coverage_outcomes import write_products

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
DAY = "2026-10-06"
SESSION = nyse_session_for(date.fromisoformat(DAY))
assert SESSION is not None
OPEN = SESSION.open.astimezone(UTC)


def _window(**over) -> BlindWindow:
    values = dict(
        window_id="options-212c-forward-blind",
        trial_ids=(TRIAL_ID, COMPANION_TRIAL_ID),
        family="STRAT_212_CONTINUATION",
        symbols=frozenset(V1_UNIVERSE),
        quarantine_from="2026-10-05",
        released_at=None,
    )
    values.update(over)
    return BlindWindow(**values)


def _episode(symbol: str, family: str = "STRAT_212_CONTINUATION", session_date: str = DAY, **over) -> Episode:
    base = dict(
        reducer_version=REDUCER_VERSION, symbol=symbol, session_date=session_date, family=family, direction="LONG",
        v1_supported=True, requested_family=True, n_events=1,
        first_bar_start=(OPEN + timedelta(minutes=90)).isoformat(), first_bar_close=(OPEN + timedelta(minutes=120)).isoformat(),
        last_bar_start=(OPEN + timedelta(minutes=90)).isoformat(),
        entry_trigger=101.0, invalidation=100.0, risk=1.0,
        nearest_target_1=102.0, nearest_rr_1=1.0, nearest_reason="valid_targets", nearest_geometry_ok=True,
        floor_target_1=103.0, floor_rr_1=2.0, floor_reason="valid_targets", floor_geometry_ok=True, floor_rescued=False,
        spy_trend="bullish", qqq_trend="bullish", hourly_candle_type="two_up", daily_candle_type="two_up",
        spy_aligned=True, qqq_aligned=True, hourly_aligned=True, daily_aligned=True, alignment_ok=True, alignment_failures="",
        first_sight_at=(OPEN + timedelta(minutes=137, seconds=57)).isoformat(),
        first_sight_after_close=False, first_sight_price=101.4,
        nearest_remaining_rr=0.43, floor_remaining_rr=1.14, late_nearest=True, late_floor=False,
        would_qualify_v1_rule=False, would_qualify_floor_rule=True,
    )
    base.update(over)
    return Episode(**base)


def _bars() -> list[Bar]:
    out = []
    cursor = OPEN
    while cursor + timedelta(minutes=5) <= SESSION.close.astimezone(UTC):
        out.append(Bar(start=cursor, open=101.4, high=103.5, low=101.2, close=103.2, volume=1.0, vwap=102.0))
        cursor += timedelta(minutes=5)
    return out


def _outcomes():
    bars = _bars()
    eps = [
        _episode("AAPL"),  # V1 212C → quarantined
        _episode("SPY", direction="SHORT", invalidation=102.0, floor_target_1=99.0, nearest_target_1=100.0, first_sight_price=100.6),  # V1 212C → quarantined
        _episode("ZZZ"),  # non-V1 212C → public
        _episode("AAPL", family="STRAT_222_CONTINUATION"),  # V1 other family → public
        _episode("MSFT", session_date="2026-09-30", first_bar_start="2026-09-30T15:00:00+00:00", first_bar_close="2026-09-30T15:30:00+00:00", last_bar_start="2026-09-30T15:00:00+00:00", first_sight_at="2026-09-30T15:47:57+00:00"),  # before window → public
    ]
    return [measure_episode(e, SESSION.open, SESSION.close, bars) for e in eps]


# --------------------------------------------------------------------------- #
# policy
# --------------------------------------------------------------------------- #


def test_shipped_policy_loads_and_names_both_forward_trials() -> None:
    windows = load_blind_windows(ROOT / DEFAULT_POLICY_RELPATH)
    assert len(windows) == 1
    w = windows[0]
    assert w.window_id == "options-212c-forward-blind"
    assert set(w.trial_ids) == {TRIAL_ID, COMPANION_TRIAL_ID}
    assert w.family == "STRAT_212_CONTINUATION"
    assert w.symbols == frozenset(V1_UNIVERSE)
    assert w.quarantine_from == "2026-10-05"
    assert w.released_at is None
    assert w.active_on("2026-10-05") and w.active_on("2027-01-04")
    assert not w.active_on("2026-10-02")


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda p: p.unlink(), "policy_missing"),
        (lambda p: p.write_text("{"), "policy_unparseable"),
        (lambda p: p.write_text(json.dumps({"schema_version": 2, "windows": []})), "policy_schema_version"),
        (lambda p: p.write_text(json.dumps({"schema_version": 1, "windows": {}})), "policy_windows_invalid"),
    ],
)
def test_policy_fails_closed(tmp_path: Path, mutate, reason: str) -> None:
    path = tmp_path / "blind_windows.json"
    path.write_text((ROOT / DEFAULT_POLICY_RELPATH).read_text())
    mutate(path)
    with pytest.raises(QuarantinePolicyError) as exc:
        load_blind_windows(path)
    assert exc.value.reason == reason


@pytest.mark.parametrize(
    "patch",
    [
        {"window_id": "Bad Id"},
        {"trial_ids": []},
        {"trial_ids": ["not-a-trial"]},
        {"trial_ids": [TRIAL_ID, TRIAL_ID]},
        {"symbols": []},
        {"symbols": ["aapl"]},
        {"quarantine_from": "yesterday"},
        {"released_at": "2026-12-01"},
        {"unexpected": True},
    ],
)
def test_policy_rejects_malformed_window_fields(tmp_path: Path, patch: dict) -> None:
    payload = json.loads((ROOT / DEFAULT_POLICY_RELPATH).read_text())
    payload["windows"][0].update(patch)
    path = tmp_path / "p.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(QuarantinePolicyError):
        load_blind_windows(path)


# --------------------------------------------------------------------------- #
# partition
# --------------------------------------------------------------------------- #


def test_partition_isolates_exactly_the_window_population() -> None:
    rows = [o.to_row() for o in _outcomes()]
    public, held = partition_rows(rows, [_window()])
    assert sorted(r["symbol"] for r in held["options-212c-forward-blind"]) == ["AAPL", "SPY"]
    assert [(r["symbol"], r["family"], r["session_date"]) for r in public] == [
        ("ZZZ", "STRAT_212_CONTINUATION", DAY),
        ("AAPL", "STRAT_222_CONTINUATION", DAY),
        ("MSFT", "STRAT_212_CONTINUATION", "2026-09-30"),
    ]
    released, held_after = partition_rows(rows, [_window(released_at="2027-01-05T00:00:00+00:00")])
    assert len(released) == 5 and not held_after
    earlier, _ = partition_rows(rows, [_window(quarantine_from="2026-10-07")])
    assert len(earlier) == 5


# --------------------------------------------------------------------------- #
# quarantine file + binding
# --------------------------------------------------------------------------- #


def test_quarantine_file_is_canonical_hash_bound_and_never_overwritten(tmp_path: Path) -> None:
    w = _window()
    rows = [o.to_row() for o in _outcomes()]
    _, held = partition_rows(rows, [w])
    record = quarantine_record(w, "outcomes", DAY, DAY, held[w.window_id], source={"x": 1}, quarantined_at=datetime(2026, 10, 6, 21, tzinfo=UTC))
    block = write_quarantine(tmp_path, record, window=w)
    assert set(block) == set(public_block_fields)
    assert block["quarantined_rows"] == 2
    assert block["quarantine_version"] == QUARANTINE_VERSION
    path = tmp_path / block["path"]
    assert path.read_bytes() == canonical_bytes(record)
    assert verify_quarantine_block(tmp_path, block) == []
    manifest = [json.loads(l) for l in (path.parent / "manifest.jsonl").read_text().splitlines()]
    assert manifest[-1]["sha256"] == block["sha256"]
    # the public block exposes no direction, outcome, or R
    rendered = json.dumps(block)
    for hidden in ("LONG", "SHORT", "TARGET_FIRST", "INVALIDATION_FIRST", "realized", "close_r", "would_qualify"):
        assert hidden not in rendered
    # re-write keeps the earlier bytes under a superseded name
    again = write_quarantine(tmp_path, record, window=w)
    assert again["sha256"] == block["sha256"]
    assert any(p.name.startswith(path.name + ".superseded.") for p in path.parent.iterdir())
    # tamper → refused
    body = bytearray(path.read_bytes())
    body[-2] ^= 1
    path.write_bytes(bytes(body))
    assert "quarantine_sha256" in verify_quarantine_block(tmp_path, block)
    path.write_bytes(canonical_bytes(record))
    assert "quarantine_row_count" in verify_quarantine_block(tmp_path, {**block, "quarantined_rows": 3})
    assert "quarantine_block_fields" in verify_quarantine_block(tmp_path, {**block, "extra": 1})
    with pytest.raises(QuarantinePolicyError, match="row_outside_window"):
        quarantine_record(w, "outcomes", DAY, DAY, [rows[2]], source={})


def test_write_products_redacts_every_public_product_and_reconciles(tmp_path: Path) -> None:
    outcomes = _outcomes()
    summary, public = write_products(tmp_path, DAY, DAY, outcomes, {}, [_window()], raw_events=7, episodes=5)
    stem = tmp_path / f"outcomes_{DAY}_{DAY}"
    payload = json.loads(stem.with_suffix(".json").read_text())
    public_syms = {(r["symbol"], r["family"]) for r in payload["episodes"]}
    assert ("AAPL", "STRAT_212_CONTINUATION") not in public_syms
    assert ("SPY", "STRAT_212_CONTINUATION") not in public_syms
    assert ("ZZZ", "STRAT_212_CONTINUATION") in public_syms
    assert ("AAPL", "STRAT_222_CONTINUATION") in public_syms
    assert len(public) == 3
    # the summary is computed over public rows only
    assert payload["summary"]["total"]["episodes"] == 3
    assert payload["summary"]["episodes"] == 5
    with stem.with_suffix(".csv").open() as handle:
        rows = list(csv.DictReader(handle))
    assert sorted((r["symbol"], r["family"]) for r in rows) == sorted(public_syms)
    md = stem.with_suffix(".md").read_text()
    assert "SPY" not in md
    assert "Blind-window quarantine" in md
    assert "2 structural STRAT_212_CONTINUATION rows" in md
    blocks = payload["summary"]["quarantine"]
    assert len(blocks) == 1 and blocks[0]["quarantined_rows"] == 2
    held = json.loads((tmp_path / blocks[0]["path"]).read_bytes())
    assert sorted(r["symbol"] for r in held["rows"]) == ["AAPL", "SPY"]
    assert all("views" in r for r in held["rows"])  # full outcome rows preserved, not deleted

    # the collector's completeness check reconciles public + quarantined to the reducer
    check = outcomes_completion(tmp_path, date.fromisoformat(DAY), None, 5)
    assert check.ok, check.problems
    assert check.episodes == 5 and check.quarantined == 2
    # a quarantine file that no longer binds fails the daily product closed
    (tmp_path / blocks[0]["path"]).write_bytes(b"{}\n")
    broken = outcomes_completion(tmp_path, date.fromisoformat(DAY), None, 5)
    assert not broken.ok and any(p.startswith("quarantine_") for p in broken.problems)
    with pytest.raises(CollectorError, match="aggregate_quarantine_unverified"):
        daily_quarantine_blocks(tmp_path, [SESSION])


def test_inactive_window_leaves_products_untouched(tmp_path: Path) -> None:
    outcomes = _outcomes()
    summary, public = write_products(tmp_path, DAY, DAY, outcomes, {}, [_window(quarantine_from="2026-10-07")], raw_events=7, episodes=5)
    assert "quarantine" not in summary
    assert len(public) == 5
    assert not (tmp_path / "quarantine").exists()
    check = outcomes_completion(tmp_path, date.fromisoformat(DAY), None, 5)
    assert check.ok and check.quarantined == 0


def test_active_window_with_no_matching_rows_still_proves_it_ran(tmp_path: Path) -> None:
    outcomes = [o for o in _outcomes() if o.symbol == "ZZZ"]
    summary, _ = write_products(tmp_path, DAY, DAY, outcomes, {}, [_window()], raw_events=1, episodes=1)
    block = summary["quarantine"][0]
    assert block["quarantined_rows"] == 0
    assert verify_quarantine_block(tmp_path, block) == []


def test_episode_aggregate_is_partitioned_and_stamped(tmp_path: Path) -> None:
    eps = [_episode("AAPL"), _episode("ZZZ"), _episode("NVDA", session_date="2026-09-30", first_bar_start="2026-09-30T15:00:00+00:00", first_bar_close="2026-09-30T15:30:00+00:00", last_bar_start="2026-09-30T15:00:00+00:00", first_sight_at="2026-09-30T15:47:57+00:00")]
    public, held, blocks = apply_quarantine(eps, [_window()], "2026-09-30", DAY, tmp_path, raw_events=3)
    assert [e.symbol for e in public] == ["ZZZ", "NVDA"]
    assert held == 1
    assert blocks[0]["kind"] == "episodes" and blocks[0]["quarantined_rows"] == 1
    out = tmp_path / f"episodes_2026-09-30_{DAY}.json"
    out.write_text(json.dumps({"summary": {}, "answers": {}, "episodes": [e.to_row() for e in public], "quarantine": blocks}))
    source = SourceProvenance(sha="a" * 40, provenance="working_tree", root=str(tmp_path))
    stamped = stamp_reducer_aggregate(out, source, date(2026, 9, 30), date.fromisoformat(DAY))
    assert stamped["episodes"] == 2 and stamped["quarantined_episodes"] == 1
    rendered = json.dumps(json.loads(out.read_text())["episodes"])
    assert "AAPL" not in rendered
    # no output dir → nothing written, rows still withheld from the printed summary
    public2, held2, blocks2 = apply_quarantine(eps, [_window()], "2026-09-30", DAY, None, raw_events=3)
    assert len(public2) == 2 and held2 == 1 and blocks2 == []
