#!/usr/bin/env python3
"""Preregistered LC_ZONE quality audit — 2026-09-18.

Offline research only.  Does not import strategy/risk/execution/webhook code.
See docs/prereg-lc-zone-quality-audit-2026-09-18.md.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from context.location_context import (
    IMPULSE_BODY_X,
    ZONE_LOOKBACK_BARS_1H,
    ZONE_LOOKBACK_BARS_4H,
    _median_true_range,
    aggregate,
    detect_zones,
    nearest_zones,
)

UTC = timezone.utc
TF_LOOKBACK = {60: ZONE_LOOKBACK_BARS_1H, 240: ZONE_LOOKBACK_BARS_4H}
EXPECTED_15M = {60: 4, 240: 16}
CONTROL_MIN = 0.20
CONTROL_MAX = 0.80
BOOT_SEED = 20260918
PREREG_COMMIT = "b4a1f48003da41602fad69e4435f43487bd42ff7"


def parse_ts(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v)).astimezone(UTC)


def load_bars(data_root: Path, instrument: str) -> list[dict]:
    rows: dict[datetime, dict] = {}
    for p in sorted((data_root / instrument).glob(f"{instrument}_*.jsonl")):
        for line in p.read_text().splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            ts = parse_ts(raw["timestamp"])
            rows[ts] = {
                "ts": ts,
                "open": float(raw["open"]),
                "high": float(raw["high"]),
                "low": float(raw["low"]),
                "close": float(raw["close"]),
            }
    return [rows[k] for k in sorted(rows)]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def bucket_start(ts: datetime, minutes: int) -> datetime:
    sec = minutes * 60
    key = int(ts.timestamp()) // sec
    return datetime.fromtimestamp(key * sec, tz=UTC)


def strict_aggregate(bars15: list[dict], minutes: int) -> list[dict]:
    """Only emit clock buckets containing every expected 15m bar."""
    expected = EXPECTED_15M[minutes]
    sec = minutes * 60
    groups: dict[int, list[dict]] = defaultdict(list)
    for b in bars15:
        groups[int(b["ts"].timestamp()) // sec].append(b)
    out = []
    for key in sorted(groups):
        arr = sorted(groups[key], key=lambda x: x["ts"])
        start = datetime.fromtimestamp(key * sec, tz=UTC)
        wanted = [start + timedelta(minutes=15 * i) for i in range(expected)]
        if len(arr) != expected or [b["ts"] for b in arr] != wanted:
            continue
        out.append({
            "ts": start,
            "open": arr[0]["open"],
            "high": max(b["high"] for b in arr),
            "low": min(b["low"] for b in arr),
            "close": arr[-1]["close"],
            "_count": expected,
        })
    return out


def z_identity(z: dict | None) -> tuple | None:
    if not z:
        return None
    return (
        z.get("kind"),
        int(z.get("timeframe_minutes") or 0),
        str(z.get("formed_ts")),
        round(float(z["top"]), 8),
        round(float(z["bottom"]), 8),
    )


def zone_partial_at_asof(z: dict | None, agg: list[dict], minutes: int, asof: datetime) -> bool:
    if not z:
        return False
    fts = parse_ts(z["formed_ts"])
    idx = next((i for i, b in enumerate(agg) if b["ts"] == fts), None)
    if idx is None or idx + 1 >= len(agg):
        return False
    imp_start = bucket_start(agg[idx + 1]["ts"], minutes)
    return imp_start + timedelta(minutes=minutes) > asof


def nearest_snapshot(bars15: list[dict], minutes: int, price: float, strict: bool) -> tuple[dict, list[dict]]:
    lookback = TF_LOOKBACK[minutes]
    agg = strict_aggregate(bars15, minutes) if strict else aggregate(bars15, minutes)
    agg = agg[-lookback:]
    zones = detect_zones(agg, minutes)
    return nearest_zones(zones, price), agg


def scan_gate0(instrument: str, bars: list[dict]) -> dict:
    """Compare current aggregate semantics with strict completed-HTF semantics."""
    out: dict[str, Any] = {"instrument": instrument, "timeframes": {}}
    for tf in (60, 240):
        per_kind = {
            k: {
                "asof_rows": 0,
                "comparable_rows": 0,
                "identity_disagreements": 0,
                "current_partial_nearest": 0,
                "boundary_disagreements_same_formed_ts": 0,
            }
            for k in ("supply", "demand")
        }
        churn: dict[tuple, list[tuple | None]] = defaultdict(list)

        for i, b in enumerate(bars):
            hist = bars[max(0, i - 959): i + 1]
            if len(hist) < 12:
                continue
            asof = b["ts"] + timedelta(minutes=15)
            cur, curagg = nearest_snapshot(hist, tf, b["close"], strict=False)
            strict, _ = nearest_snapshot(hist, tf, b["close"], strict=True)
            forming_bucket = bucket_start(b["ts"], tf)

            for kind in ("supply", "demand"):
                m = per_kind[kind]
                m["asof_rows"] += 1
                cz, sz = cur.get(kind), strict.get(kind)
                ci, si = z_identity(cz), z_identity(sz)
                if ci is not None or si is not None:
                    m["comparable_rows"] += 1
                    if ci != si:
                        m["identity_disagreements"] += 1
                if cz and sz:
                    if (
                        str(cz.get("formed_ts")) == str(sz.get("formed_ts"))
                        and (
                            abs(float(cz["top"]) - float(sz["top"])) > 1e-9
                            or abs(float(cz["bottom"]) - float(sz["bottom"])) > 1e-9
                        )
                    ):
                        m["boundary_disagreements_same_formed_ts"] += 1
                if zone_partial_at_asof(cz, curagg, tf, asof):
                    m["current_partial_nearest"] += 1
                churn[(forming_bucket, kind)].append(ci)

        churn_groups = 0
        churned = 0
        transitions = 0
        for seq in churn.values():
            if len(seq) < 2:
                continue
            churn_groups += 1
            t = sum(a != b for a, b in zip(seq, seq[1:]))
            transitions += t
            if t:
                churned += 1

        for kind, m in per_kind.items():
            c = m["comparable_rows"]
            m["identity_disagreement_rate"] = m["identity_disagreements"] / c if c else None
            m["partial_rate_of_asof"] = (
                m["current_partial_nearest"] / m["asof_rows"] if m["asof_rows"] else None
            )

        out["timeframes"][str(tf)] = {
            "by_kind": per_kind,
            "within_bucket_groups": churn_groups,
            "within_bucket_groups_with_nearest_zone_churn": churned,
            "within_bucket_churn_rate": churned / churn_groups if churn_groups else None,
            "nearest_zone_identity_transitions": transitions,
        }
    return out
def strict_series(bars: list[dict], tf: int) -> list[dict]:
    return strict_aggregate(bars, tf)


def formation_events(instrument: str, bars: list[dict], tf: int) -> tuple[list[dict], list[dict], dict]:
    """Create real impulse zones and preregistered non-impulse pseudo-zones."""
    htf = strict_series(bars, tf)
    lookback = TF_LOOKBACK[tf]
    real: list[dict] = []
    controls: list[dict] = []
    validation_mismatches = 0

    for i in range(1, len(htf)):
        window = htf[max(0, i - lookback + 1): i + 1]
        if len(window) < 3:
            continue
        mtr = _median_true_range(window)
        if not mtr:
            continue
        imp = htf[i]
        base = htf[i - 1]
        body = imp["close"] - imp["open"]
        ratio = abs(body) / mtr
        if body == 0:
            continue
        kind = "demand" if body > 0 else "supply"
        top, bottom = float(base["high"]), float(base["low"])
        near = top if kind == "demand" else bottom
        raw_dist = (imp["close"] - near) if kind == "demand" else (near - imp["close"])
        event = {
            "instrument": instrument,
            "timeframe_minutes": tf,
            "kind": kind,
            "base_idx": i - 1,
            "impulse_idx": i,
            "formed_ts": base["ts"],
            "impulse_ts": imp["ts"],
            "available_ts": imp["ts"] + timedelta(minutes=tf),
            "top": top,
            "bottom": bottom,
            "availability_close": float(imp["close"]),
            "mtr_htf": float(mtr),
            "impulse_body_mtr": float(ratio),
            "width_mtr": (top - bottom) / mtr,
            "distance_mtr": max(0.0, raw_dist) / mtr,
            "half_year": f"{imp['ts'].year}-H{1 if imp['ts'].month <= 6 else 2}",
            "bucket_hour": imp["ts"].hour,
        }
        if ratio >= IMPULSE_BODY_X:
            # Cross-check that the exact detector sees the just-formed identity.
            dz = detect_zones(window, tf)
            zid = (
                kind,
                round(top, 8),
                round(bottom, 8),
                base["ts"],
            )
            found = any(
                z["kind"] == zid[0]
                and round(float(z["top"]), 8) == zid[1]
                and round(float(z["bottom"]), 8) == zid[2]
                and z["formed_ts"] == zid[3]
                for z in dz
            )
            if not found:
                validation_mismatches += 1
            real.append(event)
        elif CONTROL_MIN <= ratio < CONTROL_MAX:
            # Pseudo-zones must already lie on the expected side at availability.
            side_ok = (top <= imp["close"]) if kind == "demand" else (bottom >= imp["close"])
            if side_ok:
                controls.append(event)

    return real, controls, {
        "htf_bars": len(htf),
        "real_zone_formations": len(real),
        "control_formations": len(controls),
        "formation_detector_crosscheck_mismatches": validation_mismatches,
    }


def attach_lifetime(events: list[dict], htf: list[dict], tf: int) -> None:
    lookback = TF_LOOKBACK[tf]
    for e in events:
        i = int(e["impulse_idx"])
        last_j = min(len(htf) - 1, i + lookback - 2)
        expiry = htf[last_j]["ts"] + timedelta(minutes=tf)
        break_ts = None
        for j in range(i + 1, last_j + 1):
            b = htf[j]
            broken = (
                b["close"] > e["top"]
                if e["kind"] == "supply"
                else b["close"] < e["bottom"]
            )
            if broken:
                break_ts = b["ts"] + timedelta(minutes=tf)
                break
        e["expiry_ts"] = expiry
        e["break_ts"] = break_ts
        e["valid_until"] = min(expiry, break_ts) if break_ts else expiry


def rolling_mtr15(bars: list[dict], idx: int) -> float | None:
    m = _median_true_range(bars[max(0, idx - 63): idx + 1])
    return float(m) if m else None


def contiguous_window(bars: list[dict], start: int, n: int) -> list[dict] | None:
    arr = bars[start:start + n]
    if len(arr) != n:
        return None
    for a, b in zip(arr, arr[1:]):
        if b["ts"] - a["ts"] != timedelta(minutes=15):
            return None
    return arr


def reaction_from_touch(bars: list[dict], idx: int, e: dict) -> dict:
    mtr15 = rolling_mtr15(bars, idx)
    window = contiguous_window(bars, idx, 8)
    if not mtr15 or window is None:
        return {
            "reaction_complete": False,
            "mtr15_touch": mtr15,
        }

    near = e["top"] if e["kind"] == "demand" else e["bottom"]
    far = e["bottom"] if e["kind"] == "demand" else e["top"]

    def clean(threshold_mtr: float) -> bool:
        threshold = threshold_mtr * mtr15
        for b in window:
            broke = b["close"] < far if e["kind"] == "demand" else b["close"] > far
            if broke:
                return False
            reached = (
                b["high"] >= near + threshold
                if e["kind"] == "demand"
                else b["low"] <= near - threshold
            )
            if reached:
                return True
        return False

    close_through = any(
        (b["close"] < far if e["kind"] == "demand" else b["close"] > far)
        for b in window
    )
    sign = 1.0 if e["kind"] == "demand" else -1.0
    ret_1h = sign * (window[3]["close"] - near) / mtr15
    ret_2h = sign * (window[7]["close"] - near) / mtr15
    if e["kind"] == "demand":
        mfe = max(0.0, max(b["high"] for b in window) - near) / mtr15
        penetration = max(0.0, near - min(b["low"] for b in window)) / mtr15
    else:
        mfe = max(0.0, near - min(b["low"] for b in window)) / mtr15
        penetration = max(0.0, max(b["high"] for b in window) - near) / mtr15

    return {
        "reaction_complete": True,
        "mtr15_touch": mtr15,
        "clean_rejection_05": clean(0.5),
        "clean_rejection_10": clean(1.0),
        "close_through_2h": close_through,
        "return_1h_mtr": ret_1h,
        "return_2h_mtr": ret_2h,
        "mfe_2h_mtr": mfe,
        "penetration_2h_mtr": penetration,
    }
def attach_first_touch(events: list[dict], bars: list[dict]) -> None:
    times = [b["ts"] for b in bars]
    for e in events:
        start = bisect.bisect_left(times, e["available_ts"])
        end = bisect.bisect_left(times, e["valid_until"])
        touch_idx = None
        for idx in range(start, min(end, len(bars))):
            b = bars[idx]
            if b["low"] <= e["top"] and b["high"] >= e["bottom"]:
                touch_idx = idx
                break
        if touch_idx is None:
            e["touched"] = False
            e["touch_ts"] = None
            e["reaction"] = None
        else:
            e["touched"] = True
            e["touch_ts"] = bars[touch_idx]["ts"]
            e["reaction"] = reaction_from_touch(bars, touch_idx, e)


def attach_test_ordinal_reactions(events: list[dict], bars15: list[dict], htf: list[dict], tf: int) -> list[dict]:
    times = [b["ts"] for b in bars15]
    rows: list[dict] = []
    for e in events:
        i = int(e["impulse_idx"])
        ordinal = 0
        for j in range(i + 1, len(htf)):
            hb = htf[j]
            bucket_end = hb["ts"] + timedelta(minutes=tf)
            if hb["ts"] >= e["valid_until"]:
                break
            overlaps = hb["low"] <= e["top"] and hb["high"] >= e["bottom"]
            if overlaps:
                ordinal += 1
                a = bisect.bisect_left(times, hb["ts"])
                bnd = bisect.bisect_left(times, bucket_end)
                tidx = next(
                    (
                        k for k in range(a, min(bnd, len(bars15)))
                        if bars15[k]["low"] <= e["top"] and bars15[k]["high"] >= e["bottom"]
                    ),
                    None,
                )
                if tidx is not None:
                    r = reaction_from_touch(bars15, tidx, e)
                    rows.append({
                        "instrument": e["instrument"],
                        "timeframe_minutes": tf,
                        "kind": e["kind"],
                        "test_ordinal": ordinal,
                        "bucket": "1_fresh" if ordinal == 1 else ("2" if ordinal == 2 else "3plus"),
                        "reaction": r,
                    })
            broken = hb["close"] > e["top"] if e["kind"] == "supply" else hb["close"] < e["bottom"]
            if broken:
                break
    return rows


def feature_scalers(real: list[dict], controls: list[dict]) -> dict[tuple, tuple[float, float]]:
    vals: dict[tuple, list[tuple[float, float]]] = defaultdict(list)
    for e in real + controls:
        key = (e["instrument"], e["timeframe_minutes"], e["kind"], e["half_year"])
        vals[key].append((e["width_mtr"], e["distance_mtr"]))
    out = {}
    for k, arr in vals.items():
        ws = [x[0] for x in arr]
        ds = [x[1] for x in arr]
        sw = statistics.pstdev(ws) or 1.0
        sd = statistics.pstdev(ds) or 1.0
        out[k] = (sw, sd)
    return out


def match_controls(real: list[dict], controls: list[dict]) -> tuple[list[dict], dict]:
    scalers = feature_scalers(real, controls)
    pool: dict[tuple, list[int]] = defaultdict(list)
    for i, c in enumerate(controls):
        key = (c["instrument"], c["timeframe_minutes"], c["kind"], c["half_year"])
        pool[key].append(i)

    used: set[int] = set()
    pairs: list[dict] = []
    exact_hour = relaxed = unmatched = 0

    for r in sorted(real, key=lambda x: (x["available_ts"], x["instrument"], x["timeframe_minutes"], x["kind"])):
        key = (r["instrument"], r["timeframe_minutes"], r["kind"], r["half_year"])
        cand = [i for i in pool.get(key, []) if i not in used and controls[i]["bucket_hour"] == r["bucket_hour"]]
        mode = "exact_hour"
        if not cand:
            cand = [i for i in pool.get(key, []) if i not in used]
            mode = "relaxed_hour"
        if not cand:
            unmatched += 1
            continue
        sw, sd = scalers[key]
        def dist(i: int) -> tuple[float, datetime]:
            c = controls[i]
            d = ((r["width_mtr"] - c["width_mtr"]) / sw) ** 2 + ((r["distance_mtr"] - c["distance_mtr"]) / sd) ** 2
            return d, c["available_ts"]
        ci = min(cand, key=dist)
        used.add(ci)
        if mode == "exact_hour":
            exact_hour += 1
        else:
            relaxed += 1
        pairs.append({"real": r, "control": controls[ci], "match_mode": mode, "distance": dist(ci)[0]})

    return pairs, {
        "real_zones": len(real),
        "controls": len(controls),
        "matched": len(pairs),
        "match_rate": len(pairs) / len(real) if real else None,
        "exact_hour_matches": exact_hour,
        "relaxed_hour_matches": relaxed,
        "unmatched": unmatched,
    }


def paired_primary_rows(pairs: list[dict]) -> list[dict]:
    out = []
    for p in pairs:
        rr = p["real"].get("reaction")
        cr = p["control"].get("reaction")
        if (
            p["real"].get("touched")
            and p["control"].get("touched")
            and rr
            and cr
            and rr.get("reaction_complete")
            and cr.get("reaction_complete")
        ):
            out.append(p)
    return out


def bootstrap_ci(diffs: list[float], nboot: int = 10000) -> tuple[float | None, float | None]:
    if not diffs:
        return None, None
    rng = random.Random(BOOT_SEED + len(diffs))
    n = len(diffs)
    means = []
    for _ in range(nboot):
        means.append(sum(diffs[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    lo = means[int(0.025 * (nboot - 1))]
    hi = means[int(0.975 * (nboot - 1))]
    return lo, hi


def summarize_pairs(pairs: list[dict]) -> dict:
    rows = paired_primary_rows(pairs)
    if not rows:
        return {"n": 0}
    a05 = [1.0 if p["real"]["reaction"]["clean_rejection_05"] else 0.0 for p in rows]
    c05 = [1.0 if p["control"]["reaction"]["clean_rejection_05"] else 0.0 for p in rows]
    diffs = [a - c for a, c in zip(a05, c05)]
    lo, hi = bootstrap_ci(diffs)
    def avg_side(side: str, key: str) -> float:
        vals = [float(p[side]["reaction"][key]) for p in rows]
        return sum(vals) / len(vals)
    return {
        "n": len(rows),
        "actual_clean_rejection_05_rate": sum(a05) / len(a05),
        "control_clean_rejection_05_rate": sum(c05) / len(c05),
        "uplift_pp": 100.0 * sum(diffs) / len(diffs),
        "uplift_95ci_pp": [100.0 * lo, 100.0 * hi] if lo is not None else None,
        "actual_clean_rejection_10_rate": avg_side("real", "clean_rejection_10"),
        "control_clean_rejection_10_rate": avg_side("control", "clean_rejection_10"),
        "actual_close_through_2h_rate": avg_side("real", "close_through_2h"),
        "control_close_through_2h_rate": avg_side("control", "close_through_2h"),
        "actual_return_1h_mtr_mean": avg_side("real", "return_1h_mtr"),
        "control_return_1h_mtr_mean": avg_side("control", "return_1h_mtr"),
        "actual_return_2h_mtr_mean": avg_side("real", "return_2h_mtr"),
        "control_return_2h_mtr_mean": avg_side("control", "return_2h_mtr"),
        "actual_mfe_2h_mtr_mean": avg_side("real", "mfe_2h_mtr"),
        "control_mfe_2h_mtr_mean": avg_side("control", "mfe_2h_mtr"),
        "actual_penetration_2h_mtr_mean": avg_side("real", "penetration_2h_mtr"),
        "control_penetration_2h_mtr_mean": avg_side("control", "penetration_2h_mtr"),
    }


def filtered(pairs: list[dict], **kwargs: Any) -> list[dict]:
    return [
        p for p in pairs
        if all(p["real"].get(k) == v and p["control"].get(k) == v for k, v in kwargs.items())
    ]


def touch_probability(pairs: list[dict]) -> dict:
    if not pairs:
        return {"n": 0}
    return {
        "n": len(pairs),
        "actual_touch_rate": sum(bool(p["real"].get("touched")) for p in pairs) / len(pairs),
        "control_touch_rate": sum(bool(p["control"].get("touched")) for p in pairs) / len(pairs),
    }


def summarize_freshness(rows: list[dict]) -> dict:
    out = {}
    for bucket in ("1_fresh", "2", "3plus"):
        vals = [r for r in rows if r["bucket"] == bucket and r["reaction"].get("reaction_complete")]
        out[bucket] = {
            "n": len(vals),
            "clean_rejection_05_rate": (
                sum(bool(r["reaction"]["clean_rejection_05"]) for r in vals) / len(vals)
                if vals else None
            ),
            "clean_rejection_10_rate": (
                sum(bool(r["reaction"]["clean_rejection_10"]) for r in vals) / len(vals)
                if vals else None
            ),
            "close_through_2h_rate": (
                sum(bool(r["reaction"]["close_through_2h"]) for r in vals) / len(vals)
                if vals else None
            ),
        }
    return out
def qualification_churn(strict_htf: list[dict], tf: int) -> dict:
    """Quantify rolling-MTR retroactive appearance/disappearance on completed HTF bars."""
    lookback = TF_LOOKBACK[tf]
    first_seen: dict[tuple, int] = {}
    meta: dict[tuple, dict] = {}
    absence_rows = 0
    eligible_alive_rows = 0
    reappeared: set[tuple] = set()
    late_appearance: set[tuple] = set()
    was_absent: dict[tuple, bool] = {}

    full_index = {b["ts"]: i for i, b in enumerate(strict_htf)}

    for j in range(len(strict_htf)):
        window = strict_htf[max(0, j - lookback + 1): j + 1]
        zones = detect_zones(window, tf)
        ids = set()
        for z in zones:
            zid = z_identity(z)
            if zid is None:
                continue
            ids.add(zid)
            if zid not in first_seen:
                first_seen[zid] = j
                base_ts = parse_ts(z["formed_ts"])
                base_idx = full_index.get(base_ts)
                meta[zid] = {
                    "kind": z["kind"],
                    "top": float(z["top"]),
                    "bottom": float(z["bottom"]),
                    "base_idx": base_idx,
                }
                if base_idx is not None and j > base_idx + 1:
                    late_appearance.add(zid)

        for zid, m in list(meta.items()):
            base_idx = m["base_idx"]
            if base_idx is None:
                continue
            impulse_idx = base_idx + 1
            if j < impulse_idx or j > impulse_idx + lookback - 2:
                continue
            # Independent broken test through current completed HTF bar.
            broken = False
            for b in strict_htf[impulse_idx + 1:j + 1]:
                if m["kind"] == "supply" and b["close"] > m["top"]:
                    broken = True
                    break
                if m["kind"] == "demand" and b["close"] < m["bottom"]:
                    broken = True
                    break
            if broken:
                continue
            eligible_alive_rows += 1
            present = zid in ids
            if not present:
                absence_rows += 1
                was_absent[zid] = True
            elif was_absent.get(zid):
                reappeared.add(zid)
                was_absent[zid] = False

    return {
        "unique_detected_zone_identities": len(first_seen),
        "late_retroactive_appearance_identities": len(late_appearance),
        "late_appearance_rate": len(late_appearance) / len(first_seen) if first_seen else None,
        "expected_alive_identity_rows": eligible_alive_rows,
        "qualification_absence_rows": absence_rows,
        "qualification_absence_rate": absence_rows / eligible_alive_rows if eligible_alive_rows else None,
        "reappeared_identities": len(reappeared),
    }


def scan_4hr_artifact_partial(repo_root: Path) -> dict:
    p = repo_root / "scripts/4hr_target_zone_geometry_2026-09-18.json"
    if not p.exists():
        return {"available": False}
    d = json.loads(p.read_text())
    rows = []
    partial = []
    for r in d.get("rows", []):
        z = r.get("exact_opposing_zone")
        if not z:
            continue
        entry = datetime.fromisoformat(r["entry_time"])
        formed = datetime.fromisoformat(z["formed_ts"])
        tf = int(z["timeframe_minutes"])
        # Base bucket starts at formed_ts; defining impulse is the next HTF bucket.
        impulse_complete = formed + timedelta(minutes=2 * tf)
        is_partial = entry < impulse_complete
        row = {
            "day": r.get("day"),
            "entry_time": r["entry_time"],
            "zone_tf": tf,
            "zone_formed_ts": z["formed_ts"],
            "impulse_complete_ts": impulse_complete.isoformat(),
            "used_before_impulse_complete": is_partial,
            "target_vs_exact_zone": r.get("target_vs_exact_zone"),
        }
        rows.append(row)
        if is_partial:
            partial.append(row)
    return {
        "available": True,
        "rows_with_exact_zone": len(rows),
        "partial_zone_rows": len(partial),
        "partial_rows": partial,
    }


def classify_quality(summary: dict, by_instrument: dict, by_tf: dict) -> str:
    if not summary.get("n"):
        return "NO EVIDENCE OF ZONE QUALITY"
    uplift = summary["uplift_pp"] / 100.0
    lo, hi = [x / 100.0 for x in summary["uplift_95ci_pp"]]
    inst_u = [v.get("uplift_pp", 0.0) / 100.0 for v in by_instrument.values() if v.get("n")]
    tf_u = [v.get("uplift_pp", 0.0) / 100.0 for v in by_tf.values() if v.get("n")]

    supported = (
        uplift >= 0.05
        and lo > 0
        and len(inst_u) == 2
        and all(x > 0 for x in inst_u)
        and len(tf_u) == 2
        and all(x >= -0.02 for x in tf_u)
    )
    if supported:
        return "QUALITY SIGNAL SUPPORTED"
    if uplift > 0 and (lo <= 0 or any(x <= 0 for x in inst_u)):
        return "PROMISING BUT UNPROVEN"
    if abs(uplift) < 0.02:
        return "NO EVIDENCE OF ZONE QUALITY"
    if uplift <= -0.05 and hi < 0:
        return "BROKEN AS A REACTION-AREA DETECTOR"
    return "NO EVIDENCE OF ZONE QUALITY"


def serial_event(e: dict) -> dict:
    out = {}
    for k, v in e.items():
        if k in {"base_idx", "impulse_idx"}:
            continue
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif k == "reaction":
            out[k] = v
        elif k in {"expiry_ts", "break_ts", "valid_until", "touch_ts"}:
            out[k] = v.isoformat() if isinstance(v, datetime) else None
        else:
            out[k] = v
    return out


def run(data_root: Path, repo_root: Path) -> dict:
    instruments = ("MNQ", "MES")
    bars_by_inst = {inst: load_bars(data_root, inst) for inst in instruments}

    gate0 = {
        "per_instrument": {},
        "four_hr_artifact_partial_check": scan_4hr_artifact_partial(repo_root),
        "qualification_churn": {},
    }
    all_real = []
    all_controls = []
    formation_meta = {}
    freshness_rows = []

    for inst in instruments:
        bars = bars_by_inst[inst]
        gate0["per_instrument"][inst] = scan_gate0(inst, bars)
        formation_meta[inst] = {}
        for tf in (60, 240):
            htf = strict_series(bars, tf)
            gate0["qualification_churn"][f"{inst}_{tf}"] = qualification_churn(htf, tf)
            real, controls, meta = formation_events(inst, bars, tf)
            attach_lifetime(real, htf, tf)
            attach_lifetime(controls, htf, tf)
            attach_first_touch(real, bars)
            attach_first_touch(controls, bars)
            freshness_rows.extend(attach_test_ordinal_reactions(real, bars, htf, tf))
            all_real.extend(real)
            all_controls.extend(controls)
            formation_meta[inst][str(tf)] = meta

    pairs, match_meta = match_controls(all_real, all_controls)

    combined = summarize_pairs(pairs)
    by_inst = {inst: summarize_pairs(filtered(pairs, instrument=inst)) for inst in instruments}
    by_tf = {str(tf): summarize_pairs(filtered(pairs, timeframe_minutes=tf)) for tf in (60, 240)}
    by_kind = {kind: summarize_pairs(filtered(pairs, kind=kind)) for kind in ("supply", "demand")}
    by_inst_tf = {
        f"{inst}_{tf}": summarize_pairs(filtered(pairs, instrument=inst, timeframe_minutes=tf))
        for inst in instruments for tf in (60, 240)
    }
    touch = {
        "combined": touch_probability(pairs),
        "by_instrument": {inst: touch_probability(filtered(pairs, instrument=inst)) for inst in instruments},
        "by_timeframe": {str(tf): touch_probability(filtered(pairs, timeframe_minutes=tf)) for tf in (60, 240)},
    }
    classification = classify_quality(combined, by_inst, by_tf)

    # Gate-0 blocking condition.
    max_disagree = 0.0
    for inst in instruments:
        for tf in ("60", "240"):
            for kind in ("supply", "demand"):
                rate = gate0["per_instrument"][inst]["timeframes"][tf]["by_kind"][kind]["identity_disagreement_rate"]
                if rate is not None:
                    max_disagree = max(max_disagree, rate)
    partial_4hr = gate0["four_hr_artifact_partial_check"].get("partial_zone_rows", 0)
    gate0["max_nearest_identity_disagreement_rate"] = max_disagree
    gate0["blocking_rule_triggered"] = bool(max_disagree >= 0.01 or partial_4hr > 0)
    gate0["verdict"] = (
        "UNSAFE FOR TARGET-RULE VALIDATION"
        if gate0["blocking_rule_triggered"]
        else "COMPLETION SEMANTICS PASS"
    )

    manifest_hashes = {}
    for inst in instruments:
        mp = data_root / inst / "MANIFEST.json"
        manifest_hashes[inst] = sha256_file(mp) if mp.exists() else None

    result = {
        "status": "AUDIT_ONLY",
        "prereg_commit": PREREG_COMMIT,
        "source": {
            "data_root": str(data_root),
            "instruments": list(instruments),
            "bars": {k: len(v) for k, v in bars_by_inst.items()},
            "date_ranges": {
                k: [v[0]["ts"].isoformat(), v[-1]["ts"].isoformat()] for k, v in bars_by_inst.items()
            },
            "manifest_sha256": manifest_hashes,
            "impulse_body_x": IMPULSE_BODY_X,
            "lookbacks": TF_LOOKBACK,
            "control_body_mtr_band": [CONTROL_MIN, CONTROL_MAX],
        },
        "gate0": gate0,
        "formation": formation_meta,
        "matching": match_meta,
        "quality": {
            "classification": classification,
            "combined": combined,
            "by_instrument": by_inst,
            "by_timeframe": by_tf,
            "by_kind": by_kind,
            "by_instrument_timeframe": by_inst_tf,
            "touch_probability": touch,
            "freshness": summarize_freshness(freshness_rows),
        },
        "pairs": [
            {
                "match_mode": p["match_mode"],
                "match_distance": p["distance"],
                "real": serial_event(p["real"]),
                "control": serial_event(p["control"]),
            }
            for p in pairs
        ],
    }
    return result


def pct(x: float | None) -> str:
    return "n/a" if x is None else f"{100*x:.2f}%"


def render_md(result: dict) -> str:
    g = result["gate0"]
    q = result["quality"]
    lines = [
        "# LC_ZONE Quality Audit — 2026-09-18",
        "",
        "## Verdict",
        "",
        f"**Zone-quality classification: {q['classification']}**",
        "",
        f"**HTF completion semantics: {g['verdict']}**",
        "",
        "Offline diagnostic only. No runtime, strategy, risk, target, broker, or execution rule was changed.",
        "",
        "## Gate 0 — current vs completed higher-timeframe semantics",
        "",
        f"Maximum nearest-zone identity disagreement rate: **{pct(g['max_nearest_identity_disagreement_rate'])}**.",
        f"4HR target-geometry rows that used a zone before its defining impulse HTF candle completed: **{g['four_hr_artifact_partial_check'].get('partial_zone_rows', 'n/a')}**.",
        "",
    ]
    for inst in ("MNQ", "MES"):
        lines.append(f"### {inst}")
        lines.append("")
        lines.append("| TF | Kind | Comparable | Identity disagree | Partial current-nearest |")
        lines.append("|---|---|---:|---:|---:|")
        for tf in ("60", "240"):
            for kind in ("supply", "demand"):
                m = g["per_instrument"][inst]["timeframes"][tf]["by_kind"][kind]
                lines.append(
                    f"| {tf}m | {kind} | {m['comparable_rows']} | "
                    f"{pct(m['identity_disagreement_rate'])} | {m['current_partial_nearest']} |"
                )
        lines.append("")
        lines.append("Rolling-MTR qualification churn:")
        for tf in ("60", "240"):
            c = g["qualification_churn"][f"{inst}_{tf}"]
            lines.append(
                f"- {tf}m: {c['unique_detected_zone_identities']} identities; "
                f"{c['late_retroactive_appearance_identities']} late retroactive appearances "
                f"({pct(c['late_appearance_rate'])}); "
                f"qualification absent on {pct(c['qualification_absence_rate'])} of expected-alive identity rows; "
                f"{c['reappeared_identities']} identities reappeared."
            )
        lines.append("")

    lines += [
        "## Matched reaction-area quality",
        "",
        f"Real zones: **{result['matching']['real_zones']}**; controls: **{result['matching']['controls']}**; "
        f"matched: **{result['matching']['matched']}** ({pct(result['matching']['match_rate'])}).",
        "",
        "Primary metric: clean 0.5-MTR rejection within 2h, with far-edge close-through counted first on ambiguous bars.",
        "",
        "| Population | n paired touches | Actual | Control | Uplift | 95% CI |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    def row(name: str, s: dict) -> str:
        if not s.get("n"):
            return f"| {name} | 0 | n/a | n/a | n/a | n/a |"
        ci = s["uplift_95ci_pp"]
        return (
            f"| {name} | {s['n']} | {pct(s['actual_clean_rejection_05_rate'])} | "
            f"{pct(s['control_clean_rejection_05_rate'])} | {s['uplift_pp']:+.2f} pp | "
            f"[{ci[0]:+.2f}, {ci[1]:+.2f}] pp |"
        )
    lines.append(row("Combined", q["combined"]))
    for inst in ("MNQ", "MES"):
        lines.append(row(inst, q["by_instrument"][inst]))
    for tf in ("60", "240"):
        lines.append(row(f"{tf}m", q["by_timeframe"][tf]))
    for kind in ("supply", "demand"):
        lines.append(row(kind, q["by_kind"][kind]))
    lines += ["", "Instrument x timeframe:"]
    for key, s in q["by_instrument_timeframe"].items():
        lines.append(f"- {key}: " + (f"n={s['n']}, uplift {s['uplift_pp']:+.2f} pp" if s.get("n") else "n=0"))

    c = q["combined"]
    if c.get("n"):
        lines += [
            "",
            "Secondary combined metrics:",
            f"- 1.0-MTR clean rejection: actual {pct(c['actual_clean_rejection_10_rate'])}, control {pct(c['control_clean_rejection_10_rate'])}.",
            f"- far-edge close-through within 2h: actual {pct(c['actual_close_through_2h_rate'])}, control {pct(c['control_close_through_2h_rate'])}.",
            f"- mean directional close return +1h: actual {c['actual_return_1h_mtr_mean']:+.3f} MTR, control {c['control_return_1h_mtr_mean']:+.3f}.",
            f"- mean directional close return +2h: actual {c['actual_return_2h_mtr_mean']:+.3f} MTR, control {c['control_return_2h_mtr_mean']:+.3f}.",
            f"- mean 2h MFE: actual {c['actual_mfe_2h_mtr_mean']:.3f} MTR, control {c['control_mfe_2h_mtr_mean']:.3f}.",
            f"- mean 2h penetration: actual {c['actual_penetration_2h_mtr_mean']:.3f} MTR, control {c['control_penetration_2h_mtr_mean']:.3f}.",
        ]
    lines += ["", "## Freshness / repeated tests (descriptive only)", ""]
    for bucket, s in q["freshness"].items():
        lines.append(
            f"- {bucket}: n={s['n']}, clean 0.5-MTR rejection {pct(s['clean_rejection_05_rate'])}, "
            f"clean 1.0-MTR {pct(s['clean_rejection_10_rate'])}, close-through {pct(s['close_through_2h_rate'])}."
        )
    lines += [
        "",
        "## Interpretation rule",
        "",
        "This report follows the preregistered classification exactly. It does not tune the impulse threshold, control band, reaction thresholds, matching features, or instrument/timeframe population after reading outcomes.",
        "",
        "Any alternative zone boundary (body-only, proximal/distal, multi-bar base, BOS/volume confirmation) requires a separate preregistered follow-up.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--repo-root", type=Path, default=Path("."))
    ap.add_argument("--json-out", type=Path, required=True)
    ap.add_argument("--md-out", type=Path, required=True)
    args = ap.parse_args()

    result = run(args.data_root, args.repo_root.resolve())
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.md_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(result, indent=2, default=str) + "\n")
    args.md_out.write_text(render_md(result) + "\n")
    print(json.dumps({
        "gate0": result["gate0"]["verdict"],
        "max_disagreement": result["gate0"]["max_nearest_identity_disagreement_rate"],
        "partial_4hr": result["gate0"]["four_hr_artifact_partial_check"].get("partial_zone_rows"),
        "classification": result["quality"]["classification"],
        "combined": result["quality"]["combined"],
        "matching": result["matching"],
    }, indent=2))


if __name__ == "__main__":
    main()
