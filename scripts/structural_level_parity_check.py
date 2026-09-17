#!/usr/bin/env python3
"""P3 — levels-only parity proof for the dynamic structural-level prereg. AUDIT / RESEARCH ONLY.

Rebuilds every admitted level with ``research/structural_level_features.build_levels`` from a
read-only copy of the VPS bar history and compares it, row by row, with the copies the live
runner journaled at the time (``context.location_context``, ``context.orb``, ``context.vwap``,
``context.previous_day``, ``wall_context`` PWH/PWL/HOD/LOD). Prereg v1.2 §14 P3 / §12 K2:
an admitted level passes at >= 98% agreement within ONE TICK over its comparable rows.

Reads evidence only; never reads a SHADOW_OUTCOME, PaperBroker or demo outcome row, never
scores a candidate, never touches the journal, collectors, config or the box. Writes only
into ``--out-dir``. Imports nothing from webhook/strategy/execution/journal/replay.

Denominator rules (frozen in the prereg):
  * NY / London ORB: only rows in that session whose canonical 09:30 / 03:00 bar exists in
    bar history; ``NOT_AVAILABLE`` session-days are counted separately, never as failures.
  * ONH/ONL, PMH/PML: rows where the journal carries the level.
  * zones: (top, bottom, tests, broken, formed_ts) of the nearest unbroken zone per
    timeframe/kind; None==None is agreement.
  * VWAP: rows whose ``price_vs_vwap`` is defined.

Usage:
    python3 scripts/structural_level_parity_check.py --logs-root <snapshot> \
        --start 2026-07-16 --end-ts 2026-09-14T22:00:00+00:00 --out-dir <dir>
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from context.bar_history import _parse_dt  # noqa: E402  (pure parser)
from research import structural_level_features as slf  # noqa: E402

TOOL_VERSION = "slf-parity-v1"
INSTRUMENTS = ("MNQ", "MES")
PASS_PCT = 98.0
LIVE_LOOKBACK_DAYS = 14   # runner: BarHistory.recent(inst, 960, lookback_days=14)


def _read_jsonl(path: str):
    with open(path) as fh:
        for line in fh:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_bars(logs_root: str, inst: str) -> list[dict]:
    """All 15m bars for the instrument, sorted, first-seen-wins on duplicate ts."""
    seen: dict[datetime, dict] = {}
    for f in sorted(glob.glob(os.path.join(logs_root, f"bars_{inst}_*.jsonl"))):
        for r in _read_jsonl(f):
            ts = _parse_dt(r.get("ts"))
            if ts is None or ts in seen:
                continue
            try:
                seen[ts] = {"ts": ts, "open": float(r["open"]), "high": float(r["high"]),
                            "low": float(r["low"]), "close": float(r["close"]), "volume": r.get("volume")}
            except (KeyError, TypeError, ValueError):
                continue
    return [seen[k] for k in sorted(seen)]


def _is_decision_row(r: dict) -> bool:
    return isinstance(r, dict) and "decision" in r and not r.get("type")


class Tally:
    def __init__(self) -> None:
        self.c: collections.Counter = collections.Counter()
        self.examples: dict[str, list[dict]] = collections.defaultdict(list)
        # decomposition of every disagreement: (key, gap_flag) and (key, inst:trading_day)
        self.by_gap: collections.Counter = collections.Counter()
        self.by_day: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
        self.day_n: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)

    def cmp(self, key: str, mine: Any, theirs: Any, tick: float, row_id: str, *, exact: bool = False,
            gap_flag: Optional[bool] = None, day: Optional[str] = None) -> None:
        if mine is None and theirs is None:
            self.c[f"{key}.both_none"] += 1
            return
        if theirs is None:
            self.c[f"{key}.journal_missing"] += 1
            return
        if mine is None:
            self.c[f"{key}.mine_missing"] += 1
            self._ex(key, row_id, mine, theirs)
            return
        self.c[f"{key}.compared"] += 1
        if exact:
            ok = mine == theirs
        else:
            try:
                ok = abs(float(mine) - float(theirs)) <= tick + 1e-9
            except (TypeError, ValueError):
                ok = mine == theirs
        g = "gap_flagged" if gap_flag else ("clean" if gap_flag is not None else "n/a")
        if day:
            self.day_n[key][day] += 1
        if ok:
            self.c[f"{key}.agree"] += 1
            self.by_gap[(key, "agree", g)] += 1
        else:
            self.c[f"{key}.disagree"] += 1
            self.by_gap[(key, "disagree", g)] += 1
            if day:
                self.by_day[key][day] += 1
            self._ex(key, row_id, mine, theirs)

    def _ex(self, key: str, row_id: str, mine: Any, theirs: Any) -> None:
        if len(self.examples[key]) < 12:
            self.examples[key].append({"row": row_id, "mine": mine, "journal": theirs})

    def report(self) -> dict:
        keys = sorted({k.rsplit(".", 1)[0] for k in self.c})
        out = {}
        for k in keys:
            n = self.c[f"{k}.compared"]
            a = self.c[f"{k}.agree"]
            out[k] = {
                "compared": n, "agree": a, "disagree": self.c[f"{k}.disagree"],
                "pct": round(100.0 * a / n, 3) if n else None,
                "journal_missing": self.c[f"{k}.journal_missing"],
                "mine_missing": self.c[f"{k}.mine_missing"],
                "both_none": self.c[f"{k}.both_none"],
                "examples": self.examples.get(k, []),
                "by_gap_flag": {f"{v}.{g}": c for (kk, v, g), c in self.by_gap.items() if kk == k},
                "disagree_by_trading_day": {d: f"{n}/{self.day_n[k][d]}" for d, n in sorted(self.by_day[k].items())},
            }
        return out


def run(args) -> dict:
    end_ts = _parse_dt(args.end_ts)
    bars = {inst: load_bars(args.logs_root, inst) for inst in INSTRUMENTS}
    tally = Tally()
    stats: collections.Counter = collections.Counter()
    orb_days: dict[str, set] = {"NY_ORB": set(), "LDN_ORB": set()}
    orb_na_days: dict[str, set] = {"NY_ORB": set(), "LDN_ORB": set()}
    orb_na_journal_had_orb: dict[str, set] = {"NY_ORB": set(), "LDN_ORB": set()}
    per_row: list[dict] = []

    for f in sorted(glob.glob(os.path.join(args.logs_root, "journal_*.jsonl"))):
        day = os.path.basename(f)[8:18]
        if day < args.start:
            continue
        for r in _read_jsonl(f):
            if not _is_decision_row(r) or r.get("instrument") not in INSTRUMENTS:
                continue
            if r.get("timeframe_minutes") == 5:
                stats["skipped_5m"] += 1
                continue
            ctx = r.get("context") if isinstance(r.get("context"), dict) else {}
            b0 = _parse_dt(ctx.get("timestamp"))
            if b0 is None or b0 >= end_ts:   # end exclusive: 2026-09-14T22:00Z is the first Z6 bar (roll cut)
                stats["skipped_out_of_window"] += 1
                continue
            inst = r["instrument"]
            stats["rows"] += 1
            stats[f"rows_{inst}"] += 1
            # live window: bars recorded up to and including B0, last 960 within 14 days
            lo = b0 - timedelta(days=LIVE_LOOKBACK_DAYS)
            win = [b for b in bars[inst] if lo <= b["ts"] <= b0]
            if not win:
                stats["rows_no_bars"] += 1
                continue
            if win[-1]["ts"] != b0:
                stats["rows_b0_bar_missing_from_history"] += 1
            try:
                ls = slf.build_levels(win, inst, b0_ts=b0)
            except Exception as exc:  # noqa: BLE001 — counted, never hidden
                stats["rows_build_failed"] += 1
                tally._ex("build_failed", f"{inst}@{b0.isoformat()}", str(exc), None)
                continue
            tick = ls.tick
            rid = f"{inst}@{b0.isoformat()}"
            L = ls.levels
            tday = f"{inst}:{slf._trading_day(b0).isoformat()}"

            def G(name: str) -> Optional[bool]:
                return L[name].gap_contaminated
            loc = ctx.get("location_context") or {}
            lv = loc.get("levels") or {}
            sess = r.get("session")

            # location_context levels (the collector's own PDH/PDL/PDC/ONH/ONL/PMH/PML)
            if loc:
                stats["rows_with_location_context"] += 1
                for mine, theirs in (("PDH", "pdh"), ("PDL", "pdl"), ("PDC", "prev_close"),
                                     ("ONH", "onh"), ("ONL", "onl"), ("PMH", "pmh"), ("PML", "pml")):
                    tally.cmp(f"loc.{mine}", L[mine].value, lv.get(theirs), tick, rid, gap_flag=G(mine), day=tday)
                tally.cmp("loc.MTR15", round(ls.mtr15, 4) if ls.mtr15 else None, loc.get("mtr_15m_points"), tick, rid)
                zj = loc.get("zones") or {}
                for tf in ("1h", "4h"):
                    for kind in ("supply", "demand"):
                        mine_z = (ls.zones_raw.get(tf) or {}).get(kind)
                        their_z = (zj.get(tf) or {}).get(kind)
                        if mine_z is None and their_z is None:
                            tally.cmp(f"zone.{tf}.{kind}", None, None, tick, rid)
                            continue
                        if mine_z is None or their_z is None:
                            tally.cmp(f"zone.{tf}.{kind}", mine_z and "zone", their_z and "zone", tick, rid, exact=True)
                            continue
                        same = (abs(mine_z["top"] - their_z["top"]) <= tick and abs(mine_z["bottom"] - their_z["bottom"]) <= tick
                                and mine_z["tests"] == their_z["tests"] and mine_z["broken"] == their_z["broken"]
                                and mine_z["formed_ts"] == their_z["formed_ts"])
                        tally.cmp(f"zone.{tf}.{kind}", "match" if same else f"{mine_z['top']}/{mine_z['bottom']}/t{mine_z['tests']}/{mine_z['formed_ts']}",
                                  "match" if same else f"{their_z['top']}/{their_z['bottom']}/t{their_z['tests']}/{their_z['formed_ts']}", tick, rid, exact=True)

            # Pine previous_day (payload) — informational secondary source
            pd = ctx.get("previous_day") or {}
            if pd.get("price_vs_pdh") not in (None, "undefined", "unknown"):
                tally.cmp("pine.PDH", L["PDH"].value, pd.get("high"), tick, rid)
                tally.cmp("pine.PDL", L["PDL"].value, pd.get("low"), tick, rid)
                tally.cmp("pine.PDC_daily_close", L["PDC"].value, pd.get("close"), tick, rid)

            # ORBs — canonical-bar denominator only
            orb = ctx.get("orb") or {}
            orb_defined = orb.get("status") not in (None, "undefined")
            for prefix, want_sess in (("NY_ORB", "new_york"), ("LDN_ORB", "london")):
                if sess != want_sess:
                    continue
                if L[f"{prefix}_H"].status == "NOT_AVAILABLE":
                    orb_na_days[prefix].add(tday)
                    if orb_defined:
                        orb_na_journal_had_orb[prefix].add(tday)
                    stats[f"{prefix}.rows_not_available"] += 1
                    continue
                orb_days[prefix].add(tday)
                if L[f"{prefix}_H"].status != "AVAILABLE":
                    stats[f"{prefix}.rows_outside_validity"] += 1   # e.g. the 09:30 bar itself
                    continue
                if not orb_defined:
                    stats[f"{prefix}.rows_journal_undefined"] += 1
                    continue
                tally.cmp(f"{prefix}.high", L[f"{prefix}_H"].value, orb.get("high"), tick, rid, gap_flag=False, day=tday)
                tally.cmp(f"{prefix}.low", L[f"{prefix}_L"].value, orb.get("low"), tick, rid, gap_flag=False, day=tday)

            # VWAP (also record whether ANY bar is missing in the current trading day so far —
            # VWAP is cumulative and volume-weighted, so a single missing bar can exceed one tick)
            vw = ctx.get("vwap") or {}
            if L["VWAP"].gap_minutes:
                stats["VWAP.rows_with_any_missing_bar_in_day"] += 1
            if vw.get("price_vs_vwap") not in (None, "undefined"):
                tally.cmp("VWAP", L["VWAP"].value, vw.get("value"), tick, rid, gap_flag=G("VWAP"), day=tday)

            # wall_context PWH/PWL/HOD/LOD (Pine weekly / trading-day extremes)
            wc = r.get("wall_context") or {}
            if wc:
                walls = {w.get("name"): w.get("value") for w in (wc.get("walls_above") or []) + (wc.get("walls_below") or [])}
                for n in ("PWH", "PWL", "HOD", "LOD"):
                    if n in walls:
                        tally.cmp(f"wall.{n}", L[n].value, walls[n], tick, rid, gap_flag=G(n), day=tday)

            if args.dump_rows:
                per_row.append({"row": rid, "session": sess, "levels": {k: v.value for k, v in L.items()},
                                "status": {k: v.status for k, v in L.items()}})

    rep = tally.report()
    verdict = {}
    admitted = {
        "loc.PDH": "PDH", "loc.PDL": "PDL", "loc.PDC": "PDC", "loc.ONH": "ONH", "loc.ONL": "ONL",
        "NY_ORB.high": "NY ORB high", "NY_ORB.low": "NY ORB low", "LDN_ORB.high": "London ORB high",
        "LDN_ORB.low": "London ORB low", "VWAP": "VWAP", "wall.PWH": "PWH", "wall.PWL": "PWL",
        "zone.1h.supply": "LC_ZONE 1H supply", "zone.1h.demand": "LC_ZONE 1H demand",
        "zone.4h.supply": "LC_ZONE 4H supply", "zone.4h.demand": "LC_ZONE 4H demand",
    }
    for k, label in admitted.items():
        e = rep.get(k)
        if not e or not e["compared"]:
            verdict[label] = "NOT_TESTABLE (no comparable rows)"
        else:
            verdict[label] = ("PASS" if e["pct"] >= PASS_PCT else "FAIL") + f" {e['pct']}% of {e['compared']}"
    out = {
        "tool_version": TOOL_VERSION, "prereg_version": slf.PREREG_VERSION, "prereg_sha": slf.PREREG_SHA,
        "logs_root": os.path.abspath(args.logs_root), "start": args.start, "end_ts": args.end_ts,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pass_threshold_pct": PASS_PCT, "tolerance": "one tick",
        "stats": dict(sorted(stats.items())),
        "orb_denominators": {p: {"session_days_with_canonical_bar": len(orb_days[p]),
                                 "session_days_not_available": len(orb_na_days[p]),
                                 "not_available_days_where_runtime_still_had_an_orb": len(orb_na_journal_had_orb[p]),
                                 "not_available_days": sorted(orb_na_days[p])} for p in orb_days},
        "comparisons": rep,
        "admitted_level_verdicts": verdict,
        "overall": "PASS" if all(v.startswith("PASS") for v in verdict.values() if not v.startswith("NOT_TESTABLE")) else "FAIL",
    }
    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "structural_level_parity.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    if args.dump_rows:
        with open(os.path.join(args.out_dir, "structural_level_parity_rows.jsonl"), "w") as fh:
            for row in per_row:
                fh.write(json.dumps(row, default=str) + "\n")
    return out


def _md(out: dict) -> str:
    lines = [f"# Structural-level parity (P3) — {out['generated_utc']}", "",
             f"logs_root `{out['logs_root']}` · window {out['start']} → {out['end_ts']} · tolerance {out['tolerance']} · pass ≥ {out['pass_threshold_pct']}%", "",
             f"rows: {out['stats'].get('rows', 0)} (MNQ {out['stats'].get('rows_MNQ', 0)} / MES {out['stats'].get('rows_MES', 0)}); build failures {out['stats'].get('rows_build_failed', 0)}; B0 bar missing from history {out['stats'].get('rows_b0_bar_missing_from_history', 0)}", "",
             "| level | verdict |", "|---|---|"]
    for k, v in out["admitted_level_verdicts"].items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "**ORB denominators (canonical bar present / NOT_AVAILABLE session-days):**"]
    for p, d in out["orb_denominators"].items():
        lines.append(f"- {p}: {d['session_days_with_canonical_bar']} comparable session-days; {d['session_days_not_available']} NOT_AVAILABLE "
                     f"(runtime still carried an ORB on {d['not_available_days_where_runtime_still_had_an_orb']} of them — designed divergence, excluded from the denominator)")
    lines += ["", "**All comparisons:**", "", "| key | compared | agree | pct | journal_missing | mine_missing | both_none |", "|---|---|---|---|---|---|---|"]
    for k, e in out["comparisons"].items():
        lines.append(f"| {k} | {e['compared']} | {e['agree']} | {e['pct']} | {e['journal_missing']} | {e['mine_missing']} | {e['both_none']} |")
    lines += ["", "**Disagreement decomposition (admitted levels that FAIL):**"]
    for k, e in out["comparisons"].items():
        if k in ("VWAP", "wall.PWH", "wall.PWL", "loc.PDH", "loc.PDL", "loc.PDC", "loc.ONH", "loc.ONL",
                 "NY_ORB.high", "NY_ORB.low", "LDN_ORB.high", "LDN_ORB.low") and e["pct"] is not None and e["pct"] < out["pass_threshold_pct"]:
            lines.append(f"- `{k}`: by gap flag {e['by_gap_flag']}; disagreements by trading day: {e['disagree_by_trading_day']}")
    lines += ["", f"**Overall: {out['overall']}**"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs-root", required=True)
    ap.add_argument("--start", default="2026-07-16")
    ap.add_argument("--end-ts", default="2026-09-14T22:00:00+00:00")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--dump-rows", action="store_true")
    args = ap.parse_args(argv)
    out = run(args)
    md = _md(out)
    with open(os.path.join(args.out_dir, "structural_level_parity.md"), "w") as fh:
        fh.write(md)
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
