#!/usr/bin/env python3
"""Prereg #929 step 0 parity gate (checks 0a-0d). Never reads forward P&L.

Research only. Implements docs/prereg-mnq-portfolio-ex-asia-forward-2026-09-23.md
§3 step 0 as amended by §9.4 (thresholds unchanged):

  0a  5m OHLC parity   fresh Polygon 5m  vs replay_corpus_v1_5m               2026-07-01..07-23
  0b  15m OHLC parity  fresh Polygon 15m vs replay_corpus_v1_market_condition_fixed 2026-06-05..07-23
  0c  candidate parity every frozen #915 family adapter over the corpus rebuilt
      from that Polygon fetch (existing derive_candles path, 60-day pre-roll,
      gap days removed) vs over the frozen corpus, on the overlap dates; the
      §9.4 holiday-boundary class is the only automatic explanation
  0d  4HR-audit lineage: 4HR, 3-2-2, Miyagi, Daily adapters over
      replay_corpus_v1_5m vs replay_corpus_v1_5m_4hr_audit on shared dates,
      plus 3-2-2 over replay_polygon vs replay_corpus_v1_market_condition_fixed
      (15m detector) and Miyagi over replay_polygon_5m vs replay_corpus_v1_5m
      (fill corpus), #915 window

Every run re-fetches from Polygon (§9.1); needs the local POLYGON_API_KEY.

Supplementary (reported, see the JSON): whether the unchanged Miyagi detector
reproduces the frozen #915 Miyagi candidate file (gating for 0c, because
forward Miyagi candidates come from that detector), the 15m detector lineage
for 3-2-2/Miyagi, and whether the ported #915 core reproduces the prereg §1
table on the frozen corpora.

Usage:
    python3 scripts/prereg929_step0_parity.py \
        --data-root data \
        --out /private/tmp/.../step0.json [--summary step0.txt] \
        [--explanations reviewed.json] [--previous-report old_step0.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research import prereg929_step0 as s0  # noqa: E402
from research.prereg929_forward_corpus import (  # noqa: E402
    STEP0_WARMUP_DAYS,
    fetch_polygon_bars,
    rebuild_overlap_corpus,
)
from research.prereg929_forward_portfolio import (  # noqa: E402
    ALL_FAMILIES,
    FAMILY_4HR,
    FAMILY_322,
    FAMILY_ASIA,
    FAMILY_DAILY,
    FAMILY_MIYAGI,
    MIYAGI_FROZEN_REL,
    PREREG_ID,
    corpus_days,
    detect_miyagi_candidates,
    pr915_roots,
    run_family_adapters,
    single_pair_roots,
    view_roots,
)

OVERLAP_5M = (date(2026, 7, 1), date(2026, 7, 23))
OVERLAP_15M = (date(2026, 6, 5), date(2026, 7, 23))
LINEAGE_FAMILIES = (FAMILY_4HR, FAMILY_322, FAMILY_DAILY, FAMILY_MIYAGI)
PR915_TABLE = {
    "all_six": {"fills": 488, "net": 5138.46},
    "ex_asia": {"fills": 60, "net": 9372.70},
}


def _run_0c(data_root: Path, rebuilt_root: Path, tmp: Path) -> tuple[list, dict]:  
    frozen = single_pair_roots(
        data_root / "replay_corpus_v1_5m",
        data_root / "replay_corpus_v1_market_condition_fixed",
        miyagi_candidates="detect",
    )
    rebuilt = single_pair_roots(rebuilt_root / "5m", rebuilt_root / "15m", miyagi_candidates="detect")
    five = [f for f in ALL_FAMILIES if f != FAMILY_ASIA]
    mismatches, sizes = [], {}
    for label, (start, end), fams in (
        ("0c", OVERLAP_5M, five),
        ("0c", OVERLAP_15M, [FAMILY_ASIA]),
    ):
        fv = view_roots(frozen, start, end, tmp / f"0c_frozen_{start}")
        rv = view_roots(rebuilt, start, end, tmp / f"0c_rebuilt_{start}")
        frun = run_family_adapters(fv, start, end, families=fams)
        rrun = run_family_adapters(rv, start, end, families=fams)
        mismatches += s0.compare_runs(label, "frozen", frun, "rebuilt", rrun, fams)
        for side, run in (("frozen", frun), ("rebuilt", rrun)):
            for fam, info in s0.family_stream_sizes(run).items():
                sizes.setdefault(fam, {})[side] = {**info, "date_range": [start.isoformat(), end.isoformat()]}
        if FAMILY_MIYAGI in fams:
            sizes.setdefault(FAMILY_MIYAGI, {})["detector_candidates"] = {
                "frozen": frun.miyagi_candidates, "rebuilt": rrun.miyagi_candidates,
            }
    return mismatches, sizes


def _run_0d(data_root: Path, tmp: Path) -> tuple[list, dict, list[str]]:
    a = set(corpus_days(data_root / "replay_corpus_v1_5m"))
    b = set(corpus_days(data_root / "replay_corpus_v1_5m_4hr_audit"))
    shared = sorted(a & b)
    start, end = date.fromisoformat(shared[0]), date.fromisoformat(shared[-1])
    base = pr915_roots(data_root)
    from dataclasses import replace

    left = replace(base, root5=data_root / "replay_corpus_v1_5m", root_miyagi5=data_root / "replay_corpus_v1_5m")
    right = replace(
        base,
        root5=data_root / "replay_corpus_v1_5m_4hr_audit",
        root_miyagi5=data_root / "replay_corpus_v1_5m_4hr_audit",
    )
    lv = view_roots(left, start, end, tmp / "0d_v1_5m")
    rv = view_roots(right, start, end, tmp / "0d_4hr_audit")
    lrun = run_family_adapters(lv, start, end, families=LINEAGE_FAMILIES)
    rrun = run_family_adapters(rv, start, end, families=LINEAGE_FAMILIES)
    mm = s0.compare_runs("0d", "v1_5m", lrun, "v1_5m_4hr_audit", rrun, LINEAGE_FAMILIES)
    sizes = {"v1_5m": s0.family_stream_sizes(lrun), "v1_5m_4hr_audit": s0.family_stream_sizes(rrun)}
    only = {"days_only_in_v1_5m_within_range": [d for d in sorted(a - b) if shared[0] <= d <= shared[-1]],
            "days_only_in_4hr_audit_within_range": [d for d in sorted(b - a) if shared[0] <= d <= shared[-1]]}
    return mm, {"shared_date_range": [shared[0], shared[-1]], "shared_days": len(shared), **only, "streams": sizes}, shared


def _run_0d_polygon_lineage(data_root: Path, tmp: Path) -> tuple[list, dict]:
    """§9.4: the two lineages the evaluator found (#915 window)."""
    from dataclasses import replace

    lo, hi = date(2025, 7, 24), date(2026, 6, 26)
    base = pr915_roots(data_root)  # root322_15=replay_polygon, root_miyagi5=replay_polygon_5m
    right = replace(
        base,
        root322_15=data_root / "replay_corpus_v1_market_condition_fixed",
        root_miyagi5=data_root / "replay_corpus_v1_5m",
    )
    fams = (FAMILY_322, FAMILY_MIYAGI)
    lv = view_roots(base, lo, hi, tmp / "0d_polygon")
    rv = view_roots(right, lo, hi, tmp / "0d_v1")
    lrun = run_family_adapters(lv, lo, hi, families=fams)
    rrun = run_family_adapters(rv, lo, hi, families=fams)
    mm = s0.compare_runs("0d_polygon", "replay_polygon", lrun, "v1_corpora", rrun, fams)
    return mm, {"date_range": [lo.isoformat(), hi.isoformat()],
                "streams": {"replay_polygon": s0.family_stream_sizes(lrun), "v1_corpora": s0.family_stream_sizes(rrun)}}


def _supplementary(data_root: Path) -> dict:
    from research.run_322_expanded_evidence import detect_candidates as detect322

    out: dict = {}
    frozen_file = json.loads((REPO / MIYAGI_FROZEN_REL).read_text())
    fr = frozen_file["study_range"]
    det = detect_miyagi_candidates(
        data_root / "replay_polygon", data_root / "replay_polygon_5m",
        date.fromisoformat(fr["start"]), date.fromisoformat(fr["end"]),
    )
    out["miyagi_detector_reproduces_frozen_candidate_file"] = s0.compare_candidate_lists(
        det, frozen_file["candidates"], s0.MIYAGI_CAND_FIELDS
    )

    lo, hi = date(2025, 7, 24), date(2026, 6, 26)
    c322a = detect322(data_root / "replay_polygon", "MNQ", lo, hi)
    c322b = detect322(data_root / "replay_corpus_v1_market_condition_fixed", "MNQ", lo, hi)
    fields322 = ("date", "direction", "entry_trigger", "stop", "target", "gap_open")
    for c in c322a + c322b:
        c["date"] = c["date"].isoformat()
    out["lineage_322_detector_replay_polygon_vs_market_condition_fixed"] = {
        **s0.compare_candidate_lists(c322a, c322b, fields322),
        "date_range": [lo.isoformat(), hi.isoformat()],
    }
    ma = detect_miyagi_candidates(data_root / "replay_polygon", data_root / "replay_polygon_5m", lo, hi)
    mb = detect_miyagi_candidates(
        data_root / "replay_corpus_v1_market_condition_fixed", data_root / "replay_corpus_v1_5m", lo, hi
    )
    out["lineage_miyagi_detector_replay_polygon_vs_v1_corpora"] = {
        **s0.compare_candidate_lists(ma, mb, s0.MIYAGI_CAND_FIELDS),
        "date_range": [lo.isoformat(), hi.isoformat()],
    }
    return out


def _core_reproduction(data_root: Path) -> dict:
    """Frozen #915 run on the frozen corpora (controls enforced). Historical only."""
    import contextlib
    import io

    from scripts import mnq_combined_portfolio_audit as pa

    try:
        with contextlib.redirect_stdout(io.StringIO()):
            rep = pa.run(data_root)
    except Exception as exc:
        return {"reproduces_prereg_table": False, "error": f"{type(exc).__name__}: {exc}"[:600]}
    six = rep["combined"]
    ex = rep["leave_one_family_out"][FAMILY_ASIA]["without_family"]
    got = {"all_six": {"fills": six["fills"], "net": six["net"]}, "ex_asia": {"fills": ex["fills"], "net": ex["net"]}}
    ok = all(
        got[k]["fills"] == v["fills"] and abs(got[k]["net"] - v["net"]) < 0.01 for k, v in PR915_TABLE.items()
    )
    return {"reproduces_prereg_table": ok, "expected": PR915_TABLE, "got": got,
            "controls_passed": True, "note": "historical #915 window 2025-07-24..2026-06-26 only"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--data-root", type=Path, default=REPO / "data")
    p.add_argument("--keep-rebuilt", type=Path, default=None,
                   help="optional dir OUTSIDE the repo to keep the rebuilt overlap corpus for inspection")
    p.add_argument("--explanations", type=Path, default=None,
                   help="reviewed JSON {mismatch_id: explanation}; unexplained mismatches fail 0c/0d")
    p.add_argument("--previous-report", type=Path, default=None, help="carry its attempts log forward")
    p.add_argument("--skip-core-reproduction", action="store_true")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--summary", type=Path, default=None)
    args = p.parse_args(argv)

    explanations = json.loads(args.explanations.read_text()) if args.explanations else {}
    data_root = args.data_root.resolve()
    generated = datetime.now(timezone.utc).isoformat()

    # §9.1/§9.2: one fresh fetch per timeframe, 60-day pre-roll, overlap end.
    from datetime import timedelta

    starts = {5: OVERLAP_5M[0], 15: OVERLAP_15M[0]}
    end = OVERLAP_5M[1]
    fetch_start = min(starts.values()) - timedelta(days=STEP0_WARMUP_DAYS)
    loaded = {tf: fetch_polygon_bars(tf, fetch_start, end) for tf in (5, 15)}
    checks: dict = {}
    checks["0a"] = s0.ohlc_parity(loaded[5], data_root / "replay_corpus_v1_5m", *OVERLAP_5M, label="0a")
    checks["0b"] = s0.ohlc_parity(
        loaded[15], data_root / "replay_corpus_v1_market_condition_fixed", *OVERLAP_15M, label="0b"
    )

    with tempfile.TemporaryDirectory(prefix="prereg929-step0-") as tmp_s:
        tmp = Path(tmp_s)
        rebuilt_root = args.keep_rebuilt or (tmp / "rebuilt")
        rebuilt = rebuild_overlap_corpus(rebuilt_root, loaded, starts, end)
        supp = _supplementary(data_root)
        supp["step0_rebuild_gap_days"] = rebuilt["gap_days"]
        mm0c, sizes0c = _run_0c(data_root, rebuilt_root, tmp)
        diffs = {
            "5m": s0.corpus_field_diffs(data_root / "replay_corpus_v1_5m", rebuilt_root / "5m", *OVERLAP_5M),
            "15m": s0.corpus_field_diffs(
                data_root / "replay_corpus_v1_market_condition_fixed", rebuilt_root / "15m", *OVERLAP_15M
            ),
        }
        merged: dict[str, list[str]] = {}
        for per_day in diffs.values():
            for d, fields in per_day.items():
                merged[d] = sorted(set(merged.get(d, [])) | set(fields))
        auto = s0.holiday_boundary_explanations(mm0c, merged)
        supp["corpus_field_diffs_by_utc_day"] = diffs
        extra0c = []
        if not supp["miyagi_detector_reproduces_frozen_candidate_file"]["identical"]:
            extra0c.append({"what": "Miyagi detector does not reproduce the frozen #915 candidate file"})
        checks["0c"] = s0.candidate_check("0c", mm0c, {**auto, **explanations}, sizes=sizes0c, extra_failures=extra0c)
        checks["0c"]["predeclared_holiday_class_applied"] = len(auto)
        mm0d, info0d, _ = _run_0d(data_root, tmp)
        mm0dp, info0dp = _run_0d_polygon_lineage(data_root, tmp)
        checks["0d"] = s0.candidate_check("0d", mm0d + mm0dp, explanations,
                                          sizes={**info0d, "polygon_lineages": info0dp})

    if not args.skip_core_reproduction:
        supp["ported_915_core_reproduces_prereg_table"] = _core_reproduction(data_root)

    overall = s0.overall_verdict(checks)
    attempts = []
    if args.previous_report and args.previous_report.is_file():
        attempts = list(json.loads(args.previous_report.read_text()).get("attempts_log") or [])
    attempts.append({
        "generated_at": generated,
        "overall": overall,
        "verdicts": {k: checks[k]["verdict"] for k in ("0a", "0b", "0c", "0d")},
        "mismatch_counts": {
            "0a_ohlc": len(checks["0a"]["ohlc_mismatches"]),
            "0a_rth_gap_runs": len(checks["0a"]["rth_gap_violations_gt_2_bars"]),
            "0b_ohlc": len(checks["0b"]["ohlc_mismatches"]),
            "0b_rth_gap_runs": len(checks["0b"]["rth_gap_violations_gt_2_bars"]),
            "0c": checks["0c"]["mismatch_count"],
            "0d": checks["0d"]["mismatch_count"],
        },
    })
    report = {
        "prereg": PREREG_ID,
        "kind": "step0_parity",
        "generated_at": generated,
        "overall": overall,
        "thresholds": {"ohlc_match_rate_min": s0.MATCH_RATE_MIN, "tick": s0.TICK,
                       "max_rth_gap_bars": s0.MAX_RTH_GAP_BARS},
        "inputs": {"data_root": str(data_root), "source": "polygon", "fetched_at": generated,
                   "warmup_days": STEP0_WARMUP_DAYS,
                   "polygon_5m_fetch": loaded[5].source_files, "polygon_15m_fetch": loaded[15].source_files},
        "checks": checks,
        "supplementary": supp,
        "attempts_log": attempts,
        "note": "Step 0 never reads forward P&L. Candidate comparisons use identity/timing/bracket fields only.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    text = s0.summarize_text(report)
    if args.summary:
        args.summary.write_text(text)
    print(text, end="")
    return 0 if overall == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
