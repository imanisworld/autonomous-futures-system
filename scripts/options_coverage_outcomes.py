"""Episode outcome study: causal forward movement for deduped 30m coverage episodes.

    python scripts/options_coverage_outcomes.py --from 2026-09-09 --to 2026-09-15 --out logs/coverage_outcomes

READ-ONLY RESEARCH.  Reads the coverage observer sqlite, reduces it with the
#579 episode reducer, fetches regular-session 5Min bars for the sessions and
symbols that have episodes (5m is used only to resolve the forward path --
never to discover or redefine a setup), measures both entry views against both
stored target geometries, and writes:

    <out>/outcomes_<from>_<to>.json      machine-readable summary + episodes
    <out>/outcomes_<from>_<to>.csv       episode-level outcome table
    <out>/outcomes_<from>_<to>.md        short report

Identity OPTIONS_COVERAGE_OUTCOMES / out-v0.1.  Never writes to the observer
or V1 databases.  Needs Alpaca data credentials for the 5Min fetch.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from alert_ranker.bar_provider import CONSOLIDATED_FEED, AlpacaBarProvider  # noqa: E402
from alert_ranker.causal_bars import MINUTE_5  # noqa: E402
from alert_ranker.config import resolve_alpaca_credentials  # noqa: E402
from alert_ranker.coverage_episodes import Episode, reduce_events  # noqa: E402
from alert_ranker.coverage_observer import OBSERVER_VERSION  # noqa: E402
from alert_ranker.coverage_outcomes import (  # noqa: E402
    GEOMETRIES,
    OUTCOME_ID,
    OUTCOME_VERSION,
    EpisodeOutcome,
    measure_episode,
    summarize_outcomes,
)
from alert_ranker.coverage_quarantine import (  # noqa: E402
    DEFAULT_POLICY_RELPATH,
    BlindWindow,
    QuarantinePolicyError,
    load_blind_windows,
    partition_rows,
    quarantine_record,
    write_quarantine,
)
from alert_ranker.session_calendar import nyse_session_for  # noqa: E402
from ops.options_212c_floor_outcome_capture import (  # noqa: E402
    CaptureIntegrationError,
    capture_decision,
    write_artifact_once,
)
from ops.options_212c_floor_outcome_monitor import ELIGIBLE_START, FAMILY, V1_UNIVERSE  # noqa: E402
from ops.options_212c_floor_outcome_study import StudyContractError, build_session_artifact  # noqa: E402
from scripts.options_coverage_observer import fetch_all  # noqa: E402

DEFAULT_SQLITE = ROOT / "logs" / "options_coverage_observer.sqlite"
DEFAULT_OUT = ROOT / "logs" / "coverage_outcomes"
DEFAULT_BLIND_WINDOWS = ROOT / DEFAULT_POLICY_RELPATH
_SHA40 = re.compile(r"^[0-9a-f]{40}$")


def load_events(path: Path, date_from: str, date_to: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT row_json FROM coverage_events WHERE observer_version=? AND session_date BETWEEN ? AND ? ORDER BY symbol, bar_start",
            (OBSERVER_VERSION, date_from, date_to),
        ).fetchall()
    finally:
        conn.close()
    return [json.loads(r[0]) for r in rows]


def load_observer_run(path: Path, session_date: str) -> tuple[int, str, int]:
    """Return the exact latest cov-v0.1 run identity for a session."""

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT id, ran_at, events FROM coverage_runs "
            "WHERE observer_version=? AND session_date=? ORDER BY id DESC LIMIT 1",
            (OBSERVER_VERSION, session_date),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise CaptureIntegrationError("observer_run_missing", session_date)
    return int(row[0]), str(row[1]), int(row[2] or 0)


def _capture_request(args: argparse.Namespace, sqlite_path: Path, events: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    supplied = [
        args.capture_root,
        args.capture_source_sha,
        args.capture_observer_run_id,
        args.capture_observer_ran_at,
    ]
    if not any(value is not None for value in supplied):
        return None
    if any(value is None for value in supplied):
        raise CaptureIntegrationError("capture_args_incomplete")
    if args.date_from != args.date_to:
        raise CaptureIntegrationError("capture_range_refused", f"{args.date_from}->{args.date_to}")
    if not _SHA40.fullmatch(str(args.capture_source_sha)):
        raise CaptureIntegrationError("capture_source_sha_invalid", str(args.capture_source_sha))

    decision = capture_decision(
        Path(args.capture_root),
        args.date_from,
        eligible_start=ELIGIBLE_START,
    )
    if decision.reason == "already_sealed":
        return {
            "session_date": args.date_from,
            "already_sealed": True,
            "existing": decision.existing.to_public_dict() if decision.existing else None,
        }
    if not decision.required:
        raise CaptureIntegrationError(
            "capture_not_allowed", f"{args.date_from}:{decision.reason}"
        )

    run_id, ran_at, run_events = load_observer_run(sqlite_path, args.date_from)
    if run_id != int(args.capture_observer_run_id):
        raise CaptureIntegrationError(
            "observer_run_id_mismatch",
            f"expected={args.capture_observer_run_id} actual={run_id}",
        )
    if ran_at != str(args.capture_observer_ran_at):
        raise CaptureIntegrationError("observer_ran_at_mismatch")
    if run_events != len(events):
        raise CaptureIntegrationError(
            "observer_event_count_mismatch",
            f"run={run_events} stored={len(events)}",
        )
    return {
        "session_date": args.date_from,
        "root": Path(args.capture_root),
        "source_sha": str(args.capture_source_sha),
        "observer_run_id": run_id,
        "observer_ran_at": ran_at,
        "already_sealed": False,
    }


def _seal_from_fetch(
    request: dict[str, Any],
    session,
    session_episodes: Sequence[Episode],
    coverage_events: Sequence[dict[str, Any]],
    bars: dict[str, list[Any]],
    errors: dict[str, str],
    *,
    captured_at: datetime | None = None,
) -> dict[str, Any]:
    if request.get("already_sealed"):
        existing = request.get("existing")
        return existing if isinstance(existing, dict) else {"state": "valid", "session_date": session.date.isoformat()}

    selected_symbols = {
        ep.symbol
        for ep in session_episodes
        if ep.family == FAMILY and ep.symbol in V1_UNIVERSE
    }
    provider_errors = {
        symbol: errors[symbol]
        for symbol in sorted(selected_symbols)
        if symbol in errors
    }
    if provider_errors:
        raise CaptureIntegrationError(
            "capture_provider_errors",
            json.dumps(provider_errors, sort_keys=True)[:500],
        )
    selected_bars = {
        symbol: bars.get(symbol, [])
        for symbol in sorted(selected_symbols)
    }
    source = {
        "provider": f"alpaca_{CONSOLIDATED_FEED}",
        "request_start": session.open.astimezone(timezone.utc).isoformat(),
        "request_end": session.close.astimezone(timezone.utc).isoformat(),
        "observer_run_id": request["observer_run_id"],
        "observer_ran_at": request["observer_ran_at"],
        "source_sha": request["source_sha"],
    }
    artifact = build_session_artifact(
        session,
        session_episodes,
        coverage_events,
        selected_bars,
        source=source,
        captured_at=captured_at or datetime.now(timezone.utc),
    )
    check = write_artifact_once(
        request["root"],
        artifact,
        eligible_start=ELIGIBLE_START,
    )
    return check.to_public_dict()


async def measure_all(
    episodes: Sequence[Episode],
    provider: AlpacaBarProvider,
    pause_seconds: float,
    *,
    capture_request: dict[str, Any] | None = None,
    coverage_events: Sequence[dict[str, Any]] = (),
) -> tuple[list[EpisodeOutcome], dict[str, str], dict[str, Any] | None]:
    by_session: dict[str, list[Episode]] = defaultdict(list)
    for ep in episodes:
        by_session[ep.session_date].append(ep)
    outcomes: list[EpisodeOutcome] = []
    errors: dict[str, str] = {}
    capture_result: dict[str, Any] | None = None
    for session_date in sorted(by_session):
        session = nyse_session_for(date.fromisoformat(session_date))
        if session is None:
            continue
        symbols = sorted({ep.symbol for ep in by_session[session_date]})
        bars, errs = await fetch_all(provider, symbols, MINUTE_5, session.open, session.close)
        for symbol, reason in errs.items():
            errors[f"{session_date}:{symbol}"] = reason

        if capture_request is not None and capture_request["session_date"] == session_date:
            local_errors = {
                key.split(":", 1)[1]: value
                for key, value in errors.items()
                if key.startswith(f"{session_date}:")
            }
            capture_result = _seal_from_fetch(
                capture_request,
                session,
                by_session[session_date],
                coverage_events,
                bars,
                local_errors,
            )

        for ep in by_session[session_date]:
            outcomes.append(
                measure_episode(
                    ep,
                    session.open,
                    session.close,
                    bars.get(ep.symbol, []),
                )
            )
        await asyncio.sleep(pause_seconds)
    return outcomes, errors, capture_result


CSV_COLUMNS = [
    "symbol", "session_date", "family", "direction", "first_bar_start", "n_events", "v1_supported",
    "entry_trigger", "invalidation", "structural_risk", "first_sight_at", "first_sight_after_close", "first_sight_price",
    "blind_window_extension_r", "alignment_ok", "alignment_failures", "gate_bucket_nearest", "gate_bucket_floor",
    "clean", "rr_quality_flags", "quality_flags",
]
VIEW_COLUMNS = [
    "entry_at", "target_1", "target_1_r", "target_2", "target_2_r", "outcome", "first_decisive_event", "mfe_r", "mae_r", "close_r",
    "max_favorable_r_before_invalidation", "max_adverse_r_before_target", "hit_0_5r_at", "hit_1_0r_at", "hit_1_5r_at", "hit_2_0r_at",
    "target_1_hit_at", "target_2_hit_at", "invalidation_hit_at", "minutes_to_1r", "minutes_to_target_1", "minutes_to_invalidation", "flags",
]


def write_csv(path: Path, outcomes: Sequence[EpisodeOutcome]) -> None:
    header = list(CSV_COLUMNS)
    for view in ("mechanical", "first_sight"):
        for geometry in GEOMETRIES:
            header.extend(f"{view}:{geometry}:{c}" for c in VIEW_COLUMNS)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for o in outcomes:
            row = o.to_row()
            line: list[Any] = []
            for c in CSV_COLUMNS:
                value = row[c]
                line.append(",".join(value) if isinstance(value, list) else value)
            for view in ("mechanical", "first_sight"):
                for geometry in GEOMETRIES:
                    pv = o.view(view, geometry)
                    for c in VIEW_COLUMNS:
                        value = getattr(pv, c) if pv else None
                        line.append(",".join(value) if isinstance(value, list) else value)
            writer.writerow(line)


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _family_table(summary: dict[str, Any], population: str) -> list[str]:
    lines = [
        f"| family | ep | clean | flagged | view:geom | scored | TARGET_FIRST | INVAL_FIRST | UNRES | AMBIG | ≥1R | ≥2R | ≥1R % | tgt-first % | med MFE R | med MAE R |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for family, block in sorted(summary["by_family"].items(), key=lambda kv: -kv[1]["episodes"]):
        first = True
        for view in ("mechanical", "first_sight"):
            for geometry in GEOMETRIES:
                s = block[population][f"{view}:{geometry}"]
                lead = f"| {family} | {block['episodes']} | {block['clean_episodes']} | {block['quality_flagged_episodes']} |" if first else "|  |  |  |  |"
                first = False
                lines.append(
                    f"{lead} {view}:{geometry} | {s['scored']} | {s['TARGET_FIRST']} | {s['INVALIDATION_FIRST']} | {s['UNRESOLVED_AT_CLOSE']} | {s['AMBIGUOUS']} | {s['ge_1r']} | {s['ge_2r']} | {_fmt(s['ge_1r_rate_pct'])} | {_fmt(s['target_first_rate_pct'])} | {_fmt(s['mfe_r']['median'])} | {_fmt(s['mae_r']['median'])} |"
                )
    return lines


def _timing_table(summary: dict[str, Any], population: str) -> list[str]:
    lines = [
        "| family | ep | blind-window R p25 / median / p75 | % lose ≥0.25R pre-sight | % gain ≥0.25R pre-sight | mech ≥1R eps | % still ≥1R at sight | mech ≥1R % → sight ≥1R % (nearest) | mech tgt-first % → sight tgt-first % (nearest) | same (floor) |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for family, block in sorted(summary["by_family"].items(), key=lambda kv: -kv[1]["episodes"]):
        t = block[population]["timing"]
        tl = block[population]["timing_loss"]
        q = t["blind_window_extension_r"]
        lines.append(
            f"| {family} | {block['episodes']} | {_fmt(q['p25'])} / {_fmt(q['median'])} / {_fmt(q['p75'])} | {_fmt(t['pct_losing_ge_0_25r_before_first_sight'])} | {_fmt(t['pct_gaining_ge_0_25r_before_first_sight'])} | {t['mechanical_ge1r_episodes']} | {_fmt(t['pct_mechanical_ge1r_still_ge1r_at_first_sight'])} | {_fmt(tl['nearest']['mechanical_ge1r_rate_pct'])} → {_fmt(tl['nearest']['first_sight_ge1r_rate_pct'])} | {_fmt(tl['nearest']['mechanical_target_first_rate_pct'])} → {_fmt(tl['nearest']['first_sight_target_first_rate_pct'])} | {_fmt(tl['floor']['mechanical_target_first_rate_pct'])} → {_fmt(tl['floor']['first_sight_target_first_rate_pct'])} |"
        )
    return lines


def _group_table(summary: dict[str, Any], key: str, population: str, label: str) -> list[str]:
    lines = [
        f"| {label} | ep | clean | mech ≥1R % (near) | sight ≥1R % (near) | mech tgt-first % (near) | sight tgt-first % (near) | mech tgt-first % (floor) | sight tgt-first % (floor) | med MFE R mech | med MFE R sight |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name, block in summary[key].items():
        p = block[population]
        lines.append(
            f"| {name} | {block['episodes']} | {block['clean_episodes']} | {_fmt(p['mechanical:nearest']['ge_1r_rate_pct'])} | {_fmt(p['first_sight:nearest']['ge_1r_rate_pct'])} | {_fmt(p['mechanical:nearest']['target_first_rate_pct'])} | {_fmt(p['first_sight:nearest']['target_first_rate_pct'])} | {_fmt(p['mechanical:floor']['target_first_rate_pct'])} | {_fmt(p['first_sight:floor']['target_first_rate_pct'])} | {_fmt(p['mechanical:nearest']['mfe_r']['median'])} | {_fmt(p['first_sight:nearest']['mfe_r']['median'])} |"
        )
    return lines


def quarantine_lines(summary: dict[str, Any]) -> list[str]:
    """Human-readable proof that a blind window was applied. No row content."""
    blocks = summary.get("quarantine") or []
    if not blocks:
        return []
    lines = ["## Blind-window quarantine (presentation only)", ""]
    for block in blocks:
        lines.append(
            f"- window `{block['window_id']}` ({', '.join(block['trial_ids'])}): {block['quarantined_rows']} structural "
            f"{block['family']} rows on {block['symbols_count']} symbols withheld from this product; sealed at "
            f"`{block['path']}` ({block['byte_length']} bytes, sha256 `{block['sha256']}`). Raw observer events are unchanged."
        )
    lines.append("")
    return lines


def write_markdown(path: Path, summary: dict[str, Any], date_from: str, date_to: str, errors: dict[str, str]) -> None:
    total = summary["total"]
    lines = [
        f"# {OUTCOME_ID} {OUTCOME_VERSION} — {date_from} → {date_to}",
        "",
        "READ-ONLY research over the coverage observer (cov-v0.1) + episode reducer (ep-v0.1). Same-session horizon; 5m bars used only to resolve the forward path; no intrabar ordering assumed; R normalised so favourable is positive; structural risk is the R denominator for both views.",
        "",
        f"Episodes {total['episodes']} · clean {total['clean_episodes']} · flagged {total['quality_flagged_episodes']} · quality flags {json.dumps(summary['quality_flag_counts'], sort_keys=True)}",
        f"Provider errors during 5m fetch: {len(errors)}" + (f" — {json.dumps(errors, sort_keys=True)[:400]}" if errors else ""),
        "",
        *quarantine_lines(summary),
        "## By family — CLEAN episodes",
        *_family_table(summary, "clean"),
        "",
        "## By family — ALL episodes (flagged included)",
        *_family_table(summary, "all"),
        "",
        "## Timing loss (blind window) — CLEAN episodes",
        *_timing_table(summary, "clean"),
        "",
        "## By session (clean)",
        *_group_table(summary, "by_session", "clean", "session"),
        "",
        "## By direction (clean)",
        *_group_table(summary, "by_direction", "clean", "direction"),
        "",
        "## By market alignment (clean)",
        *_group_table(summary, "by_alignment", "clean", "alignment"),
        "",
        "## By gate-stage bucket, nearest geometry (clean)",
        *_group_table(summary, "by_gate_bucket_nearest", "clean", "bucket"),
        "",
        "## By gate-stage bucket, floor geometry (clean)",
        *_group_table(summary, "by_gate_bucket_floor", "clean", "bucket"),
        "",
        "## By symbol (clean) — top 25 by episodes",
    ]
    top = dict(sorted(summary["by_symbol"].items(), key=lambda kv: -kv[1]["episodes"])[:25])
    lines.extend(_group_table({"by_symbol": top}, "by_symbol", "clean", "symbol"))
    path.write_text("\n".join(lines) + "\n")


async def run(args: argparse.Namespace) -> int:
    policy_path = Path(args.blind_windows or os.environ.get("OPTIONS_COVERAGE_BLIND_WINDOWS") or DEFAULT_BLIND_WINDOWS)
    try:
        windows = load_policy(policy_path)
    except QuarantinePolicyError as exc:
        print(f"blind-window policy refused: {exc}", file=sys.stderr)
        return 2
    sqlite_path = Path(args.sqlite or os.environ.get("OPTIONS_COVERAGE_SQLITE_PATH") or DEFAULT_SQLITE)
    events = load_events(sqlite_path, args.date_from, args.date_to)
    episodes = reduce_events(events)
    if not episodes:
        print("no episodes in range", file=sys.stderr)
        return 2

    try:
        capture_request = _capture_request(args, sqlite_path, events)
    except CaptureIntegrationError as exc:
        print(f"capture refused: {exc}", file=sys.stderr)
        return 2

    api_key, secret_key = resolve_alpaca_credentials()
    if not api_key or not secret_key:
        print("Alpaca credentials not configured", file=sys.stderr)
        return 2
    provider = AlpacaBarProvider(
        base_url=os.environ.get("ALPACA_DATA_BASE_URL", "https://data.alpaca.markets").rstrip("/"),
        api_key=api_key,
        secret_key=secret_key,
        feed=CONSOLIDATED_FEED,
    )
    try:
        outcomes, errors, capture_result = await measure_all(
            episodes,
            provider,
            args.pause_seconds,
            capture_request=capture_request,
            coverage_events=events,
        )
    except (CaptureIntegrationError, StudyContractError) as exc:
        print(f"capture refused: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out or DEFAULT_OUT)
    summary, public = write_products(
        out, args.date_from, args.date_to, outcomes, errors, windows,
        raw_events=len(events), episodes=len(episodes),
    )
    stem = out / f"outcomes_{args.date_from}_{args.date_to}"
    held = sum(block["quarantined_rows"] for block in summary.get("quarantine") or [])
    print(
        f"{OUTCOME_ID} {OUTCOME_VERSION}: {len(events)} events → {len(episodes)} episodes → {len(outcomes)} outcomes "
        f"({len(public)} public, {held} quarantined); clean {summary['total']['clean_episodes']}; errors {len(errors)}"
    )
    print(f"wrote {stem}.json / .csv / .md")
    if capture_result is not None:
        print(
            "path-v0.2 seal: "
            f"{capture_result.get('session_date')} "
            f"{capture_result.get('state')} "
            f"sha256={capture_result.get('sha256')}"
        )
    return 0


def write_products(
    out: Path,
    date_from: str,
    date_to: str,
    outcomes: Sequence[EpisodeOutcome],
    errors: dict[str, str],
    windows: Sequence[BlindWindow],
    *,
    raw_events: int,
    episodes: int,
) -> tuple[dict[str, Any], list[EpisodeOutcome]]:
    """Write the public ``outcomes_<from>_<to>.{json,csv,md}`` and any quarantine files.

    Rows inside an active blind window are withheld from every public product
    and written canonically under ``<out>/quarantine/<window_id>/``; the public
    summary carries only the binding block. Pure presentation isolation: the
    measured outcomes themselves are unchanged. Returns ``(summary, public)``.
    """
    rows = [o.to_row() for o in outcomes]
    public_rows, held = partition_rows(rows, windows)
    held_ids = {id(r) for rows_ in held.values() for r in rows_}
    public = [o for o, row in zip(outcomes, rows) if id(row) not in held_ids]

    summary = summarize_outcomes(public)
    summary["date_from"], summary["date_to"] = date_from, date_to
    summary["raw_events"], summary["episodes"] = raw_events, episodes
    summary["generated_at"] = datetime.now(timezone.utc).isoformat()
    summary["provider_errors"] = errors

    out.mkdir(parents=True, exist_ok=True)
    blocks: list[dict[str, Any]] = []
    by_id = {w.window_id: w for w in windows}
    # Every window active for the range gets a file, even an empty one, so the
    # product proves the quarantine ran for that session.
    active = [w for w in windows if any(w.active_on(d) for d in (date_from, date_to))]
    for window in sorted(active, key=lambda w: w.window_id):
        record = quarantine_record(
            window, "outcomes", date_from, date_to, held.get(window.window_id, []),
            source={"outcome_id": OUTCOME_ID, "outcome_version": OUTCOME_VERSION, "raw_events": raw_events, "episodes": episodes},
        )
        blocks.append(write_quarantine(out, record, window=by_id[window.window_id]))
    if blocks:
        summary["quarantine"] = blocks

    stem = out / f"outcomes_{date_from}_{date_to}"
    stem.with_suffix(".json").write_text(json.dumps({"summary": summary, "episodes": public_rows}, indent=1, sort_keys=True))
    write_csv(stem.with_suffix(".csv"), public)
    write_markdown(stem.with_suffix(".md"), summary, date_from, date_to, errors)
    return summary, public


def load_policy(path: Path) -> Sequence[BlindWindow]:
    """Fail closed: a missing or malformed blind-window policy stops the run."""
    return load_blind_windows(path)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only coverage episode outcome study")
    parser.add_argument("--from", dest="date_from", required=True)
    parser.add_argument("--to", dest="date_to", required=True)
    parser.add_argument("--sqlite")
    parser.add_argument("--out")
    parser.add_argument("--pause-seconds", type=float, default=3.0, help="pause between sessions to respect the provider rate limit")
    parser.add_argument("--blind-windows", help=f"Blind-window quarantine policy JSON (default: {DEFAULT_POLICY_RELPATH} or OPTIONS_COVERAGE_BLIND_WINDOWS)")
    # Internal collector-only path-v0.2 capture wiring. These are deliberately
    # hidden from ordinary manual usage; the capture module independently
    # refuses while ELIGIBLE_START is unset or the session is out of sequence.
    parser.add_argument("--capture-root", help=argparse.SUPPRESS)
    parser.add_argument("--capture-source-sha", help=argparse.SUPPRESS)
    parser.add_argument("--capture-observer-run-id", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--capture-observer-ran-at", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
