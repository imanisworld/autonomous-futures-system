"""Extended metrics/robustness analysis for the MNQ 60M 3-2-2 expanded-evidence
study (2026-07-26). Scoped exclusively to the 60M 3-2-2 lane.

Consumes the "trades" list already produced by research.replay_322_honest_fill
(via research.run_322_expanded_evidence's group JSON reports) and computes the
additional slices Step 3 of the study brief requires beyond what
replay_322_honest_fill._metrics already reports per group/half/direction:
  - monthly distribution
  - yearly distribution
  - additional chronological partitions (quarters)
  - top 1/3/5 winner contribution
  - worst/best month
  - gap-open representation
  - market-condition / trend regime tagging (Robustness Question #10)

Regime tagging (Q10) reads `market_condition`/`trend_direction`/`trend_strength`
read-only, after trade outcomes are already final -- it never gates, filters,
or excludes any candidate/fill/signal. For candidate dates >= 2025-07-24 (the
start of data/replay_corpus_v1's coverage), the Pine-faithful
`reconstructed_market_condition`/`reconstructed_trend_direction` fields are
preferred and labeled canonical. For earlier dates, only the legacy
`market_condition` field (baked into data/replay_polygon*) is available; that
field is known-defective relative to the runtime/Pine formula (33,635/47,066
bars disagree system-wide) and is labeled diagnostic/directional only, never
canonical, per the 2026-07-26 operator traffic-control update on this task.

CONFIRMED CANONICAL (post-PR #338, main@0057bc2, 2026-07-26): the shared
market-condition parity fix's own regression proof established that
`reconstructed_market_condition` was already bit-identical to the corrected
engine-facing formula for every bar in data/replay_corpus_v1 (0 mismatches
across 47,066 bars) -- it simply wasn't wired into ReplayEngine before. The
values this module reads for candidate dates >= 2025-07-24 are therefore
genuinely canonical, not merely a best-effort proxy; the "diagnostic only"
label above applies only to the pre-2025-07-24 legacy-field portion, which PR
#338 did not and could not rematerialize (data/replay_polygon has no
reconstructed_* field to rematerialize from).
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import date
from pathlib import Path

from research.bars_322_polygon_loader import load_15m_day
from research.replay_322_honest_fill import _metrics


REPO_ROOT = Path(__file__).resolve().parents[1]
POLYGON_15M = REPO_ROOT / "data" / "replay_polygon"
CORPUS_V1_15M = REPO_ROOT / "data" / "replay_corpus_v1"
CORPUS_V1_START = date(2025, 7, 24)


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _quarter_key(d: date) -> str:
    q = (d.month - 1) // 3 + 1
    return f"{d.year:04d}-Q{q}"


def monthly_distribution(rows: list[dict]) -> dict:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[_month_key(date.fromisoformat(row["date"]))].append(row)
    return {key: _metrics(buckets[key]) for key in sorted(buckets)}


def yearly_distribution(rows: list[dict]) -> dict:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        d = date.fromisoformat(row["date"])
        buckets[str(d.year)].append(row)
    return {key: _metrics(buckets[key]) for key in sorted(buckets)}


def quarterly_distribution(rows: list[dict]) -> dict:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        buckets[_quarter_key(date.fromisoformat(row["date"]))].append(row)
    return {key: _metrics(buckets[key]) for key in sorted(buckets)}


def best_worst_month(monthly: dict) -> dict:
    scored = [(k, v["net_pnl"]) for k, v in monthly.items() if v.get("n")]
    if not scored:
        return {"best_month": None, "worst_month": None}
    best = max(scored, key=lambda kv: kv[1])
    worst = min(scored, key=lambda kv: kv[1])
    return {
        "best_month": {"month": best[0], "net_pnl": best[1]},
        "worst_month": {"month": worst[0], "net_pnl": worst[1]},
    }


def top_n_contribution(rows: list[dict]) -> dict:
    resolved = [r for r in rows if r.get("net_pnl") is not None]
    total_net = sum(r["net_pnl"] for r in resolved)
    winners = sorted(
        (r for r in resolved if r["net_pnl"] > 0), key=lambda r: -r["net_pnl"]
    )
    out = {}
    for n in (1, 3, 5):
        top = winners[:n]
        contribution = sum(r["net_pnl"] for r in top)
        out[f"top_{n}"] = {
            "trades": [{"date": r["date"], "direction": r["direction"], "net_pnl": r["net_pnl"]} for r in top],
            "contribution": contribution,
            "share_of_total_net_pnl": (contribution / total_net) if total_net else None,
        }
    return out


def gap_open_representation(rows: list[dict]) -> dict:
    gap = [r for r in rows if r.get("gap_open")]
    non_gap = [r for r in rows if not r.get("gap_open")]
    return {
        "gap_open_candidates": len(gap),
        "non_gap_candidates": len(non_gap),
        "gap_open_metrics": _metrics(gap) if gap else None,
        "non_gap_metrics": _metrics(non_gap) if non_gap else None,
    }


def _hour9_bar(bars_15m: list[dict], et_day: date):
    """The last 15m sub-bar within the 9AM ET hour (state closest to the 9AM
    setup bar's close, i.e. right before the 10AM live-break window opens)."""
    from zoneinfo import ZoneInfo

    ET = ZoneInfo("America/New_York")
    candidates = [b for b in bars_15m if b["ts"].astimezone(ET).date() == et_day and b["ts"].astimezone(ET).hour == 9]
    if not candidates:
        return None
    return sorted(candidates, key=lambda b: b["ts"])[-1]


def regime_tags_for_candidates(candidate_dates: list[date]) -> dict:
    """Read-only regime tagging per candidate date. Never used for gating."""
    out = {}
    for d in candidate_dates:
        legacy_bars = load_15m_day(POLYGON_15M, "MNQ", d)
        legacy_bar = _hour9_bar(legacy_bars, d)
        entry = {
            "date": d.isoformat(),
            "legacy_market_condition": legacy_bar.get("market_condition") if legacy_bar else None,
            "legacy_trend_direction": legacy_bar.get("trend_direction") if legacy_bar else None,
            "legacy_trend_strength": legacy_bar.get("trend_strength") if legacy_bar else None,
            "legacy_field_status": "PARITY_INVALID_DIAGNOSTIC_ONLY",
            "reconstructed_market_condition": None,
            "reconstructed_trend_direction": None,
            "reconstructed_field_status": "NOT_AVAILABLE",
        }
        if d >= CORPUS_V1_START:
            corpus_bars = load_15m_day(CORPUS_V1_15M, "MNQ", d)
            corpus_bar = _hour9_bar(corpus_bars, d)
            if corpus_bar and corpus_bar.get("reconstructed_market_condition") is not None:
                entry["reconstructed_market_condition"] = corpus_bar.get("reconstructed_market_condition")
                entry["reconstructed_trend_direction"] = corpus_bar.get("reconstructed_trend_direction")
                entry["reconstructed_field_status"] = "PINE_FAITHFUL_CANONICAL"
        out[d.isoformat()] = entry
    return out


def build_extended_report(group_report: dict) -> dict:
    base_case = group_report.get("base_case")
    if not base_case:
        return {"note": "empty group, no extended metrics"}
    rows = base_case["trades"]
    candidate_dates = sorted({date.fromisoformat(r["date"]) for r in rows})
    monthly = monthly_distribution(rows)
    return {
        "monthly_distribution": monthly,
        "yearly_distribution": yearly_distribution(rows),
        "quarterly_distribution": quarterly_distribution(rows),
        **best_worst_month(monthly),
        "top_n_winner_contribution": top_n_contribution(rows),
        "gap_open_representation": gap_open_representation(rows),
        "regime_tags": regime_tags_for_candidates(candidate_dates),
    }


def main() -> None:
    evidence_dir = REPO_ROOT / "docs" / "strategy-rules" / "evidence_322"
    group1 = json.loads((evidence_dir / "group1_corrected_baseline.json").read_text())
    extended = build_extended_report(group1)
    (evidence_dir / "group1_extended_metrics.json").write_text(
        json.dumps(extended, indent=2, default=str) + "\n"
    )
    print(f"wrote {evidence_dir / 'group1_extended_metrics.json'}")


if __name__ == "__main__":
    main()
