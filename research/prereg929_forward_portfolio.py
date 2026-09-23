"""Prereg #929 forward evaluator: MNQ five-family (ex-Asia) shared-account portfolio.

RESEARCH / AUDIT ONLY. No execution authority. See
``docs/prereg-mnq-portfolio-ex-asia-forward-2026-09-23.md`` (the prereg wins over
anything written here) and ``docs/prereg929-evaluator.md``.

Design (why the frozen #915 code is not edited):

* ``research/mnq_combined_portfolio_audit.py`` and
  ``scripts/mnq_combined_portfolio_audit.py`` are carried byte-for-byte from
  5a9f14b (``tests/test_prereg929_forward_portfolio.py`` pins their git blob
  hashes). Portfolio mechanics, fill models, costs and family adapters are the
  frozen ones.
* The three permitted changes are applied from the outside:
    - corpus directory + date range: the adapters already take corpus roots;
      the window is applied by temporarily setting the frozen module's
      ``START``/``END`` (which is all ``_date_in_window`` reads), and day-range
      views of a corpus are symlink directories;
    - drop ``ASIA_D_EMA`` for H1: ``replay_portfolio(..., excluded_families=
      {"ASIA_D_EMA"})`` (the frozen API);
    - tie order: the frozen ``TIE_PRIORITY`` with Asia removed is exactly the
      prereg §4 order, so no change is needed (tested).
* The #915 full-window control assertions are properties of the frozen
  historical corpora, not of the adapters. On any other corpus
  (``enforce_controls=False``) they are replaced by a no-op and their
  diagnostics are discarded unread; everything else in the adapters runs as
  frozen, including every fail-closed guard. stdout is swallowed while adapters
  run so no control P&L can leak to a terminal.
* 12HR Miyagi: the #915 adapter reads a frozen candidate file. For a corpus
  other than the frozen one, candidates come from the unchanged detector
  (``research.run_12hr_miyagi_evidence.detect_candidates``) pointed at the
  corpus, written to a temporary file with the frozen name, and the frozen
  adapter reads it. Step 0 proves the detector reproduces the frozen file.

Blind by default: ``counts_report`` builds its output from an allowlist and
``assert_blind`` rejects any P&L-shaped key. ``look_report`` refuses to run
below the prereg minimum sample (unless the deadline has passed) and without a
passing step-0 report.
"""
from __future__ import annotations

import contextlib
import io
import json
import math
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional
from zoneinfo import ZoneInfo

from research.mnq_combined_portfolio_audit import (
    TIE_PRIORITY,
    PortfolioEvent,
    parse_ts,
    replay_portfolio,
    summarize_fills,
)

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

PREREG_DOC = "docs/prereg-mnq-portfolio-ex-asia-forward-2026-09-23.md"
PREREG_ID = "929"
SCORING_START = datetime(2026, 9, 23, 22, 0, tzinfo=UTC)
DEADLINE = date(2027, 9, 30)
MIN_TERMINAL_FILLS = 40
MIN_CME_DAYS = 120
PF_HURDLE = 1.94
MAX_DRAWDOWN = 1750.0
TOP3_DAY_SHARE_MAX = 0.60

FAMILY_4HR = "4HR_RETRIGGER"
FAMILY_322 = "60M_322_FIRST_LIVE"
FAMILY_DAILY = "DAILY_22_COMPLETED_CLOSE"
FAMILY_MIYAGI = "12HR_MIYAGI"
FAMILY_ASIA = "ASIA_D_EMA"
FAMILY_ST = "SUSTAINED_TREND_V1"
ALL_FAMILIES = tuple(TIE_PRIORITY)
H1_FAMILIES = tuple(f for f in TIE_PRIORITY if f != FAMILY_ASIA)
PREREG_H1_TIE_ORDER = (
    FAMILY_4HR,
    FAMILY_322,
    FAMILY_DAILY,
    FAMILY_MIYAGI,
    FAMILY_ST,
)
# Same terminal set the frozen ``summarize_fills`` uses.
TERMINAL_RESULTS = frozenset({"WIN", "LOSS", "BREAKEVEN"})

MIYAGI_FROZEN_REL = (
    "docs/strategy-rules/evidence_12hr_miyagi/mnq_results_trigger_bar_corrected_2026-09-19.json"
)


# ─── corpus roots ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CorpusRoots:
    """Every corpus root the six frozen adapters read (each contains ``MNQ/``).

    Field -> #915 source:
      root5          replay_corpus_v1_5m_4hr_audit (4HR, 3-2-2 fills, Daily)
      root15         replay_corpus_v1_market_condition_fixed (Asia, Sustained 15m)
      root5late      replay_corpus_v1_5m (Sustained 5m)
      root322_15     replay_polygon (3-2-2 detector, 15m)
      root_miyagi5   replay_polygon_5m (Miyagi fill replay)
      miyagi_15 / miyagi_5: Miyagi detector inputs (only used when
      ``miyagi_candidates == "detect"``; the frozen file was built from
      replay_polygon / replay_polygon_5m)
    """

    root5: Path
    root15: Path
    root5late: Path
    root322_15: Path
    root_miyagi5: Path
    miyagi_15: Path
    miyagi_5: Path
    miyagi_candidates: str = "detect"  # "detect" | "frozen"

    def __post_init__(self) -> None:
        if self.miyagi_candidates not in {"detect", "frozen"}:
            raise ValueError(f"bad miyagi_candidates {self.miyagi_candidates!r}")


def pr915_roots(data_root: Path) -> CorpusRoots:
    data_root = Path(data_root)
    return CorpusRoots(
        root5=data_root / "replay_corpus_v1_5m_4hr_audit",
        root15=data_root / "replay_corpus_v1_market_condition_fixed",
        root5late=data_root / "replay_corpus_v1_5m",
        root322_15=data_root / "replay_polygon",
        root_miyagi5=data_root / "replay_polygon_5m",
        miyagi_15=data_root / "replay_polygon",
        miyagi_5=data_root / "replay_polygon_5m",
        miyagi_candidates="frozen",
    )


def single_pair_roots(root_5m: Path, root_15m: Path, *, miyagi_candidates: str = "detect") -> CorpusRoots:
    """All 5m roles read one 5m corpus, all 15m roles one 15m corpus."""
    return CorpusRoots(
        root5=Path(root_5m),
        root15=Path(root_15m),
        root5late=Path(root_5m),
        root322_15=Path(root_15m),
        root_miyagi5=Path(root_5m),
        miyagi_15=Path(root_15m),
        miyagi_5=Path(root_5m),
        miyagi_candidates=miyagi_candidates,
    )


def corpus_days(root: Path, instrument: str = "MNQ") -> list[str]:
    return sorted(p.stem.removeprefix(f"{instrument}_") for p in (Path(root) / instrument).glob(f"{instrument}_*.jsonl"))


def corpus_view(root: Path, start: date, end: date, dest: Path, instrument: str = "MNQ") -> Path:
    """Symlink the day files of ``root`` within [start, end] into ``dest/MNQ``."""
    leaf = Path(dest) / instrument
    leaf.mkdir(parents=True, exist_ok=False)
    n = 0
    for path in sorted((Path(root) / instrument).glob(f"{instrument}_*.jsonl")):
        day = path.stem.removeprefix(f"{instrument}_")
        if start.isoformat() <= day <= end.isoformat():
            (leaf / path.name).symlink_to(path.resolve())
            n += 1
    if n == 0:
        raise RuntimeError(f"empty corpus view {root} {start}..{end}")
    return Path(dest)


def view_roots(roots: CorpusRoots, start: date, end: date, tmp: Path) -> CorpusRoots:
    """Restrict every distinct root to [start, end] (shared views for equal roots)."""
    made: dict[Path, Path] = {}

    def v(p: Path) -> Path:
        key = Path(p).resolve()
        if key not in made:
            made[key] = corpus_view(p, start, end, Path(tmp) / f"view{len(made)}")
        return made[key]

    return replace(
        roots,
        root5=v(roots.root5),
        root15=v(roots.root15),
        root5late=v(roots.root5late),
        root322_15=v(roots.root322_15),
        root_miyagi5=v(roots.root_miyagi5),
        miyagi_15=v(roots.miyagi_15),
        miyagi_5=v(roots.miyagi_5),
    )


# ─── adapter harness ─────────────────────────────────────────────────────────

@dataclass
class FamilyOutput:
    family: str
    status: str  # "OK" | "FAILED_CLOSED"
    events: list[PortfolioEvent] = field(default_factory=list)
    attempts: list[dict] = field(default_factory=list)
    error: Optional[str] = None
    diag: Optional[dict] = None  # only kept when controls are enforced


@dataclass
class AdapterRun:
    window: tuple[str, str]
    enforce_controls: bool
    families: dict[str, FamilyOutput]
    miyagi_candidates: Optional[list[dict]] = None

    @property
    def healthy(self) -> bool:
        return all(f.status == "OK" for f in self.families.values())

    def events(self, families: Iterable[str] | None = None) -> list[PortfolioEvent]:
        keep = set(families) if families is not None else set(self.families)
        return [e for f in self.families.values() if f.family in keep for e in f.events]


@contextlib.contextmanager
def _patched(obj, **attrs):
    missing = [k for k in attrs if not hasattr(obj, k)]
    if missing:
        raise AttributeError(f"{obj!r} lacks {missing}")
    saved = {k: getattr(obj, k) for k in attrs}
    try:
        for k, v in attrs.items():
            setattr(obj, k, v)
        yield obj
    finally:
        for k, v in saved.items():
            setattr(obj, k, v)


def _noop_control(*_args, **_kwargs) -> None:
    return None


_CONTROL_NUMERIC_KEYS = frozenset({"pf", "profit_factor", "net", "net_pnl"})


def _none_safe(fn: Callable) -> Callable:
    """Wrap a CONTROL-ONLY summary so an empty window does not crash ``float(None)``.

    The frozen adapters coerce their full-window control summaries with
    ``float(...)`` before the (here disabled) control assertion. On a short or
    trade-less corpus the frozen summaries return ``None`` for PF/net, which
    would abort the adapter before its event stream is built. The wrapped
    functions are used only for those discarded controls (never for events).
    """

    def fix(obj):
        if isinstance(obj, dict):
            return {
                k: (0.0 if (v is None and k in _CONTROL_NUMERIC_KEYS) else fix(v))
                for k, v in obj.items()
            }
        return obj

    def wrapped(*args, **kwargs):
        return fix(fn(*args, **kwargs))

    wrapped.__wrapped__ = fn
    return wrapped


def _day_extent(*roots: Path) -> tuple[date, date]:
    firsts, lasts = [], []
    for root in roots:
        days = corpus_days(root)
        if not days:
            raise RuntimeError(f"no MNQ day files under {root}")
        firsts.append(days[0])
        lasts.append(days[-1])
    return date.fromisoformat(min(firsts)), date.fromisoformat(max(lasts))


def detect_miyagi_candidates(cache_15m: Path, cache_5m: Path, start: date, end: date) -> list[dict]:
    """Run the unchanged Miyagi detector against the given corpus roots."""
    from research import run_12hr_miyagi_evidence as ev

    with _patched(ev, CACHE_15M=Path(cache_15m), CACHE_5M=Path(cache_5m)):
        out = ev.detect_candidates("MNQ", start, end)
    return [{**c, "date": c["date"].isoformat()} for c in out["candidates"]]


def _error_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:600]


def run_family_adapters(
    roots: CorpusRoots,
    window_start: date,
    window_end: date,
    *,
    enforce_controls: bool = False,
    families: Iterable[str] = ALL_FAMILIES,
    quiet: bool = True,
) -> AdapterRun:
    """Run the frozen #915 adapters over ``roots`` for events dated in the window.

    Each family is isolated: an exception marks that family FAILED_CLOSED with
    its message; it never falls back to anything.
    """
    import importlib

    from scripts import mnq_combined_portfolio_audit as pa

    audit322 = importlib.import_module("scripts.322_trigger_timing_ab_2026_09_18")
    wanted = [f for f in ALL_FAMILIES if f in set(families)]
    outputs: dict[str, FamilyOutput] = {}
    miyagi_cands: Optional[list[dict]] = None

    sink = io.StringIO()
    redirect = contextlib.redirect_stdout(sink) if quiet else contextlib.nullcontext()
    base_patch = {"START": window_start, "END": window_end}
    control_patches = contextlib.ExitStack()
    if not enforce_controls:
        # Control-only plumbing (see _none_safe): these functions feed only the
        # #915 full-window controls inside the adapters, never the events.
        base_patch["_assert_control"] = _noop_control
        base_patch["bracket_summary"] = _none_safe(pa.bracket_summary)
        base_patch["summarize_replay"] = _none_safe(pa.summarize_replay)
        control_patches.enter_context(_patched(pa.daily_audit, summary=_none_safe(pa.daily_audit.summary)))
        control_patches.enter_context(_patched(pa.sustained_script, run=_none_safe(pa.sustained_script.run)))

    with control_patches, redirect, _patched(pa, **base_patch), tempfile.TemporaryDirectory(prefix="prereg929-") as tmp:
        tmp_path = Path(tmp)

        def call(family: str, fn: Callable[[], tuple]) -> None:
            try:
                events, diag, attempts = fn()
                outputs[family] = FamilyOutput(
                    family=family,
                    status="OK",
                    events=list(events),
                    attempts=list(attempts),
                    diag=diag if enforce_controls else None,
                )
            except Exception as exc:  # fail closed per family, never substitute
                outputs[family] = FamilyOutput(family=family, status="FAILED_CLOSED", error=_error_text(exc))

        for family in wanted:
            if family == FAMILY_4HR:
                call(family, lambda: pa._four_hr(roots.root5))
            elif family == FAMILY_322:
                if enforce_controls:
                    call(family, lambda: pa._three_two_two(roots.root322_15, roots.root5))
                else:
                    def run322():
                        # The frozen crosscheck compares the 15m research detector
                        # over [START, END] against the 5m state machine over the
                        # whole 5m corpus, and requires a fixed count. Off the
                        # frozen corpus both are set to the corpus extent / the
                        # detector's own count; every other guard stays live.
                        ext = _day_extent(roots.root5, roots.root322_15)
                        with _patched(audit322, START=ext[0], END=ext[1]):
                            n = len(audit322.detect_candidates(roots.root322_15, "MNQ", ext[0], ext[1]))
                            with _patched(audit322, EXPECTED_N=n):
                                return pa._three_two_two(roots.root322_15, roots.root5)
                    call(family, run322)
            elif family == FAMILY_DAILY:
                call(family, lambda: pa._daily(roots.root5))
            elif family == FAMILY_MIYAGI:
                if roots.miyagi_candidates == "frozen":
                    call(family, lambda: pa._miyagi(roots.root_miyagi5))
                else:
                    def run_miyagi():
                        nonlocal miyagi_cands
                        ext = _day_extent(roots.miyagi_15, roots.miyagi_5)
                        miyagi_cands = detect_miyagi_candidates(roots.miyagi_15, roots.miyagi_5, ext[0], ext[1])
                        fake_repo = tmp_path / "miyagi_repo"
                        target = fake_repo / MIYAGI_FROZEN_REL
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(json.dumps({"candidates": miyagi_cands}, default=str))
                        with _patched(pa, REPO=fake_repo):
                            return pa._miyagi(roots.root_miyagi5)
                    call(family, run_miyagi)
            elif family == FAMILY_ASIA:
                call(family, lambda: pa._asia(roots.root15))
            elif family == FAMILY_ST:
                call(family, lambda: pa._sustained(roots.root15, roots.root5late))

    return AdapterRun(
        window=(window_start.isoformat(), window_end.isoformat()),
        enforce_controls=enforce_controls,
        families=outputs,
        miyagi_candidates=miyagi_cands,
    )


# ─── scoring filter + counts (blind) ─────────────────────────────────────────

def scored_events(events: Iterable[PortfolioEvent], start: datetime = SCORING_START) -> list[PortfolioEvent]:
    """Prereg §3: causal signal AND fill both at/after the scoring start."""
    return [
        e for e in events
        if parse_ts(e.signal_ts) >= start and parse_ts(e.eligible_fill_ts) >= start
    ]


def _obs_day(ts: datetime) -> date:
    et = ts.astimezone(ET)
    return et.date() + timedelta(days=1) if et.time() >= time(18, 0) else et.date()


def _obs_day_end(day: date) -> datetime:
    return datetime.combine(day, time(18, 0), tzinfo=ET).astimezone(UTC)


def observed_cme_days(root_5m: Path, as_of: datetime, start: datetime = SCORING_START) -> dict:
    """CME observation days (18:00 ET roll) with scored 5m bars, completed by ``as_of``."""
    days: set[date] = set()
    for path in sorted((Path(root_5m) / "MNQ").glob("MNQ_*.jsonl")):
        if path.stem.removeprefix("MNQ_") < (start - timedelta(days=1)).date().isoformat():
            continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            ts = parse_ts(json.loads(line)["timestamp"])
            if start <= ts and ts + timedelta(minutes=5) <= as_of:
                days.add(_obs_day(ts))
    completed = sorted(d for d in days if _obs_day_end(d) <= as_of)
    first = _obs_day(start)
    calendar = 0
    d = first
    while _obs_day_end(d) <= as_of:
        if d.weekday() < 5:
            calendar += 1
        d += timedelta(days=1)
    return {
        "observed_completed_days": len(completed),
        "first_observed_day": completed[0].isoformat() if completed else None,
        "last_observed_day": completed[-1].isoformat() if completed else None,
        "calendar_weekday_trade_dates_elapsed": calendar,
        "weekday_trade_dates_without_data": max(0, calendar - sum(1 for x in completed if x.weekday() < 5)),
    }


# Any key containing one of these fragments is P&L-shaped and forbidden in
# counts output (checked recursively on keys AND string values' keys).
FORBIDDEN_KEY_FRAGMENTS = (
    "net", "pnl", "pf", "profit", "drawdown", "dd", "win", "loss", "breakeven",
    "expectancy", "equity", "result", "exit", "entry", "stop", "target", "balance",
    "return", "sharpe",
)


def assert_blind(obj, path: str = "") -> None:
    """Raise if any dict key looks like P&L / outcome data."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            low = str(k).lower()
            for frag in FORBIDDEN_KEY_FRAGMENTS:
                if frag in low:
                    raise AssertionError(f"blind violation: key {path}/{k} contains {frag!r}")
            assert_blind(v, f"{path}/{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_blind(v, f"{path}[{i}]")
    elif isinstance(obj, float) and not math.isfinite(obj):
        raise AssertionError(f"blind violation: non-finite number at {path}")


def _occupancy_counts(replay) -> dict:
    fam = Counter(e.family for e in replay.fills)
    terminal = Counter(e.family for e in replay.fills if e.result in TERMINAL_RESULTS)
    busy = Counter(d.family for d in replay.decisions if d.disposition == "SKIPPED_BUSY_PORTFOLIO")
    busy_by_blocker = Counter(
        d.blocker_family for d in replay.decisions if d.disposition == "SKIPPED_BUSY_PORTFOLIO"
    )
    maxed = Counter(d.family for d in replay.decisions if d.disposition == "SKIPPED_MAX_TRADES_PORTFOLIO")
    return {
        "portfolio_fills": len(replay.fills),
        "terminal_portfolio_fills": sum(terminal.values()),
        "open_or_nonterminal_fills": len(replay.fills) - sum(terminal.values()),
        "fills_by_family": dict(sorted(fam.items())),
        "terminal_fills_by_family": dict(sorted(terminal.items())),
        "busy_skips_by_family": dict(sorted(busy.items())),
        "busy_skips_by_blocking_family": dict(sorted(busy_by_blocker.items())),
        "max_trades_skips_by_family": dict(sorted(maxed.items())),
        "exact_timestamp_collisions": replay.exact_timestamp_collisions,
    }


def sample_status(terminal: int, cme_days: int, today: date) -> dict:
    met = terminal >= MIN_TERMINAL_FILLS and cme_days >= MIN_CME_DAYS
    deadline = today >= DEADLINE
    return {
        "minimum_sample_met": met,
        "deadline_reached": deadline,
        "look_allowed": met or deadline,
        "min_terminal_fills": MIN_TERMINAL_FILLS,
        "min_cme_days": MIN_CME_DAYS,
        "deadline": DEADLINE.isoformat(),
    }


def counts_report(run: AdapterRun, cme_days: dict, *, as_of: datetime) -> dict:
    """Operational counts only (prereg §5). Built from an allowlist; checked blind."""
    pipeline = {
        "healthy": run.healthy,
        "family_status": {f: o.status for f, o in sorted(run.families.items())},
        "family_errors": {f: o.error for f, o in sorted(run.families.items()) if o.error},
        "adapter_date_range": list(run.window),
    }
    out: dict = {
        "prereg": PREREG_ID,
        "mode": "counts",
        "as_of": as_of.isoformat(),
        "scoring_start": SCORING_START.isoformat(),
        "pipeline_health": pipeline,
        "cme_observation_days": cme_days,
    }
    if not run.healthy:
        out["h1_counts"] = None
        out["note"] = "A family adapter failed closed; shared-account counts are not computed."
    else:
        scored = scored_events(run.events())
        pre_start = len(run.events()) - len(scored)
        h1 = replay_portfolio(scored, excluded_families={FAMILY_ASIA})
        six = replay_portfolio(scored)
        raw = Counter(e.family for e in scored)
        attempts = Counter(
            a["family"] for o in run.families.values() for a in o.attempts
            if parse_ts(a["ts"]) >= SCORING_START
        )
        out["h1_counts"] = _occupancy_counts(h1)
        out["h2_six_family_counts"] = _occupancy_counts(six)
        out["scored_fillable_events_by_family"] = dict(sorted(raw.items()))
        out["scored_signal_attempts_by_family"] = dict(sorted(attempts.items()))
        out["fillable_events_before_scoring_start_excluded"] = pre_start
        out["sample"] = sample_status(
            out["h1_counts"]["terminal_portfolio_fills"],
            cme_days["observed_completed_days"],
            as_of.date(),
        )
    assert_blind(out)
    return out


# ─── look (single, gated) ────────────────────────────────────────────────────

class LookRefused(RuntimeError):
    pass


def validate_step0_report(path: Path | None) -> dict:
    if path is None:
        raise LookRefused("look requires a passing step-0 report (--step0-report)")
    p = Path(path)
    if not p.is_file():
        raise LookRefused(f"step-0 report not found: {p}")
    try:
        rep = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise LookRefused(f"step-0 report unreadable: {exc}") from exc
    if str(rep.get("prereg")) != PREREG_ID or rep.get("kind") != "step0_parity":
        raise LookRefused("step-0 report is not a prereg #929 step-0 parity report")
    checks = rep.get("checks") or {}
    missing = [c for c in ("0a", "0b", "0c", "0d") if c not in checks]
    if missing:
        raise LookRefused(f"step-0 report missing checks {missing}")
    failed = [c for c in ("0a", "0b", "0c", "0d") if (checks[c] or {}).get("verdict") != "PASS"]
    if rep.get("overall") != "PASS" or failed:
        raise LookRefused(f"step-0 did not PASS (overall={rep.get('overall')!r}, failed={failed})")
    return rep


def _pf_num(v):
    if v == "inf":
        return math.inf
    return None if v is None else float(v)


def _top3_share(terminal: list[PortfolioEvent]) -> Optional[float]:
    by_day: dict[str, float] = defaultdict(float)
    for e in terminal:
        by_day[e.observation_day] += float(e.net_pnl)
    net = sum(by_day.values())
    if net <= 0:
        return None
    top = sorted(by_day.values(), reverse=True)[:3]
    return round(sum(top) / net, 6)


def h1_criteria(fills: list[PortfolioEvent]) -> dict:
    s = summarize_fills(fills)
    terminal = [e for e in fills if e.result in TERMINAL_RESULTS]
    pf = _pf_num(s["pf"])
    share = _top3_share(terminal)
    c = {
        "1_net_gt_0": {"value": s["net"], "pass": s["net"] > 0},
        "2_pf_ge_1_94": {"value": s["pf"], "pass": pf is not None and pf >= PF_HURDLE},
        "3_both_halves_net_gt_0": {
            "value": {"first_half": s["h1"], "second_half": s["h2"]},
            "pass": s["h1"]["net"] > 0 and s["h2"]["net"] > 0,
        },
        "4_max_drawdown_le_1750": {"value": s["max_drawdown"], "pass": s["max_drawdown"] <= MAX_DRAWDOWN},
        "5_top3_days_share_le_60pct": {
            "value": share,
            "pass": share is not None and share <= TOP3_DAY_SHARE_MAX,
        },
    }
    return {"summary": s, "criteria": c, "all_pass": all(v["pass"] for v in c.values())}


def _occupied_hours(fills: list[PortfolioEvent], as_of: datetime) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for e in fills:
        start = parse_ts(e.eligible_fill_ts)
        end = parse_ts(e.exit_ts) if e.exit_ts else as_of
        out[e.family] += max(0.0, (end - start).total_seconds() / 3600.0)
    return dict(out)


def _slip_stress_net(fills: list[PortfolioEvent]) -> float:
    from execution.paper_broker import TICK_VALUE

    per_fill = 2 * float(TICK_VALUE["MNQ"])  # +1 tick adverse per side
    terminal = [e for e in fills if e.result in TERMINAL_RESULTS]
    return round(sum(float(e.net_pnl) - per_fill for e in terminal), 2)


def look_report(
    run: AdapterRun,
    cme_days: dict,
    *,
    as_of: datetime,
    step0_report_path: Path | None,
) -> dict:
    """The single P&L look. Refuses unless every precondition holds."""
    step0 = validate_step0_report(step0_report_path)
    if not run.healthy:
        raise LookRefused(
            "a family adapter failed closed: "
            + json.dumps({f: o.error for f, o in run.families.items() if o.error})
        )
    scored = scored_events(run.events())
    h1_replay = replay_portfolio(scored, excluded_families={FAMILY_ASIA})
    six_replay = replay_portfolio(scored)
    terminal_n = sum(1 for e in h1_replay.fills if e.result in TERMINAL_RESULTS)
    days_n = int(cme_days["observed_completed_days"])
    sample = sample_status(terminal_n, days_n, as_of.date())
    if not sample["look_allowed"]:
        raise LookRefused(
            f"minimum sample not met ({terminal_n}/{MIN_TERMINAL_FILLS} terminal fills, "
            f"{days_n}/{MIN_CME_DAYS} CME days) and deadline {DEADLINE} not reached"
        )

    h1 = h1_criteria(h1_replay.fills)
    if not sample["minimum_sample_met"]:
        verdict = "INSUFFICIENT_SAMPLE"
    elif h1["all_pass"]:
        verdict = "FORWARD_PORTFOLIO_EVIDENCE"
    else:
        verdict = "FORWARD_PORTFOLIO_REJECTED"

    six = summarize_fills(six_replay.fills)
    ex_ids = {e.source_id for e in h1_replay.fills}
    asia_blocked_taken = sorted(
        d.source_id for d in six_replay.decisions
        if d.disposition == "SKIPPED_BUSY_PORTFOLIO"
        and d.blocker_family == FAMILY_ASIA
        and d.source_id in ex_ids
    )
    h2_confirmed = h1["summary"]["net"] > six["net"] and bool(asia_blocked_taken)

    loo = {}
    for fam in H1_FAMILIES:
        reduced = replay_portfolio(scored, excluded_families={FAMILY_ASIA, fam})
        rs = summarize_fills(reduced.fills)
        loo[fam] = {"net_without": rs["net"], "delta_net_full_minus_without": round(h1["summary"]["net"] - rs["net"], 2)}

    terminal = [e for e in h1_replay.fills if e.result in TERMINAL_RESULTS]
    daily_net = round(sum(float(e.net_pnl) for e in terminal if e.family == FAMILY_DAILY), 2)
    hours = _occupied_hours(h1_replay.fills, as_of)
    total_hours = sum(hours.values())
    counts = _occupancy_counts(h1_replay)
    descriptive = {
        "fills_by_family": counts["fills_by_family"],
        "busy_skips_by_family": counts["busy_skips_by_family"],
        "max_trades_skips_by_family": counts["max_trades_skips_by_family"],
        "leave_one_out_inside_five_family": loo,
        "daily_22_share_of_net": (
            round(daily_net / h1["summary"]["net"], 6) if h1["summary"]["net"] else None
        ),
        "daily_22_net": daily_net,
        "daily_22_share_of_occupied_account_hours": (
            round(hours.get(FAMILY_DAILY, 0.0) / total_hours, 6) if total_hours else None
        ),
        "occupied_account_hours_by_family": {k: round(v, 3) for k, v in sorted(hours.items())},
        "monthly": h1["summary"]["monthly"],
        "max_consecutive_losses": h1["summary"]["max_consecutive_losses"],
        "net_with_plus_1_tick_adverse_slippage_per_side": _slip_stress_net(h1_replay.fills),
    }
    return {
        "prereg": PREREG_ID,
        "mode": "look",
        "as_of": as_of.isoformat(),
        "scoring_start": SCORING_START.isoformat(),
        "section7": {
            "step0_attempts": step0.get("attempts_log") or [
                {"generated_at": step0.get("generated_at"), "overall": step0.get("overall")}
            ],
            "look_date": as_of.date().isoformat(),
            "terminal_fills": terminal_n,
            "cme_days": days_n,
            "h1_criteria": h1["criteria"],
            "h1_verdict": verdict,
            "h2": {
                "ex_asia_net": h1["summary"]["net"],
                "six_family_net": six["net"],
                "asia_busy_skipped_fills_taken_by_ex_asia": asia_blocked_taken,
                "verdict": "CONFIRMED" if h2_confirmed else "NOT CONFIRMED",
            },
            "descriptive_table": descriptive,
        },
        "sample": sample,
        "h1_summary": h1["summary"],
        "six_family_summary": six,
        "pipeline_health": {f: o.status for f, o in run.families.items()},
        "authority": "Research only. No runtime, paper, DEMO, live, broker, risk or deployment authority.",
    }
