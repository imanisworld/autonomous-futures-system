"""R6 — frozen P1 structural feature table over the sealed-study candidates (RESEARCH ONLY).

For every row of an R5 ``candidates.jsonl`` (one instrument) this driver:

* takes the candidate's ``instrument`` / ``bar_ts`` as ``B0`` and its frozen ``direction`` /
  ``entry`` / ``stop`` / ``target`` / ``strategy``;
* hands the admitted 15m corpus bars with ``ts <= B0`` (last ``LIVE_BAR_WINDOW``) to the frozen
  P1 functions ``research.structural_level_features.build_levels`` (once per ``B0``) and
  ``label_candidate`` (once per candidate) — nothing in P1 is modified, wrapped or tuned;
* writes one ``features.jsonl`` row carrying the candidate key verbatim, the level table
  (value / status / gap flags), the H1–H6 labels and the P1 version identity.

Fail-closed: refuses a candidate file whose sha256 differs from ``--expect-sha256``, a row
whose ``instrument`` is not the one requested, any row containing an ``outcome`` field, and a
corpus whose ``MANIFEST.json`` sha256 differs from ``--expect-corpus-manifest-sha256``. It
never opens ``outcomes.sealed.jsonl`` (the path is not even an argument). Output order is the
candidate file order; JSON is written with sorted keys, so a rerun is byte-identical.
Never a runtime import.

Usage:
    python3 scripts/structural_level_r6_features.py --candidates logs/structural_level_r5/MNQ/candidates.jsonl \
        --corpus-dir data/replay_polygon_v2/MNQ --instrument MNQ --out-dir logs/structural_level_r6/MNQ \
        [--expect-sha256 <hex>] [--expect-corpus-manifest-sha256 <hex>] [--limit N]
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from research import structural_level_features as slf  # noqa: E402  (pure, no runtime imports)
from research.structural_level_p2 import load_corpus_bars, parse_dt  # noqa: E402

TOOL_VERSION = "slr6-features-v1"
HYPOTHESES = ("H1", "H2", "H3", "H4", "H5", "H6")
OUTCOME_KEYS = ("outcome", "result", "pnl", "pnl_ticks", "exit_price", "exit_reason", "entry_filled", "mae", "mfe")


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def corpus_as_bars(corpus: dict[datetime, dict]) -> list[dict]:
    out = []
    for ts in sorted(corpus):
        c = corpus[ts]
        out.append({"ts": ts, "open": float(c["open"]), "high": float(c["high"]), "low": float(c["low"]),
                    "close": float(c["close"]), "volume": c.get("volume")})
    return out


def _level_row(lv: slf.Level) -> dict:
    return {"value": lv.value, "status": lv.status, "kind": lv.kind, "top": lv.top, "bottom": lv.bottom,
            "tests": lv.tests, "broken": lv.broken, "exploratory": lv.exploratory, "reason": lv.reason,
            "gap_minutes": lv.gap_minutes, "gap_contaminated": lv.gap_contaminated,
            "formed_ts": lv.formed_ts.isoformat() if lv.formed_ts else None}


def feature_row(cand: dict, ls: slf.LevelSet, labels: dict, p1_sha: str) -> dict:
    """One features.jsonl row. Carries no outcome field (the candidate row has none either)."""
    admitted = slf.MAJOR + slf.ZONE_NAMES + ("PDC_BAR",)
    levels = {k: _level_row(v) for k, v in ls.levels.items()}
    gap_names = sorted(n for n in admitted if n in ls.levels and ls.levels[n].gap_contaminated)
    na_names = sorted(n for n in admitted if n not in ls.levels or ls.levels[n].status == "NOT_AVAILABLE")
    niw_names = sorted(n for n in admitted if n in ls.levels and ls.levels[n].status == "NOT_IN_WINDOW")
    return {
        "candidate_key": cand["candidate_key"],
        "instrument": cand["instrument"],
        "bar_ts": cand["bar_ts"],
        "b0_ts": ls.b0_ts.isoformat(),
        "strategy": cand.get("strategy"),
        "family": cand.get("family"),
        "p1_family": labels["family"],
        "direction": cand.get("direction"),
        "entry": cand.get("entry"), "stop": cand.get("stop"), "target": cand.get("target"),
        "session_candidate": cand.get("session"),
        "session_p1": ls.session,
        "contract": cand.get("contract"),
        "is_roll_utc_day": cand.get("is_roll_utc_day"),
        "contract_roll_utc_date": cand.get("contract_roll_utc_date"),
        "recent_bars_warmup": cand.get("recent_bars_warmup"),
        "candle_present_in_corpus": cand.get("candle_present_in_corpus"),
        "corpus_day_position": cand.get("corpus_day_position"),
        "p1": {"module": "research/structural_level_features.py", "module_sha256": p1_sha,
               "prereg_version": slf.PREREG_VERSION, "prereg_sha": slf.PREREG_SHA,
               "constants": {"TAU_MTR": slf.TAU_MTR, "PROX_MTR": slf.PROX_MTR, "K_SWEEP_BARS": slf.K_SWEEP_BARS,
                             "R_RETEST_BARS": slf.R_RETEST_BARS, "N_ACCEPT": slf.N_ACCEPT,
                             "D_MAX_MTR": slf.D_MAX_MTR, "LIVE_BAR_WINDOW": slf.LIVE_BAR_WINDOW},
               "diagnostic_levels_not_admitted": list(slf.DIAGNOSTIC_LEVELS),
               "exploratory_levels": list(slf.EXPLORATORY_LEVELS)},
        "n_bars_in_window": ls.n_bars,
        "mtr15": ls.mtr15,
        "close_b0": ls.close,
        "levels": levels,
        "admitted_levels_not_available": na_names,
        "admitted_levels_not_in_window": niw_names,
        "admitted_levels_gap_contaminated": gap_names,
        "level_warnings": list(ls.warnings),
        "hypotheses": {h: labels["hypotheses"].get(h, {"label": "NOT_AVAILABLE", "reason": "not produced"})
                       for h in HYPOTHESES},
    }


def build(candidates_path: Path, corpus_dir: Path, instrument: str, *, limit: int | None = None,
          progress: bool = False) -> tuple[list[dict], dict]:
    p1_sha = sha256_file(REPO / "research" / "structural_level_features.py")
    bars = corpus_as_bars(load_corpus_bars(str(corpus_dir)))
    if not bars:
        raise SystemExit(f"[r6] no corpus bars under {corpus_dir}")
    idx_of = {b["ts"]: i for i, b in enumerate(bars)}
    rows: list[dict] = []
    cache_ts: datetime | None = None
    cache_ls: slf.LevelSet | None = None
    cache_window: list[dict] | None = None
    census = {"rows": 0, "b0_not_in_corpus": 0, "unique_b0": 0}
    seen_b0: set[datetime] = set()
    with candidates_path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            if limit is not None and lineno > limit:
                break
            cand = json.loads(line)
            for k in OUTCOME_KEYS:
                if k in cand:
                    raise SystemExit(f"[r6] candidates line {lineno} carries outcome-like field {k!r} — refusing")
            if cand.get("instrument") != instrument:
                raise SystemExit(f"[r6] candidates line {lineno} instrument {cand.get('instrument')!r} != {instrument}")
            b0 = parse_dt(cand.get("bar_ts"))
            if b0 is None:
                raise SystemExit(f"[r6] candidates line {lineno} has no parsable bar_ts")
            if b0 not in idx_of:
                # fail closed: no corpus bar at B0 → every level/label NOT_AVAILABLE, nothing substituted
                census["b0_not_in_corpus"] += 1
                rows.append({"candidate_key": cand["candidate_key"], "instrument": instrument, "bar_ts": cand["bar_ts"],
                             "b0_ts": None, "strategy": cand.get("strategy"), "family": cand.get("family"),
                             "direction": cand.get("direction"), "p1": {"module_sha256": p1_sha,
                             "prereg_version": slf.PREREG_VERSION}, "levels": {}, "admitted_levels_not_available": None,
                             "admitted_levels_not_in_window": None,
                             "admitted_levels_gap_contaminated": None, "level_warnings": ["B0 bar not in corpus"],
                             "hypotheses": {h: {"label": "NOT_AVAILABLE", "reason": "B0 bar not in corpus"} for h in HYPOTHESES}})
                continue
            if cache_ts != b0:
                i = idx_of[b0]
                cache_window = bars[max(0, i - slf.LIVE_BAR_WINDOW + 1): i + 1]   # causal: ts <= B0 only
                cache_ls = slf.build_levels(cache_window, instrument, b0_ts=b0)
                cache_ts = b0
                seen_b0.add(b0)
            assert cache_ls is not None and cache_window is not None
            labels = slf.label_candidate(cache_window, instrument, direction=cand["direction"], entry=float(cand["entry"]),
                                         stop=float(cand["stop"]),
                                         target=(float(cand["target"]) if cand.get("target") is not None else None),
                                         strategy=cand.get("strategy"), b0_ts=b0, level_set=cache_ls)
            rows.append(feature_row(cand, cache_ls, labels, p1_sha))
            census["rows"] += 1
            if progress and lineno % 5000 == 0:
                print(f"[r6] {instrument} {lineno} rows", file=sys.stderr, flush=True)
    census["unique_b0"] = len(seen_b0)
    census["rows"] += census["b0_not_in_corpus"]
    return rows, {"p1_module_sha256": p1_sha, "corpus_bars": len(bars),
                  "corpus_first_bar": bars[0]["ts"].isoformat(), "corpus_last_bar": bars[-1]["ts"].isoformat(), **census}


def summarize(rows: list[dict]) -> dict:
    fam_h: dict[str, dict[str, collections.Counter]] = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))
    na_levels: collections.Counter = collections.Counter()
    niw_levels: collections.Counter = collections.Counter()
    any_niw = 0
    gap_levels: collections.Counter = collections.Counter()
    any_gap = any_na = 0
    keys: collections.Counter = collections.Counter(r["candidate_key"] for r in rows)
    for r in rows:
        for h in HYPOTHESES:
            fam_h[r.get("family")][h][r["hypotheses"][h]["label"]] += 1
        if r.get("admitted_levels_gap_contaminated"):
            any_gap += 1
            for n in r["admitted_levels_gap_contaminated"]:
                gap_levels[n] += 1
        if r.get("admitted_levels_not_available"):
            any_na += 1
            for n in r["admitted_levels_not_available"]:
                na_levels[n] += 1
        if r.get("admitted_levels_not_in_window"):
            any_niw += 1
            for n in r["admitted_levels_not_in_window"]:
                niw_levels[n] += 1
    return {
        "rows": len(rows), "unique_candidate_keys": len(keys),
        "duplicate_candidate_keys": {k: c for k, c in keys.items() if c > 1},
        "instruments": sorted({r["instrument"] for r in rows}),
        "families": sorted({str(r.get("family")) for r in rows}),
        "hypothesis_labels_by_family": {f: {h: dict(c) for h, c in hs.items()} for f, hs in sorted(fam_h.items())},
        "hypothesis_labels_total": {h: dict(sum((fam_h[f][h] for f in fam_h), collections.Counter())) for h in HYPOTHESES},
        "rows_with_any_admitted_level_not_available": any_na,
        "admitted_level_not_available_counts": dict(na_levels),
        "rows_with_any_admitted_level_not_in_window": any_niw,
        "admitted_level_not_in_window_counts": dict(niw_levels),
        "rows_with_any_admitted_level_gap_contaminated": any_gap,
        "admitted_level_gap_contaminated_counts": dict(gap_levels),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--candidates", required=True)
    ap.add_argument("--corpus-dir", required=True)
    ap.add_argument("--instrument", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--expect-sha256", default=None, help="refuse a candidates file with another sha256")
    ap.add_argument("--expect-corpus-manifest-sha256", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--progress", action="store_true")
    args = ap.parse_args(argv)
    inst = args.instrument.upper()
    cpath, cdir = Path(args.candidates), Path(args.corpus_dir)
    csha = sha256_file(cpath)
    if args.expect_sha256 and csha != args.expect_sha256:
        print(f"[r6] candidates sha256 {csha} != expected {args.expect_sha256} — BLOCKED", file=sys.stderr)
        return 2
    msha = sha256_file(cdir / "MANIFEST.json") if (cdir / "MANIFEST.json").exists() else None
    if args.expect_corpus_manifest_sha256 and msha != args.expect_corpus_manifest_sha256:
        print(f"[r6] corpus manifest sha256 {msha} != expected — BLOCKED", file=sys.stderr)
        return 2
    rows, meta = build(cpath, cdir, inst, limit=args.limit, progress=args.progress)
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    fpath = out / "features.jsonl"
    with fpath.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, sort_keys=True, separators=(",", ":"), default=str) + "\n")
    summary = summarize(rows)
    manifest = {
        "tool": TOOL_VERSION, "instrument": inst,
        "built_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": {"candidates": {"path": str(cpath), "sha256": csha},
                   "corpus_dir": str(cdir), "corpus_manifest_sha256": msha,
                   "p1_module": {"path": "research/structural_level_features.py", "sha256": meta["p1_module_sha256"],
                                 "prereg_version": slf.PREREG_VERSION, "prereg_sha": slf.PREREG_SHA}},
        "corpus": {"bars": meta["corpus_bars"], "first_bar": meta["corpus_first_bar"], "last_bar": meta["corpus_last_bar"]},
        "features_file": {"path": str(fpath), "sha256": sha256_file(fpath), "rows": len(rows)},
        "b0_census": {"unique_b0": meta["unique_b0"], "b0_not_in_corpus": meta["b0_not_in_corpus"]},
        "summary": summary,
        "outcome_files_read": [],
    }
    with (out / "manifest.json").open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"[r6] {inst} rows={len(rows)} unique_keys={summary['unique_candidate_keys']} "
          f"dups={len(summary['duplicate_candidate_keys'])} features_sha256={manifest['features_file']['sha256']}")
    for h in HYPOTHESES:
        print(f"[r6]   {h} {summary['hypothesis_labels_total'][h]}")
    print(f"[r6]   rows_any_level_NA={summary['rows_with_any_admitted_level_not_available']} "
          f"rows_any_level_not_in_window={summary['rows_with_any_admitted_level_not_in_window']} "
          f"rows_any_gap={summary['rows_with_any_admitted_level_gap_contaminated']}")
    return 0 if not summary["duplicate_candidate_keys"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
