"""Tests for the structural-level P2 read-only tooling (P2-X extractor, P2-P parity,
R1/R2 corpus build wrapper). Synthetic journals and corpora only — no network, no runtime.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research import structural_level_p2 as p2  # noqa: E402
from strategy.shadow_resolver import _candidate_key  # noqa: E402

UTC = timezone.utc
DAY = date(2026, 8, 12)


def _ts(h: int, m: int = 0, d: date = DAY) -> datetime:
    return datetime(d.year, d.month, d.day, h, m, tzinfo=UTC)


def _cand(strategy="strat_22_continuation_observed", direction="LONG", entry=100.0, stop=98.0,
          target=104.0, outcome=None):
    c = {"strategy": strategy, "direction": direction, "entry": entry, "stop": stop, "target": target,
         "rr_ratio": 2.0, "risk_tier": "B", "size_multiplier": 0.5, "notes": "n"}
    if outcome is not None:
        c["outcome"] = outcome
    return c


def _outcome(result="WIN"):
    return {"result": result, "entry_filled": True, "exit_reason": "target", "exit_price": 104.0,
            "pnl_ticks": 16.0, "bars_to_fill": 1, "bars_to_exit": 3, "fill_bar_target_ambiguous_ignored": False}


def _replay_row(inst, ts: datetime, cands, session="new_york", decision="NO_TRADE"):
    return {"ts": "2026-09-17T00:00:00+00:00", "decision": decision, "instrument": inst, "session": session,
            "market_condition": "TRENDING", "bar_ts": ts.isoformat(), "shadow_candidates": cands}


def _live_row(inst, ts: datetime, cands, session="new_york", tf=15):
    return {"ts": (ts + timedelta(minutes=15, seconds=9)).isoformat(), "decision": "NO_TRADE",
            "instrument": inst, "session": session, "timeframe_minutes": tf,
            "context": {"timestamp": ts.isoformat(), "instrument": inst, "session": session},
            "shadow_candidates": cands}


def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def _corpus(tmp: Path, inst="MNQ", day: date = DAY, n=20, start_h=13, start_m=30) -> Path:
    """UTC-day file with n 15m bars starting 13:30Z (= 09:30 ET) + MANIFEST with one seam."""
    d = tmp / "corpus" / inst
    d.mkdir(parents=True, exist_ok=True)
    rows = []
    t = _ts(start_h, start_m, day)
    for i in range(n):
        rows.append({"timestamp": t.isoformat(), "instrument": inst, "session": "new_york",
                     "open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100.5 + i, "volume": 10,
                     "reconstructed_market_condition": "TRENDING", "legacy_market_condition": "CHOPPY"})
        t += timedelta(minutes=15)
    _write_jsonl(d / f"{inst}_{day.isoformat()}.jsonl", rows)
    (d / "MANIFEST.json").write_text(json.dumps({
        "source": {"contract_segments": [["MNQM6", "2026-06-01", "2026-06-10"], ["MNQU6", "2026-06-11", "2026-09-14"]]},
        "roll_ledger": [{"from": "MNQM6", "to": "MNQU6", "gap_points": 277.5}],
        "gap_ledger_cme_hours": [],
    }))
    return tmp / "corpus"


# ── identity / outcome seal ───────────────────────────────────────────────────

def test_candidate_key_matches_resolver():
    c = _cand(entry=7754.25)
    k = p2.candidate_key("MES", "2026-08-11T23:45:00+00:00", c)
    assert k == _candidate_key("shadow_setups", "MES", "2026-08-11T23:45:00+00:00",
                               "strat_22_continuation_observed", "LONG", 7754.25)


def test_split_outcome_never_mutates_and_removes_block():
    c = _cand(outcome=_outcome())
    c2, o = p2.split_outcome(c)
    assert "outcome" not in c2 and o == _outcome() and "outcome" in c


def test_extract_writes_candidates_without_outcome_and_sealed_outcomes(tmp_path):
    corpus = _corpus(tmp_path)
    log = tmp_path / "replay"
    rows = [
        _replay_row("MNQ", _ts(13, 30), [_cand(outcome=_outcome("WIN")),
                                         _cand("ema_pullback_trend", "SHORT", 101, 103, 97, _outcome("LOSS"))]),
        _replay_row("MNQ", _ts(13, 45), []),
        _replay_row("MNQ", _ts(14, 0), [_cand(entry=102.0, outcome=_outcome("NO_FILL"))]),
    ]
    _write_jsonl(log / "journal_2026-08-12.jsonl", rows)
    out = tmp_path / "out"
    m = p2.extract(str(log), str(corpus), str(out), instruments=("MNQ",))
    cands = [json.loads(l) for l in (out / "candidates.jsonl").read_text().splitlines()]
    assert len(cands) == 3
    for c in cands:
        assert "outcome" not in c and "result" not in json.dumps(c)
        assert c["candidate_key"].startswith("shadow_setups|MNQ|2026-08-12T")
        assert c["reconstructed_market_condition"] == "TRENDING"
        assert c["contract"] == "MNQU6" and c["candle_present_in_corpus"] is True
    sealed = [json.loads(l) for l in (out / "outcomes.sealed.jsonl").read_text().splitlines()]
    assert [s["outcome"]["result"] for s in sealed] == ["WIN", "LOSS", "NO_FILL"]
    assert {s["candidate_key"] for s in sealed} == {c["candidate_key"] for c in cands}
    assert m["outcomes_sealed_file"]["sha256"] == p2._sha256_path(str(out / "outcomes.sealed.jsonl"))
    assert m["candidates_file"]["sha256"] == p2._sha256_path(str(out / "candidates.jsonl"))
    assert m["integrity"]["status"] == "PASS"
    assert m["integrity"]["evaluated_bars_by_instrument"] == {"MNQ": 3}
    assert m["integrity"]["bars_in_corpus_not_evaluated_by_replay"] == {"MNQ": 17}
    assert m["integrity"]["roll_ledger"]["MNQ"][0]["gap_points"] == 277.5
    assert cands[0]["corpus_day_position"] == 0 and cands[0]["recent_bars_warmup"] is True
    assert cands[2]["corpus_day_position"] == 2
    assert (out / "manifest.json").exists()


def test_extract_integrity_only_never_writes_outcomes(tmp_path):
    corpus = _corpus(tmp_path)
    log = tmp_path / "replay"
    _write_jsonl(log / "journal_2026-08-12.jsonl",
                 [_replay_row("MNQ", _ts(13, 30), [_cand(outcome=_outcome())])])
    out = tmp_path / "out"
    m = p2.extract(str(log), str(corpus), str(out), integrity_only=True)
    assert not (out / "outcomes.sealed.jsonl").exists()
    assert m["outcomes_sealed_file"] is None and m["integrity"]["status"] == "PASS"
    assert "outcome" not in (out / "candidates.jsonl").read_text()


def test_extract_duplicates_and_forbidden_families_fail_closed(tmp_path):
    corpus = _corpus(tmp_path)
    log = tmp_path / "replay"
    same = _cand(outcome=_outcome())
    conflict = _cand(stop=97.0, outcome=_outcome())     # same key (entry), different bracket
    rows = [
        _replay_row("MNQ", _ts(13, 30), [same, dict(same)]),                  # exact duplicate
        _replay_row("MNQ", _ts(13, 30), [conflict]),                          # conflicting duplicate
        _replay_row("MNQ", _ts(13, 45), [_cand("orb_false_break_fade")], session="asian"),
        _replay_row("MNQ", _ts(14, 0), [_cand("vwap_hold_observed")]),
    ]
    _write_jsonl(log / "journal_2026-08-12.jsonl", rows)
    m = p2.extract(str(log), str(corpus), str(tmp_path / "out"), integrity_only=True)
    i = m["integrity"]
    assert i["status"] == "BLOCKED"
    assert i["exact_duplicate_keys_collapsed"] == {"strat_22_continuation_observed": 1}
    assert i["conflicting_duplicate_keys"] == {"strat_22_continuation_observed": 1}
    assert i["families_blocked"] == ["strat_22_continuation_observed"]
    assert i["asian_orb_false_break_fade_rows"] == 1
    assert i["forbidden_families_present"] == {"vwap_hold_observed": 1}
    assert len(i["problems"]) == 3


def test_end_ts_exclusive_drops_roll_cut_rows(tmp_path):
    corpus = _corpus(tmp_path)
    log = tmp_path / "replay"
    _write_jsonl(log / "journal_2026-08-12.jsonl", [
        _replay_row("MNQ", _ts(13, 30), [_cand(outcome=_outcome())]),
        _replay_row("MNQ", _ts(14, 0), [_cand(entry=102, outcome=_outcome())]),
    ])
    m = p2.extract(str(log), str(corpus), str(tmp_path / "out"), integrity_only=True,
                   end_ts_exclusive=_ts(14, 0))
    assert m["integrity"]["unique_candidate_keys"] == 1


# ── parity ────────────────────────────────────────────────────────────────────

def _parity_fixture(tmp_path, *, replay_entry_offset=0.0, live_extra=None, replay_extra=None,
                    corpus_n=20):
    corpus = _corpus(tmp_path, n=corpus_n)
    live_root = tmp_path / "live"; rlog = tmp_path / "replay"
    fam = "strat_22_continuation_observed"
    # 10 shared bars 13:30..15:45; live fires on bars 0..8, replay on 1..9 (with an offset on entry)
    live_rows, replay_rows = [], []
    for i in range(10):
        t = _ts(13, 30) + timedelta(minutes=15 * i)
        lc = [_cand(fam, entry=100.0 + i)] if i <= 8 else []
        rc = [_cand(fam, entry=100.0 + i + replay_entry_offset, outcome=_outcome())] if i >= 1 else []
        live_rows.append(_live_row("MNQ", t, lc))
        replay_rows.append(_replay_row("MNQ", t, rc))
    # live-only bar (feed had it, replay did not evaluate): 16:00Z
    live_rows.append(_live_row("MNQ", _ts(16, 0), [_cand(fam, entry=200)]))
    # stale asian ORB fade on live (excluded, counted); a 5m row (ignored)
    live_rows.append(_live_row("MNQ", _ts(1, 0), [_cand("orb_false_break_fade")], session="asian"))
    live_rows.append(_live_row("MNQ", _ts(13, 35), [_cand(fam, entry=999)], tf=5))
    live_rows += live_extra or []
    replay_rows += replay_extra or []
    _write_jsonl(live_root / "journal_2026-08-12.jsonl", live_rows)
    _write_jsonl(rlog / "journal_2026-08-12.jsonl", replay_rows)
    return corpus, live_root, rlog


def test_parity_jaccard_bracket_and_bar_census(tmp_path):
    corpus, live_root, rlog = _parity_fixture(tmp_path)
    live = list(p2.iter_live_rows(str(live_root), start_date="2026-07-16"))
    replay = list(p2.iter_replay_rows(str(rlog)))
    assert all("outcome" not in c for r in replay for c in r.candidates)
    assert all(r.outcomes for r in replay if r.candidates)
    rep = p2.compute_parity(live, replay, corpus={"MNQ": p2.load_corpus_bars(str(corpus / "MNQ"))})
    fam = rep["families"]["strat_22_continuation_observed"]
    b = fam["on_both_evaluated_bars"]
    assert b["live_firings"] == 9 and b["replay_firings"] == 9 and b["intersection"] == 8 and b["union"] == 10
    assert b["firing_jaccard"] == 0.8
    assert b["live_only_firings"] == 1 and b["replay_only_firings"] == 1
    assert b["live_only_on_replay_warmup_bars"] == 1     # bar 0 is corpus day position 0
    assert fam["bracket_on_cofired"]["pairs"] == 8
    assert fam["bracket_on_cofired"]["all_three_within_one_tick_rate"] == 1.0
    assert fam["classification"] == "BOTH — input-divergent"
    assert fam["live_firings_all_live_bars"] == 10       # 9 shared + the 16:00Z live-only bar
    census = rep["bar_census"]["MNQ"]
    assert census == {"live_bars": 12, "replay_bars": 10, "both_evaluated": 10, "live_only_bars": 2,
                      "replay_only_bars": 0, "live_only_bars_absent_from_corpus": 1,
                      "live_only_bars_in_corpus_not_evaluated_by_replay": 1, "corpus_bars": 20}
    assert rep["live_stale_orb_rows_excluded"] == 1
    assert rep["families"]["orb_false_break_fade"]["live_firings_all_live_bars"] == 0
    assert rep["families"]["gap_fill"]["classification"] == "DEAD"
    assert rep["families"]["vwap_hold_observed"]["classification"] == "LIVE_ONLY"
    assert rep["families"]["transition_failed_breakdown_reclaim"]["classification"] == "NOT_TESTABLE"
    assert rep["gate_failures"] == []
    assert fam["ruled_disposition"] is None
    assert rep["families"]["ema_pullback_trend"]["ruled_disposition"].startswith("REPLAY_ONLY + LIVE_ONLY")


def test_parity_bracket_conflict_when_firing_agrees_but_bracket_off_by_two_ticks(tmp_path):
    corpus, live_root, rlog = _parity_fixture(tmp_path, replay_entry_offset=0.5)   # MNQ tick 0.25
    live = list(p2.iter_live_rows(str(live_root), start_date="2026-07-16"))
    replay = list(p2.iter_replay_rows(str(rlog)))
    rep = p2.compute_parity(live, replay)
    fam = rep["families"]["strat_22_continuation_observed"]
    # entries differ by 0.5 → different firing keys? No: firing key ignores entry — same bars fire.
    assert fam["on_both_evaluated_bars"]["firing_jaccard"] == 0.8
    assert fam["bracket_on_cofired"]["all_three_within_one_tick_rate"] == 0.0
    assert fam["bracket_on_cofired"]["entry_rate"] == 0.0 and fam["bracket_on_cofired"]["stop_rate"] == 1.0
    assert len(fam["bracket_on_cofired"]["failing_examples"]) == 5


def test_parity_classifications_both_and_bracket_conflict():
    """Direct classifier checks for the frozen gates."""
    assert p2._classify("strat_312_observed", 10, 10, 0.95, 1.0, 10, 10)[0] == "BOTH"
    assert p2._classify("strat_312_observed", 10, 10, 0.95, 0.9, 10, 10)[0] == "BOTH — BRACKET_CONFLICT"
    assert p2._classify("strat_312_observed", 10, 10, 0.89, 1.0, 10, 10)[0] == "BOTH — input-divergent"
    assert p2._classify("strat_312_observed", 10, 0, 0.0, None, 10, 0)[0] == "LIVE_ONLY"
    assert p2._classify("strat_312_observed", 0, 10, 0.0, None, 0, 10)[0] == "REPLAY_ONLY"
    assert p2._classify("strat_312_observed", 0, 0, None, None, 0, 0)[0] == "ABSENT"
    assert p2._classify("gap_fill", 0, 0, None, None, 0, 0)[0] == "DEAD"
    assert p2._classify("gap_fill", 0, 0, None, None, 1, 0)[0].startswith("MANIFEST_ERROR")
    assert p2._classify("vwap_hold_observed", 0, 0, None, None, 155, 0)[0] == "LIVE_ONLY"
    assert p2._classify("vwap_hold_observed", 0, 0, None, None, 155, 3)[0].startswith("MANIFEST_ERROR")


def test_parity_live_only_family_in_replay_is_gate_failure(tmp_path):
    corpus, live_root, rlog = _parity_fixture(
        tmp_path, replay_extra=[_replay_row("MNQ", _ts(20, 0), [_cand("vwap_hold_observed", outcome=_outcome())])])
    live = list(p2.iter_live_rows(str(live_root), start_date="2026-07-16"))
    replay = list(p2.iter_replay_rows(str(rlog)))
    rep = p2.compute_parity(live, replay)
    assert any(g.startswith("vwap_hold_observed: MANIFEST_ERROR") for g in rep["gate_failures"])


def test_live_loader_uses_context_timestamp_and_roll_cut(tmp_path):
    root = tmp_path / "live"
    _write_jsonl(root / "journal_2026-09-14.jsonl", [
        _live_row("MES", datetime(2026, 9, 14, 21, 45, tzinfo=UTC), [_cand()]),
        _live_row("MES", datetime(2026, 9, 14, 22, 0, tzinfo=UTC), [_cand()]),    # first Z6 bar → dropped
    ])
    _write_jsonl(root / "journal_2026-07-15.jsonl", [_live_row("MES", _ts(13, 30, date(2026, 7, 15)), [_cand()])])
    rows = list(p2.iter_live_rows(str(root), start_date="2026-07-16"))
    assert [r.bar_ts for r in rows] == [datetime(2026, 9, 14, 21, 45, tzinfo=UTC)]
    assert rows[0].source == "live" and rows[0].outcomes == [None]


def test_determinism_check_identical_and_differing(tmp_path):
    a = tmp_path / "a"; b = tmp_path / "b"; c = tmp_path / "c"
    rows = [_replay_row("MNQ", _ts(13, 30), [_cand(outcome=_outcome("WIN"))])]
    _write_jsonl(a / "journal_2026-08-12.jsonl", rows)
    _write_jsonl(b / "journal_2026-08-12.jsonl", rows)
    _write_jsonl(c / "journal_2026-08-12.jsonl", [_replay_row("MNQ", _ts(13, 30), [_cand(outcome=_outcome("LOSS"))])])
    assert p2.determinism_check(str(a), str(b))["identical"] is True
    d = p2.determinism_check(str(a), str(c))
    assert d["identical"] is False and d["rows_differ"] == 1


def test_orb_availability_counts_missing_canonical_bars():
    corpus = {}
    # Day A: full NY + London canonical bars; Day B: 09:30 ET missing, 03:00 ET missing
    for day, has in ((date(2026, 8, 12), True), (date(2026, 8, 13), False)):
        for h, m in ((7, 0), (7, 15), (13, 30), (13, 45), (14, 0)):    # 03:00 ET = 07:00Z; 09:30 ET = 13:30Z (EDT)
            if not has and (h, m) in ((7, 0), (13, 30)):
                continue
            corpus[_ts(h, m, day)] = {}
    a = p2._orb_availability(corpus)
    assert a["ny_session_days"] == 2 and a["ny_orb_not_available_days"] == ["2026-08-13"]
    assert a["london_session_days"] == 2 and a["london_orb_not_available_days"] == ["2026-08-13"]


# ── corpus build wrapper ──────────────────────────────────────────────────────

def test_corpus_build_writes_pinned_schema_manifest_and_roll_ledger(tmp_path, monkeypatch):
    from scripts import structural_level_corpus_build as cb
    from sources.polygon_client import PolygonBar, contract_schedule

    start, end = date(2026, 6, 9), date(2026, 6, 12)   # spans the 2026-06-11 MNQ roll (8-day rule)
    segs = contract_schedule("MNQ", start - timedelta(days=1), end, 8)
    assert [s[0] for s in segs] == ["MNQM6", "MNQU6"] and segs[1][1] == date(2026, 6, 11)

    class FakeClient:
        configured = True

        def fetch_continuous(self, symbol, s, e, tf=15, roll_days=8):
            bars = []
            t = datetime(s.year, s.month, s.day, 0, 0, tzinfo=UTC)
            end_dt = datetime(e.year, e.month, e.day, 23, 45, tzinfo=UTC)
            px = 1000.0
            while t <= end_dt:
                if cb._slot_open(t):
                    if t.date() == date(2026, 6, 11) and t.hour == 0 and t.minute == 0:
                        px += 277.75                      # open − prior close = 277.5 (the known roll gap)
                    bars.append(PolygonBar(t, px, px + 1, px - 1, px + 0.5, 100.0, "X"))
                    px += 0.25
                t += timedelta(minutes=15)
            return bars

    out_root = tmp_path / "v2"
    m = cb.build(symbol="MNQ", start=start, end=end, timeframe=15, warmup_days=1, roll_days=8,
                 out_root=out_root, end_ts_exclusive=datetime(2026, 6, 12, 20, 0, tzinfo=UTC),
                 client=FakeClient(), corpus_label="test")
    files = sorted(p.name for p in (out_root / "MNQ").glob("*.jsonl"))
    assert files[0] == "MNQ_2026-06-09.jsonl" and "MNQ_2026-06-08.jsonl" not in files   # warm-up dropped
    row = json.loads((out_root / "MNQ" / files[-1]).read_text().splitlines()[-1])
    for k in cb.REQUIRED_FIELDS:
        assert k in row
    assert row["timestamp"] < "2026-06-12T20:00:00"        # end cut applied
    assert m["source"]["roll_rule"].startswith("roll_days=8") and "[DEFAULT_ROLL_DAYS]" in m["source"]["roll_rule"]
    assert m["source"]["contract_segments"][1][0] == "MNQU6"
    roll = [r for r in m["roll_ledger"] if r["to"] == "MNQU6"][0]
    assert roll["roll_utc_date"] == "2026-06-11" and abs(roll["gap_points"] - 277.5) < 1e-6
    assert m["gap_ledger_cme_hours"] == []                  # fake feed had every open slot
    assert set(m["builder"]["file_sha256"]) == set(cb.PINNED_FILES)
    assert m["coverage"]["files"] == len(files) == len(m["files"])
    manifest = json.loads((out_root / "MNQ" / "MANIFEST.json").read_text())
    assert manifest["files"][files[0]]["sha256"] == cb.sha256_file(out_root / "MNQ" / files[0])
    # a second build into the same directory is refused unless --fresh (no stale days beside new)
    with pytest.raises(SystemExit):
        cb.build(symbol="MNQ", start=start, end=end, timeframe=15, warmup_days=1, roll_days=8,
                 out_root=out_root, end_ts_exclusive=None, client=FakeClient())
    m2 = cb.build(symbol="MNQ", start=date(2026, 6, 10), end=end, timeframe=15, warmup_days=1, roll_days=8,
                  out_root=out_root, end_ts_exclusive=None, client=FakeClient(), fresh=True)
    assert sorted(p.name for p in (out_root / "MNQ").glob("*.jsonl"))[0] == "MNQ_2026-06-10.jsonl"
    assert m2["coverage"]["files"] == len(list((out_root / "MNQ").glob("*.jsonl")))


def test_gap_ledger_reports_missing_open_slots_only():
    from scripts.structural_level_corpus_build import gap_ledger
    # Wed 2026-08-12 13:30Z.. with a 45-min hole at 14:00-14:30Z; the 21:00-22:00Z halt is not a gap
    ts = [_ts(13, 30), _ts(13, 45), _ts(14, 45), _ts(15, 0), _ts(20, 45), _ts(22, 0)]
    runs = gap_ledger(ts, 15)
    assert runs[0] == {"first_missing": _ts(14, 0).isoformat(), "last_missing": _ts(14, 30).isoformat(),
                       "slots": 3, "minutes": 45}
    # 15:15..20:30 is one long run (22 slots); nothing between 20:45 and 22:00 (halt) is counted
    assert [r["minutes"] for r in runs] == [45, 330]


def test_no_runtime_imports():
    for rel in ("research/structural_level_p2.py", "scripts/structural_level_p2_extract.py",
                "scripts/structural_level_p2_parity.py", "scripts/structural_level_corpus_build.py"):
        imports = [l.strip() for l in (ROOT / rel).read_text().splitlines()
                   if l.startswith(("from ", "import "))]
        for line in imports:
            for bad in ("webhook", "execution", "broker", "replay", "risk", "tradovate", "adaptive"):
                assert not line.startswith((f"from {bad}", f"import {bad}")), f"{rel}: {line}"
