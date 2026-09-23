"""Prereg #929 forward evaluator tests (synthetic fixtures only; no gitignored data)."""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from research import prereg929_forward_portfolio as fp
from research import prereg929_step0 as s0
from research.mnq_combined_portfolio_audit import TIE_PRIORITY, PortfolioEvent, replay_portfolio, summarize_fills
from research.prereg929_forward_corpus import (
    BoxBarError,
    LoadedBars,
    build,
    load_box_bars,
    parse_box_ts,
    split_by_utc_day,
)

REPO = Path(__file__).resolve().parents[1]
UTC = timezone.utc

# Git blob SHA-1s of the ported files at archive/pr915 5a9f14baf714947b98a38a19b45f04a8d18fb365.
PORTED_BLOBS_5A9F14B = {
    "research/mnq_combined_portfolio_audit.py": "82ee6cfa3f356bba368ec7302036da1f111e1ba7",
    "scripts/mnq_combined_portfolio_audit.py": "c24f82ef60e4c70dcea46dcdc1e5dadf58ec6406",
    "tests/test_mnq_combined_portfolio_audit.py": "e29bde144c97d2eaeca0d31547c57d15cc716a73",
    "research/mnq_sustained_trend_continuation_v1.py": "289f0ab9aaf7c18b09de387bd131e6a59cade0d4",
    "scripts/mnq_sustained_trend_continuation_v1.py": "909fc10e4168b5956533dea1346e412015bae719",
}


def _git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


# ─── ported core is byte-identical to 5a9f14b ────────────────────────────────

@pytest.mark.parametrize("rel,blob", sorted(PORTED_BLOBS_5A9F14B.items()))
def test_ported_915_files_are_byte_identical_to_5a9f14b(rel, blob):
    assert _git_blob_sha(REPO / rel) == blob, f"{rel} drifted from 5a9f14b"


def test_prereg_h1_tie_order_is_frozen_order_minus_asia():
    assert tuple(f for f in TIE_PRIORITY if f != fp.FAMILY_ASIA) == fp.PREREG_H1_TIE_ORDER
    assert fp.H1_FAMILIES == fp.PREREG_H1_TIE_ORDER
    # §4: Asia returns to its #915 slot for the six-family comparator.
    assert TIE_PRIORITY.index(fp.FAMILY_ASIA) == 4


def test_terminal_results_match_frozen_summarize_fills():
    ts = "2026-10-01T14:00:00+00:00"
    evs = [_ev(fp.FAMILY_4HR, f"x{r}", ts, result=r, net=1.0) for r in ("WIN", "LOSS", "BREAKEVEN", "OPEN_EOD", "EXPIRED", "OPEN")]
    s = summarize_fills(evs)
    assert s["terminal"] == sum(e.result in fp.TERMINAL_RESULTS for e in evs) == 3


# ─── helpers ─────────────────────────────────────────────────────────────────

def _ev(family, sid, fill_ts, *, signal_ts=None, exit_ts=None, result="WIN", net=10.0, day=None):
    fill = datetime.fromisoformat(fill_ts)
    exit_ts = exit_ts or (fill + timedelta(minutes=30)).isoformat()
    return PortfolioEvent(
        family=family,
        source_id=sid,
        signal_ts=signal_ts or fill_ts,
        eligible_fill_ts=fill_ts,
        exit_ts=exit_ts,
        observation_day=day or fp._obs_day(fill).isoformat(),
        direction="LONG",
        entry=100.0,
        stop=90.0,
        target=120.0,
        result=result,
        net_pnl=net,
    )


def _run(events_by_family=None, failed=None):
    events_by_family = events_by_family or {}
    fams = {}
    for f in fp.ALL_FAMILIES:
        if failed and f in failed:
            fams[f] = fp.FamilyOutput(family=f, status="FAILED_CLOSED", error="RuntimeError: boom")
        else:
            evs = events_by_family.get(f, [])
            fams[f] = fp.FamilyOutput(
                family=f, status="OK", events=evs,
                attempts=[{"family": f, "source_id": e.source_id, "ts": e.signal_ts} for e in evs],
            )
    return fp.AdapterRun(window=("2026-09-23", "2027-06-01"), enforce_controls=False, families=fams)


def _passing_step0(tmp_path: Path) -> Path:
    p = tmp_path / "step0.json"
    p.write_text(json.dumps({
        "prereg": "929", "kind": "step0_parity", "overall": "PASS",
        "checks": {k: {"verdict": "PASS"} for k in ("0a", "0b", "0c", "0d")},
        "attempts_log": [{"generated_at": "2026-10-01T00:00:00+00:00", "overall": "PASS"}],
    }))
    return p


def _many_fills(n, start=datetime(2026, 9, 24, 14, 0, tzinfo=UTC), family=fp.FAMILY_4HR):
    out = []
    for i in range(n):
        t = start + timedelta(days=i)
        out.append(_ev(family, f"{family}:{i}", t.isoformat(), result="WIN" if i % 3 else "LOSS",
                       net=100.0 if i % 3 else -40.0))
    return out


# ─── scoring filter ──────────────────────────────────────────────────────────

def test_scoring_requires_signal_and_fill_at_or_after_start():
    before = "2026-09-23T21:55:00+00:00"
    at = "2026-09-23T22:00:00+00:00"
    after = "2026-09-23T22:05:00+00:00"
    evs = [
        _ev(fp.FAMILY_4HR, "a", before),
        _ev(fp.FAMILY_4HR, "b", after, signal_ts=before),  # signal before start
        _ev(fp.FAMILY_4HR, "c", at),
        _ev(fp.FAMILY_4HR, "d", after),
    ]
    assert [e.source_id for e in fp.scored_events(evs)] == ["c", "d"]


def test_ex_asia_tie_order_in_frozen_replay():
    ts = "2026-10-01T14:00:00+00:00"
    evs = [_ev(f, f"{f}:x", ts) for f in reversed(fp.ALL_FAMILIES)]
    h1 = replay_portfolio(evs, excluded_families={fp.FAMILY_ASIA})
    assert h1.fills[0].family == fp.PREREG_H1_TIE_ORDER[0]
    skipped = [d.family for d in h1.decisions if d.disposition == "SKIPPED_BUSY_PORTFOLIO"]
    assert skipped == list(fp.PREREG_H1_TIE_ORDER[1:])


# ─── counts mode is blind ────────────────────────────────────────────────────

def test_assert_blind_rejects_pnl_keys():
    for bad in ({"net": 1}, {"x": {"pf": 1}}, {"wins": 3}, {"max_drawdown": 2}, [{"net_pnl": 0}], {"result": "WIN"}):
        with pytest.raises(AssertionError):
            fp.assert_blind(bad)
    fp.assert_blind({"terminal_portfolio_fills": 3, "busy_skips_by_family": {"4HR_RETRIGGER": 1}})


def test_counts_report_never_emits_pnl_fields():
    evs = _many_fills(5) + [_ev(fp.FAMILY_4HR, "pre", "2026-09-22T14:00:00+00:00", net=9999.0)]
    daily = [_ev(fp.FAMILY_DAILY, "d1", "2026-09-24T14:10:00+00:00", result="OPEN", net=0.0,
                 exit_ts="2026-09-26T14:10:00+00:00")]
    run = _run({fp.FAMILY_4HR: evs, fp.FAMILY_DAILY: daily})
    rep = fp.counts_report(run, {"observed_completed_days": 3}, as_of=datetime(2026, 10, 1, tzinfo=UTC))
    fp.assert_blind(rep)
    text = json.dumps(rep)
    for frag in ("net", "pnl", "\"pf\"", "WIN", "LOSS", "drawdown", "9999", "100.0", "-40"):
        assert frag not in text, frag
    c = rep["h1_counts"]
    assert c["portfolio_fills"] == 5
    assert c["terminal_portfolio_fills"] == 5
    assert c["busy_skips_by_family"] == {fp.FAMILY_DAILY: 1}  # 14:10 Daily blocked by the 14:00 4HR fill
    assert c["busy_skips_by_blocking_family"] == {fp.FAMILY_4HR: 1}
    assert rep["fillable_events_before_scoring_start_excluded"] == 1
    assert rep["sample"]["look_allowed"] is False


def test_counts_report_withholds_counts_when_a_family_failed_closed():
    run = _run({fp.FAMILY_4HR: _many_fills(3)}, failed={fp.FAMILY_ST})
    rep = fp.counts_report(run, {"observed_completed_days": 0}, as_of=datetime(2026, 10, 1, tzinfo=UTC))
    assert rep["h1_counts"] is None
    assert rep["pipeline_health"]["healthy"] is False
    assert rep["pipeline_health"]["family_status"][fp.FAMILY_ST] == "FAILED_CLOSED"


# ─── look mode gates ─────────────────────────────────────────────────────────

def test_look_refuses_without_step0_report():
    run = _run({fp.FAMILY_4HR: _many_fills(45)})
    with pytest.raises(fp.LookRefused, match="step-0"):
        fp.look_report(run, {"observed_completed_days": 130}, as_of=datetime(2027, 6, 1, tzinfo=UTC),
                       step0_report_path=None)


def test_look_refuses_when_step0_failed(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"prereg": "929", "kind": "step0_parity", "overall": "FAIL",
                             "checks": {k: {"verdict": "FAIL" if k == "0c" else "PASS"} for k in ("0a", "0b", "0c", "0d")}}))
    run = _run({fp.FAMILY_4HR: _many_fills(45)})
    with pytest.raises(fp.LookRefused, match="did not PASS"):
        fp.look_report(run, {"observed_completed_days": 130}, as_of=datetime(2027, 6, 1, tzinfo=UTC),
                       step0_report_path=p)


@pytest.mark.parametrize("n_fills,days", [(39, 130), (45, 119)])
def test_look_refuses_below_minimum_sample_before_deadline(tmp_path, n_fills, days):
    run = _run({fp.FAMILY_4HR: _many_fills(n_fills)})
    with pytest.raises(fp.LookRefused, match="minimum sample not met"):
        fp.look_report(run, {"observed_completed_days": days}, as_of=datetime(2027, 9, 29, tzinfo=UTC),
                       step0_report_path=_passing_step0(tmp_path))


def test_look_at_deadline_below_minimum_is_insufficient_sample(tmp_path):
    run = _run({fp.FAMILY_4HR: _many_fills(10)})
    rep = fp.look_report(run, {"observed_completed_days": 200}, as_of=datetime(2027, 9, 30, 12, tzinfo=UTC),
                         step0_report_path=_passing_step0(tmp_path))
    assert rep["section7"]["h1_verdict"] == "INSUFFICIENT_SAMPLE"


def test_look_refuses_when_pipeline_unhealthy(tmp_path):
    run = _run({fp.FAMILY_4HR: _many_fills(45)}, failed={fp.FAMILY_MIYAGI})
    with pytest.raises(fp.LookRefused, match="failed closed"):
        fp.look_report(run, {"observed_completed_days": 130}, as_of=datetime(2027, 6, 1, tzinfo=UTC),
                       step0_report_path=_passing_step0(tmp_path))


def test_look_computes_section7_fields(tmp_path):
    fills = _many_fills(45)
    asia = [_ev(fp.FAMILY_ASIA, "asia:0", "2026-09-24T13:55:00+00:00", exit_ts="2026-09-24T14:30:00+00:00",
                result="LOSS", net=-50.0)]
    run = _run({fp.FAMILY_4HR: fills, fp.FAMILY_ASIA: asia})
    rep = fp.look_report(run, {"observed_completed_days": 130}, as_of=datetime(2027, 6, 1, tzinfo=UTC),
                         step0_report_path=_passing_step0(tmp_path))
    s7 = rep["section7"]
    assert s7["terminal_fills"] == 45 and s7["cme_days"] == 130
    assert set(s7["h1_criteria"]) == {
        "1_net_gt_0", "2_pf_ge_1_94", "3_both_halves_net_gt_0",
        "4_max_drawdown_le_1750", "5_top3_days_share_le_60pct",
    }
    # 30 wins * 100 - 15 losses * 40 = 2400 > 0; PF 5.0; top-3 share 300/2400.
    assert s7["h1_criteria"]["1_net_gt_0"]["value"] == 2400.0
    assert s7["h1_criteria"]["5_top3_days_share_le_60pct"]["value"] == pytest.approx(0.125)
    assert s7["h1_verdict"] == "FORWARD_PORTFOLIO_EVIDENCE"
    # Asia blocked the first 4HR fill in the six-family run -> H2 confirmed.
    assert s7["h2"]["asia_busy_skipped_fills_taken_by_ex_asia"] == [f"{fp.FAMILY_4HR}:0"]
    assert s7["h2"]["verdict"] == "CONFIRMED"
    d = s7["descriptive_table"]
    assert d["net_with_plus_1_tick_adverse_slippage_per_side"] == pytest.approx(2400.0 - 45 * 1.0)
    assert set(d["leave_one_out_inside_five_family"]) == set(fp.H1_FAMILIES)


def test_look_rejects_when_pf_below_hurdle(tmp_path):
    fills = []
    for i in range(45):
        t = datetime(2026, 9, 24, 14, tzinfo=UTC) + timedelta(days=i)
        win = i % 2 == 0
        fills.append(_ev(fp.FAMILY_4HR, f"f{i}", t.isoformat(), result="WIN" if win else "LOSS",
                         net=60.0 if win else -50.0))
    rep = fp.look_report(_run({fp.FAMILY_4HR: fills}), {"observed_completed_days": 130},
                         as_of=datetime(2027, 6, 1, tzinfo=UTC), step0_report_path=_passing_step0(tmp_path))
    assert rep["section7"]["h1_criteria"]["2_pf_ge_1_94"]["pass"] is False
    assert rep["section7"]["h1_verdict"] == "FORWARD_PORTFOLIO_REJECTED"


# ─── CLI ─────────────────────────────────────────────────────────────────────

def _tiny_corpus(root: Path, days: list[str]):
    leaf = root / "MNQ"
    leaf.mkdir(parents=True)
    for d in days:
        rows = [{"timestamp": f"{d}T{h:02d}:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1} for h in range(0, 24)]
        (leaf / f"MNQ_{d}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_cli_counts_mode_prints_no_pnl(tmp_path, monkeypatch, capsys):
    import importlib

    cli = importlib.import_module("scripts.prereg929_forward_portfolio")
    days = ["2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]
    _tiny_corpus(tmp_path / "c5", days)
    _tiny_corpus(tmp_path / "c15", days)
    fake = _run({fp.FAMILY_4HR: _many_fills(2)})
    monkeypatch.setattr(fp, "run_family_adapters", lambda *a, **k: fake)
    out = tmp_path / "counts.json"
    rc = cli.main(["counts", "--corpus-5m", str(tmp_path / "c5"), "--corpus-15m", str(tmp_path / "c15"),
                   "--as-of", "2026-09-26T00:00:00+00:00", "--out", str(out)])
    assert rc == 0
    printed = capsys.readouterr()
    for blob in (printed.out, printed.err, out.read_text()):
        for frag in ("net", "pnl", "\"pf\"", "WIN", "LOSS", "drawdown", "100.0"):
            assert frag not in blob, frag
    rep = json.loads(out.read_text())
    assert rep["h1_counts"]["terminal_portfolio_fills"] == 2
    # Obs days 09-24 (from 09-23 22:00Z) and 09-25 completed by 09-26T00:00Z.
    assert rep["cme_observation_days"]["observed_completed_days"] == 2


def test_cli_look_refuses_without_confirmation_or_step0(tmp_path, capsys):
    import importlib

    cli = importlib.import_module("scripts.prereg929_forward_portfolio")
    base = ["look", "--corpus-5m", str(tmp_path), "--corpus-15m", str(tmp_path), "--out", str(tmp_path / "look.json")]
    assert cli.main(base) == 3
    assert cli.main(base + ["--confirm-single-look"]) == 3
    assert "step-0" in capsys.readouterr().err
    (tmp_path / "look.json").write_text("{}")
    assert cli.main(base + ["--confirm-single-look", "--step0-report", str(_passing_step0(tmp_path))]) == 3
    assert "already happened" in capsys.readouterr().err


# ─── harness patching ────────────────────────────────────────────────────────

def test_harness_isolates_failures_and_restores_frozen_module(tmp_path, monkeypatch):
    from scripts import mnq_combined_portfolio_audit as pa

    orig = (pa.START, pa.END, pa._assert_control, pa.bracket_summary, pa.summarize_replay,
            pa.daily_audit.summary, pa.sustained_script.run, pa.REPO)
    seen = {}

    def fake_4hr(root5):
        seen["window"] = (pa.START, pa.END)
        pa._assert_control("x", {"net": 1.0}, {"net": 2.0})  # must be a no-op off the frozen corpus
        print("net 12345 should never reach stdout")
        return [_ev(fp.FAMILY_4HR, "4hr:a", "2026-10-01T14:00:00+00:00")], {"control": 1}, [
            {"family": fp.FAMILY_4HR, "source_id": "4hr:a", "ts": "2026-10-01T14:00:00+00:00"}]

    def fake_daily(root5):
        raise RuntimeError("frozen guard tripped")

    def fake_miyagi(root5):
        cands = json.loads((pa.REPO / fp.MIYAGI_FROZEN_REL).read_text())["candidates"]
        seen["miyagi"] = cands
        return [], {}, []

    monkeypatch.setattr(pa, "_four_hr", fake_4hr)
    monkeypatch.setattr(pa, "_daily", fake_daily)
    monkeypatch.setattr(pa, "_miyagi", fake_miyagi)
    monkeypatch.setattr(fp, "detect_miyagi_candidates", lambda *a: [{"date": "2026-10-02", "direction": "LONG"}])
    _tiny_corpus(tmp_path / "c", ["2026-10-01", "2026-10-02"])
    roots = fp.single_pair_roots(tmp_path / "c", tmp_path / "c")
    run = fp.run_family_adapters(roots, date(2026, 9, 23), date(2026, 10, 5),
                                 families=[fp.FAMILY_4HR, fp.FAMILY_DAILY, fp.FAMILY_MIYAGI])
    assert seen["window"] == (date(2026, 9, 23), date(2026, 10, 5))
    assert run.families[fp.FAMILY_4HR].status == "OK"
    assert run.families[fp.FAMILY_4HR].diag is None  # control diagnostics discarded
    assert run.families[fp.FAMILY_DAILY].status == "FAILED_CLOSED"
    assert "frozen guard tripped" in run.families[fp.FAMILY_DAILY].error
    assert seen["miyagi"] == [{"date": "2026-10-02", "direction": "LONG"}]
    assert not run.healthy
    assert (pa.START, pa.END, pa._assert_control, pa.bracket_summary, pa.summarize_replay,
            pa.daily_audit.summary, pa.sustained_script.run, pa.REPO) == orig


def test_none_safe_only_fills_control_numbers():
    f = fp._none_safe(lambda: {"pf": None, "net": None, "fills": 0, "all": {"profit_factor": None}, "x": None})
    assert f() == {"pf": 0.0, "net": 0.0, "fills": 0, "all": {"profit_factor": 0.0}, "x": None}


def test_corpus_view_restricts_days(tmp_path):
    _tiny_corpus(tmp_path / "c", ["2026-09-01", "2026-09-02", "2026-09-03"])
    v = fp.corpus_view(tmp_path / "c", date(2026, 9, 2), date(2026, 9, 3), tmp_path / "v")
    assert fp.corpus_days(v) == ["2026-09-02", "2026-09-03"]


def test_observed_cme_days_uses_18et_roll_and_completion(tmp_path):
    _tiny_corpus(tmp_path / "c", ["2026-09-23", "2026-09-24", "2026-09-25"])
    got = fp.observed_cme_days(tmp_path / "c", datetime(2026, 9, 25, 23, 0, tzinfo=UTC))
    # 09-23 22:00Z..09-24 21:00Z -> obs 09-24 (complete at 09-24 22:00Z); obs 09-25 complete 09-25 22:00Z;
    # obs 09-26 (from 09-25 22:00Z) not complete.
    assert got["observed_completed_days"] == 2
    assert got["first_observed_day"] == "2026-09-24"


# ─── forward corpus builder ──────────────────────────────────────────────────

def test_parse_box_ts_accepts_iso_and_epoch_ms():
    assert parse_box_ts("2026-07-01T00:00:00+00:00") == 1782864000
    assert parse_box_ts("1782864000000") == 1782864000
    assert parse_box_ts(1782864000) == 1782864000
    with pytest.raises(BoxBarError):
        parse_box_ts("2026-07-01T00:00:00")


def _write_box(dir_: Path, day: str, rows: list[dict]):
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"bars_MNQ_{day}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _synthetic_box_5m(dir_: Path, start: datetime, n: int):
    by_day: dict[str, list[dict]] = {}
    px = 20000.0
    for i in range(n):
        t = start + timedelta(minutes=5 * i)
        o = px
        c = px + (1.0 if i % 3 else -0.75)
        by_day.setdefault(t.date().isoformat(), []).append({
            "ts": t.isoformat(), "open": o, "high": max(o, c) + 0.5, "low": min(o, c) - 0.5,
            "close": c, "volume": 100 + i % 7, "timeframe": "5m",
        })
        px = c
    for d, rows in by_day.items():
        _write_box(dir_, d, rows)


def test_load_box_bars_rejects_conflicts_and_wrong_timeframe(tmp_path):
    row = {"ts": "2026-07-01T00:00:00+00:00", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 1, "timeframe": "5m"}
    _write_box(tmp_path / "a", "2026-07-01", [row, dict(row)])
    assert load_box_bars(tmp_path / "a", 5).exact_duplicates_dropped == 1
    _write_box(tmp_path / "b", "2026-07-01", [row, {**row, "close": 1.75}])
    with pytest.raises(BoxBarError, match="conflicting"):
        load_box_bars(tmp_path / "b", 5)
    with pytest.raises(BoxBarError, match="timeframe"):
        load_box_bars(tmp_path / "a", 15)
    _write_box(tmp_path / "c", "2026-07-01", [{**row, "ts": "2026-07-01T00:02:00+00:00"}])
    with pytest.raises(BoxBarError, match="grid"):
        load_box_bars(tmp_path / "c", 5)


def test_build_uses_existing_derive_candles_and_writes_corpus_schema(tmp_path):
    from scripts.polygon_to_replay import derive_candles

    src = tmp_path / "box5"
    _synthetic_box_5m(src, datetime(2026, 3, 2, 0, 0, tzinfo=UTC), 3 * 288)
    out = tmp_path / "out"
    manifest = build(src, 5, out, allow_inside_repo=False)
    loaded = load_box_bars(src, 5)
    expected = derive_candles(loaded.bars, "MNQ", 5)
    written = []
    for p in sorted((out / "MNQ").glob("MNQ_*.jsonl")):
        written += [json.loads(line) for line in p.read_text().splitlines()]
    assert written == json.loads(json.dumps(expected))
    assert manifest["enrichment_path"] == "scripts.polygon_to_replay.derive_candles"
    assert manifest["derived_candles"] == len(expected) > 0
    for key in ("ema_9", "vwap", "orb_high", "market_condition", "reconstructed_atr14", "daily_bar_type"):
        assert key in written[-1]
    with pytest.raises(RuntimeError, match="not empty"):
        build(src, 5, out)


def test_build_refuses_to_write_inside_repo():
    from research.prereg929_forward_corpus import write_corpus

    with pytest.raises(RuntimeError, match="inside the repository"):
        write_corpus(REPO / "data" / "prereg929_should_not_exist", {"2026-01-01": [{"timestamp": "x"}]})


def test_split_by_utc_day_date_range():
    rows = [{"timestamp": f"2026-07-0{d}T01:00:00+00:00"} for d in (1, 2, 3)]
    assert list(split_by_utc_day(rows, start=date(2026, 7, 2))) == ["2026-07-02", "2026-07-03"]


# ─── step 0 checks ───────────────────────────────────────────────────────────

def _rth_series(day: str, minutes: int):
    t = datetime.fromisoformat(f"{day}T13:30:00+00:00")  # 09:30 EDT
    out = []
    while t < datetime.fromisoformat(f"{day}T20:00:00+00:00"):
        out.append(int(t.timestamp()))
        t += timedelta(minutes=minutes)
    return out


def _corpus_from(ts_list, root: Path, bump=None):
    leaf = root / "MNQ"
    leaf.mkdir(parents=True)
    by_day = {}
    for ts in ts_list:
        iso = datetime.fromtimestamp(ts, tz=UTC).isoformat()
        px = 100.0 + (bump or {}).get(ts, 0.0)
        by_day.setdefault(iso[:10], []).append({"timestamp": iso, "open": px, "high": px + 1, "low": px - 1, "close": px})
    for d, rows in by_day.items():
        (leaf / f"MNQ_{d}.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def _box_from(ts_list, minutes=5, bump=None):
    bars = []
    for ts in ts_list:
        px = 100.0 + (bump or {}).get(ts, 0.0)
        bars.append({"ts": ts, "open": px, "high": px + 1, "low": px - 1, "close": px, "volume": 1})
    return LoadedBars(minutes, bars)


def test_ohlc_parity_pass_with_one_tick_and_two_bar_gap(tmp_path):
    series = _rth_series("2026-07-14", 5)
    _corpus_from(series, tmp_path / "c")
    box = _box_from([t for i, t in enumerate(series) if i not in (10, 11)], bump={series[3]: 0.25})
    r = s0.ohlc_parity(box, tmp_path / "c", date(2026, 7, 14), date(2026, 7, 14), label="0a")
    assert r["verdict"] == "PASS"
    assert r["rth_gap_runs_le_2_bars"] == 1 and not r["rth_gap_violations_gt_2_bars"]


def test_ohlc_parity_fails_on_three_bar_rth_gap(tmp_path):
    series = _rth_series("2026-07-14", 5)
    _corpus_from(series, tmp_path / "c")
    box = _box_from([t for i, t in enumerate(series) if i not in (10, 11, 12)])
    r = s0.ohlc_parity(box, tmp_path / "c", date(2026, 7, 14), date(2026, 7, 14), label="0a")
    assert r["verdict"] == "FAIL"
    assert r["rth_gap_violations_gt_2_bars"][0]["bars"] == 3


def test_ohlc_parity_fails_below_match_rate(tmp_path):
    series = _rth_series("2026-07-14", 5)
    _corpus_from(series, tmp_path / "c")
    box = _box_from(series, bump={series[0]: 0.5})  # 1/78 bars off by 2 ticks -> 98.7%
    r = s0.ohlc_parity(box, tmp_path / "c", date(2026, 7, 14), date(2026, 7, 14), label="0a")
    assert r["verdict"] == "FAIL" and len(r["ohlc_mismatches"]) == 1


def test_compare_runs_lists_every_difference_and_explanations_gate():
    a = _run({fp.FAMILY_4HR: [_ev(fp.FAMILY_4HR, "4hr:1", "2026-07-14T14:00:00+00:00"),
                              _ev(fp.FAMILY_4HR, "4hr:2", "2026-07-15T14:00:00+00:00")]})
    moved = _ev(fp.FAMILY_4HR, "4hr:2", "2026-07-15T14:05:00+00:00")
    b = _run({fp.FAMILY_4HR: [moved, _ev(fp.FAMILY_4HR, "4hr:3", "2026-07-16T14:00:00+00:00")]},
             failed={fp.FAMILY_ST})
    mm = s0.compare_runs("0c", "frozen", a, "rebuilt", b, fp.ALL_FAMILIES)
    kinds = sorted(m["kind"] for m in mm)
    assert "ADAPTER_FAILED_CLOSED" in kinds
    assert "FIELD_DIFF" in kinds and "FILLABLE_ONLY_IN_FROZEN" in kinds and "FILLABLE_ONLY_IN_REBUILT" in kinds
    chk = s0.candidate_check("0c", mm, {}, sizes={})
    assert chk["verdict"] == "FAIL" and len(chk["unexplained"]) == len(mm)
    all_explained = s0.candidate_check("0c", mm, {m["id"]: "reviewed" for m in mm}, sizes={})
    assert all_explained["verdict"] == "PASS"
    identical = s0.compare_runs("0c", "frozen", a, "rebuilt", a, fp.ALL_FAMILIES)
    assert identical == []


def test_step0_report_validation_requires_all_four_checks(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"prereg": "929", "kind": "step0_parity", "overall": "PASS",
                             "checks": {k: {"verdict": "PASS"} for k in ("0a", "0b", "0c")}}))
    with pytest.raises(fp.LookRefused, match="missing"):
        fp.validate_step0_report(p)
    assert fp.validate_step0_report(_passing_step0(tmp_path))["overall"] == "PASS"
