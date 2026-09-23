"""Prereg #929 step 0 parity checks (0a-0d). Never reads forward P&L.

0a / 0b  OHLC parity, box bars vs a frozen corpus, on the prereg overlap dates:
         PASS iff >= 99.5% of overlapping bars match O/H/L/C within 1 tick AND
         no RTH gap of > 2 consecutive bars in the box series that the corpus
         does not also have.
0c       Candidate parity: each frozen #915 family adapter over the rebuilt
         overlap corpus vs over the frozen corpus. Identical candidate
         identities, signal timestamps, directions, entry, stop, target. Every
         mismatch is listed; any mismatch without a reviewed explanation fails.
0d       4HR-audit lineage: the 4HR, 3-2-2, Miyagi and Daily adapters over
         replay_corpus_v1_5m vs replay_corpus_v1_5m_4hr_audit on shared dates.

This module only compares candidate/event identity fields. Adapter P&L and the
#915 control numbers are never read (controls are disabled off the frozen
corpus and their diagnostics discarded by the harness).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from research.prereg929_forward_corpus import LoadedBars
from research.prereg929_forward_portfolio import AdapterRun

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
TICK = 0.25
MATCH_RATE_MIN = 0.995
MAX_RTH_GAP_BARS = 2
PRICE_TOL = 1e-6

RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)


def is_rth_bar_start(ts: datetime, minutes: int) -> bool:
    et = ts.astimezone(ET)
    if et.weekday() >= 5:
        return False
    start = et.time()
    end_dt = et + timedelta(minutes=minutes)
    return start >= RTH_OPEN and (end_dt.time() <= RTH_CLOSE and end_dt.date() == et.date())


def _load_corpus_ohlc(root: Path, start: date, end: date) -> dict[int, tuple]:
    out: dict[int, tuple] = {}
    for path in sorted((Path(root) / "MNQ").glob("MNQ_*.jsonl")):
        day = path.stem.removeprefix("MNQ_")
        if not (start.isoformat() <= day <= end.isoformat()):
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            ts = int(datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).timestamp())
            out[ts] = (float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]))
    return out


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def ohlc_parity(
    box: LoadedBars,
    corpus_root: Path,
    start: date,
    end: date,
    *,
    label: str,
) -> dict:
    """0a/0b. Dates are UTC calendar days inclusive (corpus file naming)."""
    minutes = box.timeframe_minutes
    corpus = _load_corpus_ohlc(corpus_root, start, end)
    boxmap = {
        b["ts"]: (b["open"], b["high"], b["low"], b["close"])
        for b in box.bars
        if start.isoformat() <= _iso(b["ts"])[:10] <= end.isoformat()
    }
    overlap = sorted(set(corpus) & set(boxmap))
    mismatches = []
    for ts in overlap:
        c, b = corpus[ts], boxmap[ts]
        diffs = [abs(x - y) for x, y in zip(c, b)]
        if max(diffs) > TICK + 1e-9:
            mismatches.append({
                "ts": _iso(ts),
                "corpus_ohlc": list(c),
                "box_ohlc": list(b),
                "max_abs_diff": round(max(diffs), 6),
                "rth": is_rth_bar_start(datetime.fromtimestamp(ts, tz=UTC), minutes),
            })
    rate = (len(overlap) - len(mismatches)) / len(overlap) if overlap else 0.0

    # RTH gaps: corpus RTH bars absent from the box series, grouped into
    # consecutive runs (consecutive = adjacent in the corpus RTH sequence of
    # the same ET session date).
    rth_corpus = [ts for ts in sorted(corpus) if is_rth_bar_start(datetime.fromtimestamp(ts, tz=UTC), minutes)]
    runs: list[list[int]] = []
    current: list[int] = []
    prev_ts: Optional[int] = None
    for ts in rth_corpus:
        same_session = (
            prev_ts is not None
            and datetime.fromtimestamp(ts, tz=UTC).astimezone(ET).date()
            == datetime.fromtimestamp(prev_ts, tz=UTC).astimezone(ET).date()
        )
        if ts in boxmap:
            if current:
                runs.append(current)
            current = []
        else:
            if current and not same_session:
                runs.append(current)
                current = []
            current.append(ts)
        prev_ts = ts
    if current:
        runs.append(current)
    violations = [
        {"from": _iso(r[0]), "to": _iso(r[-1]), "bars": len(r),
         "et_date": datetime.fromtimestamp(r[0], tz=UTC).astimezone(ET).date().isoformat()}
        for r in runs if len(r) > MAX_RTH_GAP_BARS
    ]
    short_gaps = sum(1 for r in runs if len(r) <= MAX_RTH_GAP_BARS)
    missing_rth = sum(len(r) for r in runs)
    box_only = sorted(set(boxmap) - set(corpus))
    verdict = "PASS" if overlap and rate >= MATCH_RATE_MIN and not violations else "FAIL"
    return {
        "check": label,
        "timeframe_minutes": minutes,
        "date_range": [start.isoformat(), end.isoformat()],
        "corpus_root": str(corpus_root),
        "corpus_bars": len(corpus),
        "box_bars": len(boxmap),
        "overlapping_bars": len(overlap),
        "matching_bars": len(overlap) - len(mismatches),
        "match_rate": round(rate, 6),
        "match_rate_threshold": MATCH_RATE_MIN,
        "ohlc_mismatches": mismatches,
        "corpus_rth_bars": len(rth_corpus),
        "corpus_rth_bars_missing_in_box": missing_rth,
        "rth_gap_runs_le_2_bars": short_gaps,
        "rth_gap_violations_gt_2_bars": violations,
        "box_bars_not_in_corpus": len(box_only),
        "box_bars_not_in_corpus_examples": [_iso(t) for t in box_only[:20]],
        "verdict": verdict,
    }


# ─── candidate stream comparison (0c / 0d) ───────────────────────────────────

def _event_key(e) -> dict:
    return {
        "signal_ts": e.signal_ts,
        "eligible_fill_ts": e.eligible_fill_ts,
        "direction": e.direction,
        "entry": float(e.entry),
        "stop": float(e.stop),
        "target": float(e.target),
    }


def _same(a: dict, b: dict) -> list[str]:
    diffs = []
    for k in a:
        x, y = a[k], b[k]
        if isinstance(x, float) or isinstance(y, float):
            if abs(float(x) - float(y)) > PRICE_TOL:
                diffs.append(k)
        elif x != y:
            diffs.append(k)
    return diffs


def compare_runs(
    check: str,
    left_name: str,
    left: AdapterRun,
    right_name: str,
    right: AdapterRun,
    families: Iterable[str],
) -> list[dict]:
    """List every candidate-stream difference between two adapter runs."""
    out: list[dict] = []
    for fam in families:
        lo, ro = left.families.get(fam), right.families.get(fam)
        for side, o in ((left_name, lo), (right_name, ro)):
            if o is None or o.status != "OK":
                out.append({
                    "id": f"{check}:{fam}:ADAPTER_FAILED_CLOSED:{side}",
                    "family": fam,
                    "kind": "ADAPTER_FAILED_CLOSED",
                    "side": side,
                    "error": None if o is None else o.error,
                })
        if not (lo and ro and lo.status == "OK" and ro.status == "OK"):
            continue
        la = {a["source_id"]: a["ts"] for a in lo.attempts}
        ra = {a["source_id"]: a["ts"] for a in ro.attempts}
        for sid in sorted(set(la) | set(ra)):
            if sid not in ra:
                out.append({"id": f"{check}:{fam}:ATTEMPT_ONLY_{left_name}:{sid}", "family": fam,
                            "kind": f"ATTEMPT_ONLY_IN_{left_name.upper()}", "source_id": sid, "ts": la[sid]})
            elif sid not in la:
                out.append({"id": f"{check}:{fam}:ATTEMPT_ONLY_{right_name}:{sid}", "family": fam,
                            "kind": f"ATTEMPT_ONLY_IN_{right_name.upper()}", "source_id": sid, "ts": ra[sid]})
            elif la[sid] != ra[sid]:
                out.append({"id": f"{check}:{fam}:ATTEMPT_TS:{sid}", "family": fam, "kind": "ATTEMPT_TS_DIFF",
                            "source_id": sid, left_name: la[sid], right_name: ra[sid]})
        le = {e.source_id: _event_key(e) for e in lo.events}
        re_ = {e.source_id: _event_key(e) for e in ro.events}
        for sid in sorted(set(le) | set(re_)):
            if sid not in re_:
                out.append({"id": f"{check}:{fam}:EVENT_ONLY_{left_name}:{sid}", "family": fam,
                            "kind": f"FILLABLE_ONLY_IN_{left_name.upper()}", "source_id": sid, left_name: le[sid]})
            elif sid not in le:
                out.append({"id": f"{check}:{fam}:EVENT_ONLY_{right_name}:{sid}", "family": fam,
                            "kind": f"FILLABLE_ONLY_IN_{right_name.upper()}", "source_id": sid, right_name: re_[sid]})
            else:
                d = _same(le[sid], re_[sid])
                if d:
                    out.append({"id": f"{check}:{fam}:EVENT_FIELDS:{sid}", "family": fam, "kind": "FIELD_DIFF",
                                "source_id": sid, "fields": d, left_name: le[sid], right_name: re_[sid]})
    return out


def family_stream_sizes(run: AdapterRun) -> dict:
    return {
        f: {"status": o.status, "attempts": len(o.attempts), "fillable_events": len(o.events), "error": o.error}
        for f, o in sorted(run.families.items())
    }


def apply_explanations(mismatches: list[dict], explanations: dict[str, str]) -> tuple[list[dict], list[dict]]:
    explained, unexplained = [], []
    for m in mismatches:
        text = (explanations or {}).get(m["id"])
        if text and str(text).strip():
            explained.append({**m, "explanation": str(text)})
        else:
            unexplained.append(m)
    return explained, unexplained


def candidate_check(
    label: str,
    mismatches: list[dict],
    explanations: dict[str, str],
    *,
    sizes: dict,
    extra_failures: list[dict] | None = None,
) -> dict:
    explained, unexplained = apply_explanations(mismatches, explanations)
    extra = list(extra_failures or [])
    verdict = "PASS" if not unexplained and not extra else "FAIL"
    return {
        "check": label,
        "streams": sizes,
        "mismatch_count": len(mismatches),
        "explained": explained,
        "unexplained": unexplained,
        "additional_failures": extra,
        "verdict": verdict,
    }


# ─── detector lineage helpers (Miyagi source proof, supplementary) ───────────

MIYAGI_CAND_FIELDS = ("date", "direction", "entry_trigger", "stop", "target", "target_2")


def compare_candidate_lists(left: list[dict], right: list[dict], fields: Iterable[str]) -> dict:
    fields = tuple(fields)

    def norm(c):
        return tuple(
            (round(float(c[f]), 8) if isinstance(c.get(f), (int, float)) else str(c.get(f)))
            for f in fields
        )

    ls, rs = {norm(c) for c in left}, {norm(c) for c in right}
    return {
        "left": len(left),
        "right": len(right),
        "only_left": [dict(zip(fields, x)) for x in sorted(ls - rs)],
        "only_right": [dict(zip(fields, x)) for x in sorted(rs - ls)],
        "identical": ls == rs and len(left) == len(right),
    }


def overall_verdict(checks: dict) -> str:
    return "PASS" if all(checks[c]["verdict"] == "PASS" for c in ("0a", "0b", "0c", "0d")) else "FAIL"


def summarize_text(report: dict) -> str:
    lines = [f"prereg #929 step 0 — overall {report['overall']}  ({report['generated_at']})"]
    c = report["checks"]
    for k in ("0a", "0b"):
        r = c[k]
        lines.append(
            f"{k} {r['verdict']}: {r['matching_bars']}/{r['overlapping_bars']} bars match "
            f"(rate {r['match_rate']:.4%}, need >= {MATCH_RATE_MIN:.1%}); "
            f"{len(r['ohlc_mismatches'])} OHLC mismatches; "
            f"{r['corpus_rth_bars_missing_in_box']} corpus RTH bars missing in box; "
            f"{len(r['rth_gap_violations_gt_2_bars'])} RTH gap runs > {MAX_RTH_GAP_BARS} bars"
        )
    for k in ("0c", "0d"):
        r = c[k]
        kinds = defaultdict(int)
        for m in r["unexplained"]:
            kinds[f"{m['family']}:{m['kind']}"] += 1
        lines.append(
            f"{k} {r['verdict']}: {r['mismatch_count']} mismatches "
            f"({len(r['unexplained'])} unexplained, {len(r['explained'])} explained); "
            f"additional failures {len(r['additional_failures'])}"
        )
        for kk, n in sorted(kinds.items()):
            lines.append(f"    {kk} x{n}")
        for f in r["additional_failures"]:
            lines.append(f"    additional: {f.get('what')}")
    for k, v in (report.get("supplementary") or {}).items():
        if "identical" in v:
            lines.append(f"supplementary {k}: identical={v.get('identical')} "
                         f"(left {v.get('left')}, right {v.get('right')})")
        else:
            lines.append(f"supplementary {k}: reproduces={v.get('reproduces_prereg_table')}")
    return "\n".join(lines) + "\n"
