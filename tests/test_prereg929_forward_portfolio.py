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
# scripts/mnq_combined_portfolio_audit.py is not in this map. AFS-0041 changes
# only its 60M_322_FIRST_LIVE control (33 fills / 2742.66 -> 33 fills / 2503.64,
# derived, pending corpus re-run). AFS-0051 adds the comment that cites the
# 322 erratum and the re-registered prereg. The 5a9f14b blob was
# c24f82ef60e4c70dcea46dcdc1e5dadf58ec6406. See
# docs/prereg-mnq-portfolio-ex-asia-forward-2026-09-23.md (superseded) and
# docs/prereg-mnq-portfolio-ex-asia-forward-2026-09-24-reregistered.md.
PORTED_BLOBS_5A9F14B = {
    "research/mnq_combined_portfolio_audit.py": "82ee6cfa3f356bba368ec7302036da1f111e1ba7",
    "tests/test_mnq_combined_portfolio_audit.py": "e29bde144c97d2eaeca0d31547c57d15cc716a73",
    "research/mnq_sustained_trend_continuation_v1.py": "289f0ab9aaf7c18b09de387bd131e6a59cade0d4",
    "scripts/mnq_sustained_trend_continuation_v1.py": "909fc10e4168b5956533dea1346e412015bae719",
}
AFS_0041_PORTFOLIO_SCRIPT = "scripts/mnq_combined_portfolio_audit.py"
AFS_0041_PORTFOLIO_SCRIPT_BLOB = "53df87ea161900867381c33a39ba9afce1e96384"


def _git_blob_sha(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


# ─── ported core is byte-identical to 5a9f14b ────────────────────────────────

@pytest.mark.parametrize("rel,blob", sorted(PORTED_BLOBS_5A9F14B.items()))
def test_ported_915_files_are_byte_identical_to_5a9f14b(rel, blob):
    assert _git_blob_sha(REPO / rel) == blob, f"{rel} drifted from 5a9f14b"


def test_portfolio_script_blob_is_the_afs_0041_control_update():
    assert _git_blob_sha(REPO / AFS_0041_PORTFOLIO_SCRIPT) == AFS_0041_PORTFOLIO_SCRIPT_BLOB


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

def _tiny_corpus(root: Path, days: list[str], *, source: str = "polygon", gap_days=()):
    leaf = root / "MNQ"
    leaf.mkdir(parents=True)
    (root / "MANIFEST_5m.json").write_text(json.dumps({"source": source, "gap_days": list(gap_days)}))
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



# ─── amendment 1 (§9): Polygon source, settlement, gap days, holiday class ──

from research import prereg929_forward_corpus as fc  # noqa: E402


def test_cli_refuses_a_non_polygon_corpus(tmp_path, capsys):
    import importlib

    cli = importlib.import_module("scripts.prereg929_forward_portfolio")
    days = ["2026-09-24"]
    _tiny_corpus(tmp_path / "c5", days, source="box")
    _tiny_corpus(tmp_path / "c15", days, source="box")
    rc = cli.main(["counts", "--corpus-5m", str(tmp_path / "c5"), "--corpus-15m", str(tmp_path / "c15")])
    assert rc == 3 and "polygon" in capsys.readouterr().err
    (tmp_path / "c5" / "MANIFEST_5m.json").unlink()
    with pytest.raises(RuntimeError, match="no Polygon corpus manifest"):
        fc.load_gap_days(tmp_path / "c5")


def test_forward_corpus_start_is_60_days_before_scoring():
    assert fc.FORWARD_CORPUS_START == fp.SCORING_START.date() - timedelta(days=60)
    assert fc.STEP0_WARMUP_DAYS == 60 and fc.POLYGON_PREROLL_DAYS == 10


def test_settled_cutoff_is_last_1700_et_close_at_least_24h_old():
    # 09-23 03:00Z -> limit 09-22 03:00Z (09-21 23:00 ET) -> close 09-21 17:00 ET.
    assert fc.settled_cutoff(datetime(2026, 9, 23, 3, 0, tzinfo=UTC)) == datetime(2026, 9, 21, 21, 0, tzinfo=UTC)
    # Exactly 24h after a close keeps that close.
    assert fc.settled_cutoff(datetime(2026, 9, 23, 21, 0, tzinfo=UTC)) == datetime(2026, 9, 22, 21, 0, tzinfo=UTC)


def _full_day_bars(day: date, tf: int, *, drop=()):
    lo, hi = fc.obs_day_window(day)
    out = []
    t = int(lo.timestamp())
    while t < int(hi.timestamp()):
        if t not in drop:
            out.append({"ts": t})
        t += tf * 60
    return out


def test_gap_days_regular_day_needs_every_slot_in_both_timeframes():
    d = date(2026, 9, 10)  # Thursday, regular
    b5, b15 = _full_day_bars(d, 5), _full_day_bars(d, 15)
    assert fc.detect_gap_days(b5, b15, d, d) == []
    hole = int(datetime(2026, 9, 10, 18, 0, tzinfo=UTC).timestamp())
    g = fc.detect_gap_days([b for b in b5 if b["ts"] != hole], b15, d, d)
    assert [x["obs_day"] for x in g] == ["2026-09-10"]
    assert g[0]["missing_5m"] == [["2026-09-10T18:00:00+00:00", "2026-09-10T18:05:00+00:00"]]
    assert g[0]["coverage_mismatch_15m_slots"] == ["2026-09-10T18:00:00+00:00"]
    # A truncated end of session (the real 2026-09-11 Polygon hole) is a gap.
    last_hour = int(datetime(2026, 9, 10, 20, 0, tzinfo=UTC).timestamp())
    cut5 = [b for b in b5 if b["ts"] < last_hour]
    cut15 = [b for b in b15 if b["ts"] < last_hour]
    assert fc.detect_gap_days(cut5, cut15, d, d)[0]["missing_15m"] == [
        ["2026-09-10T20:00:00+00:00", "2026-09-10T21:00:00+00:00"]]
    # An empty regular weekday is a gap; weekends are never checked.
    assert fc.detect_gap_days([], [], d, d)[0]["obs_day"] == "2026-09-10"
    assert fc.detect_gap_days([], [], date(2026, 9, 12), date(2026, 9, 13)) == []


def test_gap_days_reduced_schedule_checks_internal_holes_only():
    d = date(2026, 9, 7)  # Labor Day: early halt at 13:00 ET
    halt = int(datetime(2026, 9, 7, 17, 0, tzinfo=UTC).timestamp())
    b5 = [b for b in _full_day_bars(d, 5) if b["ts"] < halt]
    b15 = [b for b in _full_day_bars(d, 15) if b["ts"] < halt]
    assert fc._reduced_schedule(d) and not fc._reduced_schedule(date(2026, 9, 8))
    assert fc.detect_gap_days(b5, b15, d, d) == []
    mid = int(datetime(2026, 9, 7, 3, 0, tzinfo=UTC).timestamp())
    g = fc.detect_gap_days([b for b in b5 if b["ts"] != mid], b15, d, d)
    assert g and g[0]["reduced_schedule"] is True
    assert fc.detect_gap_days([], [], d, d) == []  # full closure


def test_drop_obs_days_removes_whole_observation_day():
    rows = [{"timestamp": t} for t in (
        "2026-09-10T21:55:00+00:00",  # 17:55 ET Thu -> obs 09-10 (break slot, still obs 09-10)
        "2026-09-10T22:00:00+00:00",  # 18:00 ET Thu -> obs 09-11
        "2026-09-11T20:55:00+00:00",  # 16:55 ET Fri -> obs 09-11
        "2026-09-13T22:00:00+00:00",  # Sun 18:00 ET -> obs 09-14
    )]
    kept = fc.drop_obs_days(rows, {"2026-09-11"})
    assert [r["timestamp"] for r in kept] == ["2026-09-10T21:55:00+00:00", "2026-09-13T22:00:00+00:00"]


def test_void_gap_fills_signal_fill_exit_or_open_across():
    gap = ["2026-10-02"]  # window 10-01 22:00Z .. 10-02 22:00Z
    on_day = _ev(fp.FAMILY_4HR, "a", "2026-10-02T14:00:00+00:00")
    across = _ev(fp.FAMILY_DAILY, "b", "2026-10-01T15:00:00+00:00", exit_ts="2026-10-05T15:00:00+00:00")
    before = _ev(fp.FAMILY_4HR, "c", "2026-10-01T14:00:00+00:00")
    after = _ev(fp.FAMILY_4HR, "d", "2026-10-02T22:00:00+00:00")
    still_open = _ev(fp.FAMILY_DAILY, "e", "2026-10-01T14:00:00+00:00")
    still_open = PortfolioEvent(**{**still_open.__dict__, "exit_ts": None, "result": "OPEN"})
    kept, void = fp.split_void_gap_fills([on_day, across, before, after, still_open], gap,
                                         as_of=datetime(2026, 10, 10, tzinfo=UTC))
    assert {e.source_id for e in void} == {"a", "b", "e"}
    assert {e.source_id for e in kept} == {"c", "d"}


def test_counts_report_reports_voids_and_excludes_them_from_terminal():
    fills = _many_fills(4)  # 09-24, 09-25, 09-26, 09-27 at 14:00Z
    rep = fp.counts_report(_run({fp.FAMILY_4HR: fills}), {"observed_completed_days": 3},
                           as_of=datetime(2026, 9, 30, tzinfo=UTC), gap_days=["2026-09-25"])
    assert rep["h1_counts"]["void_gap_day_fills"] == 1
    assert rep["h1_counts"]["terminal_portfolio_fills"] == 3
    assert rep["gap_days"]["gap_day_count"] == 1 and rep["gap_days"]["cap_exceeded"] is True  # 1/4 > 10%


def test_look_voids_gap_day_fills_and_insufficient_data_over_cap(tmp_path):
    fills = _many_fills(60)
    ok = fp.look_report(_run({fp.FAMILY_4HR: fills}), {"observed_completed_days": 130},
                        as_of=datetime(2027, 6, 1, tzinfo=UTC), step0_report_path=_passing_step0(tmp_path),
                        gap_days=["2026-09-25"])
    s7 = ok["section7"]
    assert s7["terminal_fills"] == 59
    assert s7["descriptive_table"]["void_gap_day_fills_by_family"] == {fp.FAMILY_4HR: 1}
    assert s7["gap_days"]["cap_exceeded"] is False
    many = [(date(2026, 9, 24) + timedelta(days=i)).isoformat() for i in range(0, 60, 3)]  # 20 gap days
    bad = fp.look_report(_run({fp.FAMILY_4HR: fills}), {"observed_completed_days": 130},
                         as_of=datetime(2027, 6, 1, tzinfo=UTC), step0_report_path=_passing_step0(tmp_path),
                         gap_days=many)
    assert bad["section7"]["gap_days"]["cap_exceeded"] is True  # 20/150 > 10%
    assert bad["section7"]["h1_verdict"] == "INSUFFICIENT_DATA"
    assert bad["section7"]["h2"]["verdict"] == "NOT CONFIRMED"


def test_holiday_boundary_class_is_narrow():
    mm = [
        {"id": "x1", "family": "F", "kind": "ATTEMPT_ONLY_IN_FROZEN", "ts": "2026-07-06T14:00:00+00:00"},
        {"id": "x2", "family": "F", "kind": "ATTEMPT_ONLY_IN_FROZEN", "ts": "2026-07-09T14:00:00+00:00"},
        {"id": "x3", "family": "F", "kind": "FIELD_DIFF",
         "frozen": {"signal_ts": "2026-06-22T14:00:00+00:00", "eligible_fill_ts": "2026-06-22T14:05:00+00:00"},
         "rebuilt": {"signal_ts": "2026-06-22T14:00:00+00:00", "eligible_fill_ts": "2026-06-22T14:05:00+00:00"}},
        {"id": "x4", "family": "F", "kind": "ADAPTER_FAILED_CLOSED", "ts": "2026-07-06T14:00:00+00:00"},
    ]
    diffs = {"2026-07-06": ["previous_day_low", "vwap"], "2026-06-22": ["daily_bar_type", "ftfc_aligned"]}
    assert set(s0.holiday_boundary_explanations(mm, diffs)) == {"x1", "x3"}
    # Any non-boundary difference anywhere disables the class entirely.
    assert s0.holiday_boundary_explanations(mm, {**diffs, "2026-07-14": ["ema_200"]}) == {}
    assert s0.holiday_boundary_explanations(mm, {**diffs, "2026-07-06": ["close"]}) == {}
    # A listed day with no recorded diff is not explained.
    assert s0.holiday_boundary_explanations(mm, {"2026-06-22": ["vwap"]}) == {"x3": s0.HOLIDAY_CLASS_TEXT}


def test_corpus_field_diffs_by_day(tmp_path):
    for name, low in (("a", 1.0), ("b", 0.5)):
        leaf = tmp_path / name / "MNQ"
        leaf.mkdir(parents=True)
        rows = [{"timestamp": "2026-07-06T14:00:00+00:00", "open": 2, "previous_day_low": low},
                {"timestamp": "2026-07-07T14:00:00+00:00", "open": 2, "previous_day_low": 1.0}]
        for r in rows:
            (leaf / f"MNQ_{r['timestamp'][:10]}.jsonl").write_text(json.dumps(r) + "\n")
    got = s0.corpus_field_diffs(tmp_path / "a", tmp_path / "b", date(2026, 7, 1), date(2026, 7, 31))
    assert got == {"2026-07-06": ["previous_day_low"]}


class _FakeBar:
    def __init__(self, ts, o, h, l, c, v):
        self.ts, self.open, self.high, self.low, self.close, self.volume = ts, o, h, l, c, v


class _FakeClient:
    configured = True

    def __init__(self, start: datetime, days: int, drop: set[int] = frozenset()):
        self.start, self.days, self.drop = start, days, drop
        self.calls = []

    def fetch_continuous(self, symbol, start, end, tf, roll_days=8):
        self.calls.append((symbol, start, end, tf, roll_days))
        out, px = [], 20000.0
        t = self.start
        while t < self.start + timedelta(days=self.days):
            et = t.astimezone(fp.ET)
            trading = not (et.weekday() == 5 or (et.weekday() == 6 and et.hour < 18)
                           or (et.weekday() == 4 and et.hour >= 17) or et.hour == 17)
            if trading and int(t.timestamp()) not in self.drop:
                c = px + (1.0 if (t.minute // tf) % 3 else -0.75)
                out.append(_FakeBar(t, px, max(px, c) + 0.5, min(px, c) - 0.5, c, 100.0))
                px = c
            t += timedelta(minutes=tf)
        return out


def test_build_polygon_corpus_settles_removes_gap_days_and_records_hashes(tmp_path):
    hole = int(datetime(2026, 9, 10, 18, 0, tzinfo=UTC).timestamp())
    client = _FakeClient(datetime(2026, 8, 30, 22, 0, tzinfo=UTC), 14, drop={hole})
    res = fc.build_polygon_corpus(
        tmp_path / "out", date(2026, 9, 8), preroll_days=10,
        fetched_at=datetime(2026, 9, 13, 12, 0, tzinfo=UTC), client=client,
    )
    fw = res["forward"]
    assert fw["settled_cutoff"] == "2026-09-11T21:00:00+00:00"  # Fri 09-11 close is > 24h old
    assert [g["obs_day"] for g in fw["gap_days"]] == ["2026-09-10"]
    assert all(call[4] == 8 and call[1] == date(2026, 8, 29) for call in client.calls)
    for tf in (5, 15):
        rows = [json.loads(line) for f in sorted((tmp_path / "out" / f"{tf}m" / "MNQ").glob("*.jsonl"))
                for line in f.read_text().splitlines()]
        stamps = [r["timestamp"] for r in rows]
        assert stamps[0][:10] >= "2026-09-08"
        assert not [t for t in stamps if "2026-09-09T22:00" <= t < "2026-09-10T22:00"]  # obs day 09-10 removed
        assert max(stamps) < "2026-09-11T21:00"  # settlement cutoff
        assert fc.load_gap_days(tmp_path / "out" / f"{tf}m") == ["2026-09-10"]
        man = res["timeframes"][f"{tf}m"]
        assert len(man["raw_sha256_after_settlement"]) == 64 and man["candles_removed_as_gap_days"] > 0
    assert json.loads((tmp_path / "out" / "FORWARD_MANIFEST.json").read_text())["source"] == "polygon"
