"""Regression tests for scripts/context_permission_prereg_review.py (AUDIT / RESEARCH ONLY).

Tiny synthetic fixtures prove: join-key parity with strategy.shadow_resolver, duplicate
SHADOW_OUTCOME handling (first wins, counted), 5m-timeframe and missing-context exclusions
(counted, never dropped silently), feed-gap flags, the pinned cost / R math, the F1-F12
encodings, Holm over the fixed family, the §16 seeded spot-check, deterministic output,
and that importing the tool pulls in no broker / execution / runner / replay / risk module.
"""
from __future__ import annotations

import importlib.util
import json
import math
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "context_permission_prereg_review.py"


def _load():
    spec = importlib.util.spec_from_file_location("cpr_review", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


cpr = _load()
UTC = timezone.utc
DAY = "2026-08-03"
T0 = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)   # 08:00 ET, a Monday — inside CME hours


def _loc(price, *, middle=True, rel1h="middle", rel4h="middle", tests4h=0, dist_key=5.0,
         mtr=20.0, onh=None, onl=None, other=None, agree=None, phase="pre_impulse", regime="RANGE_BOUND"):
    zone4h = {"kind": "supply", "top": price + 100, "bottom": price + 90, "tests": tests4h,
              "fresh": tests4h == 0, "broken": False, "distance_points": 90.0, "timeframe_minutes": 240}
    levels = {"pdh": price + 50, "pdl": price - 50}
    if onh is not None:
        levels.update({"onh": onh, "onl": onl})
    return {
        "observed_price": price, "levels": levels,
        "nearest_key_level": {"name": "pdh", "level": price + dist_key, "distance_points": dist_key},
        "zones": {"1h": {"supply": None, "demand": None, "relation": rel1h},
                  "4h": {"supply": zone4h, "demand": None, "relation": rel4h}},
        "middle_of_range": middle, "regime_at_signal": regime, "other_instrument": other,
        "regime_agreement": agree, "impulse": {"phase": phase}, "mtr_15m_points": mtr,
    }


def _row(bar_dt, *, price, cand, loc=None, tf=15, mc="RANGE_BOUND", ctx_mc=None, extra=None):
    row_ts = (bar_dt + timedelta(minutes=15, seconds=1)).isoformat()
    ctx = {"timestamp": bar_dt.isoformat(), "market_condition": ctx_mc or mc}
    if loc is not None:
        ctx["location_context"] = loc
    r = {"ts": row_ts, "instrument": "MNQ", "session": "london", "market_condition": mc,
         "decision": "NO_TRADE", "timeframe_minutes": tf, "context": ctx, "shadow_candidates": [cand]}
    if extra:
        r.update(extra)
    return r


def _cand(entry, stop, target, direction="LONG", strategy="strat_22_continuation_observed", location=None):
    return {"strategy": strategy, "direction": direction, "entry": entry, "stop": stop, "target": target,
            "location": location or {"direction_zone_alignment": "aligned", "target_blocked_by_opposing_zone": False}}


def _outcome(row, cand, result, pnl_ticks, resolved_dt):
    key = f"shadow_setups|MNQ|{row['ts']}|{cand['strategy']}|{cand['direction']}|{cand['entry']}"
    return {"ts": resolved_dt.isoformat(), "type": "SHADOW_OUTCOME", "instrument": "MNQ", "lane": "shadow_setups",
            "candidate_key": key, "candidate_bar_ts": row["ts"], "resolved_at_bar_ts": resolved_dt.isoformat(),
            "forward_bars_used": 4, "final": True,
            "shadow_outcome": {"result": result, "entry_filled": result in ("WIN", "LOSS"),
                               "pnl_ticks": pnl_ticks, "exit_reason": None}}


@pytest.fixture
def logs_root(tmp_path: Path) -> Path:
    """60 resolved 15m candidates on a bar grid with one 45-minute hole, plus the edge cases."""
    root = tmp_path / "logs"
    root.mkdir()
    journal, bars = [], []
    # bars: 12:00 .. 12:00+72*15m, drop three bars at 14:00, 14:15, 14:30 (a 45-min gap)
    for i in range(73):
        t = T0 + timedelta(minutes=15 * i)
        if t.hour == 14 and t.minute in (0, 15, 30):
            continue
        bars.append({"ts": t.isoformat(), "open": 29000.0, "high": 29010.0 + i, "low": 28990.0 - i,
                     "close": 29000.0, "volume": 1000.0, "timeframe": "15"})
    # 60 candidates: middle_of_range alternates; winners on edge bars, losers on middle bars
    for i in range(60):
        bar_dt = T0 + timedelta(minutes=15 * i)
        middle = i % 2 == 0
        price = 29000.0 + i
        loc = _loc(price, middle=middle, onh=price + 30 if i % 3 else None, onl=price - 30 if i % 3 else None,
                   other={"instrument": "MES", "market_condition": "TRENDING", "age_seconds": 60} if i % 4 == 0 else None,
                   agree=True if i % 4 == 0 else None, phase="late_entry" if i % 5 == 0 else "pre_impulse",
                   regime="TRENDING" if i % 6 == 0 else "RANGE_BOUND", tests4h=i % 2, rel1h="inside_demand" if i % 7 == 0 else "middle")
        cand = _cand(price, price - 12.5, price + 25.0)           # 50-tick stop
        row = _row(bar_dt, price=price, cand=cand, loc=loc, mc="TRENDING" if i % 6 == 0 else "RANGE_BOUND")
        journal.append(row)
        result = "LOSS" if middle else "WIN"
        journal.append(_outcome(row, cand, result, -50.0 if result == "LOSS" else 100.0, bar_dt + timedelta(minutes=60)))
    # edge cases -----------------------------------------------------------------------
    t = T0 + timedelta(minutes=15 * 60)
    dup_cand = _cand(29500.0, 29487.5, 29525.0)
    dup_row = _row(t, price=29500.0, cand=dup_cand, loc=_loc(29500.0))
    journal.append(dup_row)
    journal.append(_outcome(dup_row, dup_cand, "WIN", 100.0, t + timedelta(minutes=30)))     # first wins
    journal.append(_outcome(dup_row, dup_cand, "LOSS", -50.0, t + timedelta(minutes=45)))    # duplicate key, ignored
    t5 = T0 + timedelta(minutes=15 * 61)
    c5 = _cand(29600.0, 29587.5, 29625.0)
    r5 = _row(t5, price=29600.0, cand=c5, loc=_loc(29600.0), tf=5)                            # 5m contamination
    journal += [r5, _outcome(r5, c5, "WIN", 100.0, t5 + timedelta(minutes=30))]
    t6 = T0 + timedelta(minutes=15 * 62)
    c6 = _cand(29700.0, 29687.5, 29725.0)
    r6 = _row(t6, price=29700.0, cand=c6, loc=None)                                            # no location context
    journal += [r6, _outcome(r6, c6, "WIN", 100.0, t6 + timedelta(minutes=30))]
    t7 = T0 + timedelta(minutes=15 * 63)
    c7 = _cand(29800.0, 29787.5, 29825.0)
    journal.append(_row(t7, price=29800.0, cand=c7, loc=_loc(29800.0)))                        # never resolved
    journal.append({"ts": T0.isoformat(), "instrument": "MES", "decision": "NO_TRADE", "timeframe_minutes": 15,
                    "market_condition": None, "context": {"timestamp": T0.isoformat(), "market_condition": "TRENDING"}})
    journal.append({"ts": T0.isoformat(), "instrument": "M2K", "decision": "NO_TRADE", "timeframe_minutes": 15,
                    "context": {"timestamp": T0.isoformat()}})
    journal.append({"type": "BAR_CLAIM", "instrument": "MNQ", "ts": T0.isoformat()})
    (root / f"journal_{DAY}.jsonl").write_text("\n".join(json.dumps(r) for r in journal) + "\n")
    (root / f"bars_MNQ_{DAY}.jsonl").write_text("\n".join(json.dumps(b) for b in bars) + "\n")
    (root / f"bars_MES_{DAY}.jsonl").write_text("")
    (root / "strategy_context_observations.jsonl").write_text(
        json.dumps({"instrument": "MNQ", "timestamp": T0.isoformat(), "timeframe": "15", "session": "london"}) + "\n"
        + json.dumps({"instrument": "MNQ", "timestamp": T0.isoformat(), "timeframe": "5", "session": "london"}) + "\n")
    return root


def _args(root: Path, out: Path, **kw):
    base = dict(logs_root=str(root), out_dir=str(out), start="2026-07-16", review_date="2026-09-16", seed=20260916,
                nperm=50, nperm_interactions=20, commission_rt=1.48, slip_ticks_rt=2.0)
    base.update(kw)
    return SimpleNamespace(**base)


def test_resolver_mirrors_are_byte_identical():
    """The tool mirrors the resolver's identity helpers instead of importing them; prove parity."""
    from strategy import shadow_resolver as sr
    cases = [
        ("shadow_setups", "MNQ", "2026-08-03T12:15:01+00:00", "strat_22_continuation_observed", "LONG", 29000.0, None, None),
        ("range_signal", "MES", "2026-08-03T12:15:01+00:00", "range_break_close", "SHORT", 7717.25, "epochX", None),
        ("shadow_setups", "MNQ", "t", "s", "LONG", 1.5, None, "v2"),
    ]
    for c in cases:
        assert cpr._candidate_key(*c) == sr._candidate_key(*c)
    cand = {"strategy": "x", "direction": "long", "entry": "1.5", "stop": 1, "target": 2, "evidence_epoch": "E"}
    assert cpr._bracket(cand, "strategy") == sr._bracket(cand, "strategy")
    assert cpr._bracket({"direction": "LONG", "entry": "bad"}, "strategy") is None is sr._bracket({"direction": "LONG", "entry": "bad"}, "strategy")
    rs = {"signal_type": "RANGE_BREAK_CLOSE", "direction": "short", "entry_candidate": 1, "stop_candidate": 2, "target_candidate": 0.5}
    assert cpr._range_bracket(rs) == sr._range_bracket(rs)
    assert cpr._range_bracket(dict(rs, target_candidate=None)) is None is sr._range_bracket(dict(rs, target_candidate=None))
    assert cpr._population_fields(cand, {"variant": "row_v"}) == sr._population_fields(cand, {"variant": "row_v"}) == ("E", "row_v")


def test_join_key_parity_and_counts(logs_root, tmp_path):
    rows, stats = cpr.build_join(logs_root, "2026-07-16", 1.48, 2.0)
    from strategy.shadow_resolver import _candidate_key
    r = next(x for x in rows if x["result"] == "WIN")
    assert r["candidate_key"] == _candidate_key("shadow_setups", "MNQ", r["ts"], r["strategy"], r["direction"], r["entry"])
    assert stats["candidates_total"] == 64
    assert stats["shadow_outcome_rows"] == 64 and stats["shadow_outcome_duplicate_keys"] == 1
    assert stats["candidate_outcome_None"] == 1                     # the unresolved candidate is counted, not dropped
    assert stats["candidates_missing_location_context"] == 1
    assert stats["decision_rows_other_instrument"] == 1             # M2K counted out
    assert stats["decision_rows"] == 65 and stats["decision_rows_with_location_context"] == 63
    assert stats["observer_rows"] == 2 and stats["observer_duplicate_keys"] == 0   # 5m/15m share a timestamp, keyed apart
    dup = [x for x in rows if x["entry"] == 29500.0]
    assert len(dup) == 1 and dup[0]["result"] == "WIN"              # first SHADOW_OUTCOME wins


def test_exclusions_are_counted(logs_root):
    rows, _ = cpr.build_join(logs_root, "2026-07-16", 1.48, 2.0)
    s1, excl = cpr.select_s1(rows)
    assert excl == {"missing_location_context": 1, "timeframe_5m_rows": 1, "unresolved_or_nofill_open": 1}
    assert len(s1) == 61 and all(r["stratum"] == "S1" and r["family"] == "strat" for r in s1)


def test_cost_and_r_math():
    # MNQ: tick 0.25 / $0.50.  50-tick stop, +100 ticks gross -> $50 gross, $47.52 net, R = 47.52 / 25
    e = cpr.resolved_economics("MNQ", 29000.0, 28987.5, 100.0, 1.48, 2.0)
    assert e["stop_ticks"] == 50 and e["risk_usd"] == 25.0 and e["gross_usd"] == 50.0
    assert math.isclose(e["net_usd"], 47.52) and math.isclose(e["net_R"], 47.52 / 25) and e["gross_R"] == 2.0
    # MES: tick 0.25 / $1.25.  A -40-tick loss on a 40-tick stop is exactly -1R gross, worse net
    e = cpr.resolved_economics("MES", 7700.0, 7710.0, -40.0, 1.48, 2.0)
    assert e["gross_R"] == -1.0 and e["net_usd"] == -50.0 - 2.5 - 1.48 and e["net_R"] < -1.0


def test_gap_ledger_and_flags(logs_root):
    ledger = cpr.build_gap_ledger(logs_root)
    assert ledger["MNQ"]["gap_count"] == 1 and ledger["MNQ"]["gap_minutes"] == 45
    assert ledger["MNQ"]["gaps"][0]["missing_bars"] == 3 and ledger["MES"]["bars"] == 0
    rows, _ = cpr.build_join(logs_root, "2026-07-16", 1.48, 2.0)
    s1, _ = cpr.select_s1(rows)
    for r in s1:
        cpr._attach_times(r)
    counts = cpr.flag_gaps(s1, ledger)
    # candidates whose [bar, resolution] window overlaps 14:00-14:45 are flagged; earlier ones are not
    gap_start = T0.replace(hour=14, minute=0)
    flagged = {r["bar_ts"] for r in s1 if r["feed_gap_contaminated"]}
    assert all(gap_start - timedelta(minutes=60) <= datetime.fromisoformat(b) < gap_start + timedelta(minutes=45) for b in flagged)
    assert counts[("S1", "feed_gap_contaminated")] == len(flagged) > 0
    assert not next(r for r in s1 if r["bar_ts"] == T0.isoformat())["feed_gap_contaminated"]


def test_feature_encodings_follow_the_plan():
    loc = _loc(29000.0, middle=False, rel1h="approaching_demand", rel4h="inside_supply", tests4h=2, dist_key=5.0,
               mtr=20.0, onh=29005.0, onl=28900.0, other={"market_condition": "TRENDING"}, agree=True, phase="late_entry")
    r = {"loc": loc, "cand_loc": {"direction_zone_alignment": "against", "target_blocked_by_opposing_zone": True},
         "gex": {"gex_regime": None}, "F8": None, "F7r": None, "F8r": None}
    enc = {fid: fn(r) for fid, (_d, fn, _k) in cpr.FEATURES.items()}
    assert enc["F1"] is True          # edge (middle_of_range False) is the hypothesised-better side
    assert enc["F2"] is True and enc["F3"] is True
    assert enc["F4"] is False         # tested zone
    assert enc["F5"] is True          # 5 <= 0.5 * 20
    assert enc["F6"] is True          # within 0.5*MTR of ONH
    assert enc["F7"] is True
    assert enc["F9"] is False         # late_entry is the hypothesised-worse side
    assert enc["F10"] is False and enc["F11"] is False
    assert enc["F12"] is None and enc["F8"] is None
    neutral = dict(r, cand_loc={"direction_zone_alignment": "neutral", "target_blocked_by_opposing_zone": None})
    assert cpr.FEATURES["F10"][1](neutral) is None and cpr.FEATURES["F11"][1](neutral) is None
    assert cpr.FEATURES["F6"][1]({"loc": _loc(29000.0)}) is None    # no overnight levels -> unavailable


def test_holm_over_fixed_family():
    adj = cpr.holm({"a": 0.001, "b": 0.01, "c": 0.5})
    assert math.isclose(adj["a"], 0.022) and math.isclose(adj["b"], 0.21) and adj["c"] == 1.0
    assert cpr.HOLM_FAMILY == 22


def test_spot_check_selection_is_seeded_by_review_date():
    assert cpr.spot_check_indices(7563, "2026-09-16") == [5809, 4169, 343]   # matches the first review
    assert cpr.spot_check_indices(7563, "2026-09-30") != [5809, 4169, 343]


def test_end_to_end_is_deterministic(logs_root, tmp_path):
    outs = []
    for name in ("a", "b"):
        out = tmp_path / name
        args = _args(logs_root, out)
        cpr.run_gaps(args)
        cpr.run_join(args)
        res = cpr.run_analyze(args)
        outs.append((out / "prereg_results.json").read_bytes())
        assert res["summary"]["n_S1"] == 61 and res["summary"]["n_S2"] == 0
        assert res["summary"]["S2_stats"] == {"strat_22_reversal_file_missing": 1, "trend_consolidation_break_file_missing": 1}
        f1 = res["features"]["F1"]
        assert f1["testable"] and f1["n_true"] == 30 and f1["n_false"] == 31   # 30 edge bars; 30 middle + the duplicate-key row
        assert f1["effect_netR"] > 2.5 and f1["p_perm"] < 0.05           # edges won by construction
        assert res["features"]["F12"]["testable"] is False and res["features"]["F12"]["availability"] == 0.0
        assert set(res) == {"_provenance", "summary", "features", "interactions"}
        assert res["_provenance"]["seed"] == 20260916 and res["_provenance"]["holm_family"] == 22
        assert "F8r" not in res["summary"]["pvals"] and "F7r" not in res["summary"]["holm"]
        assert res["summary"]["spot_check_indices"] == cpr.spot_check_indices(61, "2026-09-16")
    assert outs[0] == outs[1]


def test_results_schema_matches_frozen_first_review():
    frozen = REPO / "docs" / "context-permission-first-review-2026-09-16-results.json"
    if not frozen.exists():
        pytest.skip("frozen first-review results not present on this branch (lands with #614)")
    ref = json.load(open(frozen))
    assert set(ref) == {"_provenance", "summary", "features", "interactions"}
    assert set(ref["features"]) == set(cpr.FEATURES)
    assert set(ref["interactions"]) == set(cpr.INTERACTIONS)


def test_tool_imports_no_runtime_modules():
    code = (
        "import importlib.util, sys\n"
        f"spec = importlib.util.spec_from_file_location('cpr', {str(SCRIPT)!r}); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "bad = sorted(k for k in sys.modules if k.split('.')[0] in ('execution', 'webhook', 'replay', 'risk', 'broker', 'adaptive', 'journal', 'strategy'))\n"
        "print(bad)\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO, check=True)
    assert out.stdout.strip() == "[]", out.stdout
