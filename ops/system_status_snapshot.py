"""Read-only System Status & Evidence Snapshot.

Composes existing read-only sources -- ops.live_box_guard (runtime/drift
identity), journal.journal_logger (trade chain + no-trade liveness),
docs/strategy-rules/Strategy_Inventory.md (evidence classification),
risk_rules.yaml / config.settings (lane configuration), and
context.bar_history (feed liveness) -- into one deterministic, regenerable
JSON artifact.

This module is NOT runtime authority. It never enables/disables a strategy,
changes risk rules, changes sizing, changes broker configuration, submits or
cancels an order, flattens a position, rewrites a journal entry, or alters an
evidence classification. It only reads existing state and reports it -- using
UNKNOWN wherever a value cannot be proven from what is actually on disk, and
`source_of_truth_conflict` wherever two canonical sources disagree, rather
than silently picking one.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from context.five_min_feed import normalize_minutes
from journal.journal_logger import JournalLogger
from ops.live_box_guard import PROOF_CRITICAL_RUNTIME_OVERRIDES, live_box_drift_report

SCHEMA_VERSION = "1.0.0"
UNKNOWN = "UNKNOWN"

ALLOWED_CLASSIFICATIONS = (
    "VALIDATED",
    "PROMISING BUT UNPROVEN",
    "BROKEN",
    "OVERFIT",
    "UNSAFE",
    "WAIT",
    "UNKNOWN",
)

# Strategy_Inventory.md verdicts that fall outside ALLOWED_CLASSIFICATIONS are
# normalized down for the machine-readable field; the literal doc text always
# survives in classification_raw so nothing is lost, only classified.
_VERDICT_MAP = {
    "VALIDATED": "VALIDATED",
    "PAPER PROOF": "PROMISING BUT UNPROVEN",
    "PROMISING BUT UNPROVEN": "PROMISING BUT UNPROVEN",
    "WAIT": "WAIT",
    "RESEARCH ONLY": "WAIT",
    "BROKEN": "BROKEN",
    "BROKEN FOR CURRENT SYSTEM RISK CONSTRAINTS": "BROKEN",
    "RETIRE": "BROKEN",
    "OVERFIT": "OVERFIT",
}

# risk_rules.yaml `strategy.enabled_concepts` key -> Strategy_Inventory.md
# master-table row label, per instrument. Hand-maintained on purpose: a
# concept/instrument absent here reports evidence classification UNKNOWN
# instead of guessing a match. Verified against the doc on 2026-07-27 --
# orb_rejection has NO row in Strategy_Inventory.md today, so it is
# deliberately omitted (falls through to UNKNOWN + a blocker). strat_122 and
# the split 4HR Re-Trigger (MNQ)/(MES) rows are pending in PR #369 (open,
# unmerged as of this writing) -- mapped here ahead of that merge using #369's
# exact row text, so this resolves automatically once #369 lands instead of
# needing a second follow-up PR; until then these correctly report UNKNOWN
# (no matching row exists on main yet).
CONCEPT_TO_INVENTORY_ROW: dict[str, dict[str, str]] = {
    "orb_reclaim": {"MES": "ORB Reclaim (MES)", "MNQ": "ORB Reclaim (MNQ)"},
    "orb_breakout": {"MNQ": "ORB Breakout (MNQ)"},
    "vwap_hold": {"MNQ": "VWAP Hold (MNQ NY)"},
    "vwap_reclaim": {"MNQ": "VWAP Reclaim (MNQ NY)"},
    "vwap_rejection": {"MES": "VWAP Rejection", "MNQ": "VWAP Rejection"},
    "pdh_reclaim": {"MES": "PDH Reclaim", "MNQ": "PDH Reclaim"},
    "pdl_reclaim": {"MES": "PDL Reclaim", "MNQ": "PDL Reclaim"},
    "strat_4hr_retrigger": {"MNQ": "4HR Re-Trigger (MNQ)", "MES": "4HR Re-Trigger (MES)"},
    "strat_322_first_live": {"MNQ": "60M 3-2-2 First Live"},
    "strat_122": {"MES": "MES 1-2-2 (`strat_122`)"},  # pending #369
}

# Env-gated proof-mode lanes (not in risk_rules.enabled_concepts) -> their
# Strategy_Inventory.md row, keyed by the *_MODE env var name. Same
# UNKNOWN-first contract as CONCEPT_TO_INVENTORY_ROW above.
ENV_CONCEPT_TO_INVENTORY_ROW: dict[str, dict[str, str]] = {
    "MNQ_ORB_BREAKOUT_INVERSE_MODE": {"MNQ": "ORB Breakout — inverted (MNQ, paper-only lane)"},  # pending #369
}

# Execution context for env-gated lanes that is provable directly from the
# lane's OWN source module (hardcoded constants, not env-configurable) rather
# than the shared Tradovate broker's global env config. Only lanes verified
# against their actual implementation belong here -- everything else stays
# UNKNOWN in build_env_gated_lanes rather than guessing it mirrors the
# Tradovate path (most proof/observe lanes do NOT: they force PaperBroker).
ENV_LANE_EXECUTION_CONTEXT: dict[str, dict[str, Any]] = {
    "MNQ_ORB_BREAKOUT_INVERSE_MODE": {
        "active_value": "paper_sim",
        "broker": "PaperBroker",
        "entry_model": "marketable_ioc",
        "entry_tolerance_ticks": {"MNQ": 8.0},
        "contracts": {"MNQ": 1},
        "source": "context/mnq_orb_breakout_inverse_paper.py (MARKETABLE_TICKS, CONTRACTS)",
    },
}

# Per-concept required decision timeframe (minutes), used for the feed-
# liveness check. Deliberately conservative: only concepts with an explicit,
# sourced timeframe claim override the system-wide default
# (config.expected_timeframe_minutes / PRIMARY_DECISION_TF, default 15).
# strat_4hr_retrigger is confirmed 5m-native per risk_rules.yaml's own comment
# ("Canonical 5m-native detector (PR #317/#334)"), not a 4-hour bar despite
# the name.
REQUIRED_DECISION_MINUTES_BY_CONCEPT: dict[str, tuple[int, ...]] = {
    "strat_4hr_retrigger": (5,),
}


def _default_decision_minutes(rules: dict[str, Any], env: dict[str, str]) -> int:
    """Mirror config.settings.load_config's expected_timeframe_minutes
    resolution (env override, then risk_rules.yaml, then 15)."""
    raw = env.get("PRIMARY_DECISION_TF") or env.get("EXPECTED_TIMEFRAME_MINUTES")
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    data_quality = rules.get("data_quality") or {}
    try:
        return int(data_quality.get("expected_timeframe_minutes", 15))
    except (TypeError, ValueError):
        return 15


def _required_decision_minutes(active_concepts: set, default_minutes: int) -> tuple[int, ...]:
    """Union of each active concept's required decision timeframe(s) for this
    instrument -- e.g. an instrument running both a 15m-default concept and
    strat_4hr_retrigger (5m-native) is checked on BOTH 5m and 15m, not just
    one blanket timeframe for every instrument."""
    minutes: set = set()
    for concept in active_concepts:
        minutes.update(REQUIRED_DECISION_MINUTES_BY_CONCEPT.get(concept, (default_minutes,)))
    return tuple(sorted(minutes)) or (default_minutes,)

# Legitimate NO_TRADE reasons -- absence of a valid setup, market/session
# state, or a risk/limit boundary. Matched as a case-insensitive substring so
# reason text can evolve without silently reclassifying a real failure as
# healthy. Extend deliberately; never widen to swallow an actual defect.
_LEGITIMATE_NO_TRADE_PATTERNS = (
    "no valid setup", "no_setup", "no setup",
    "choppy", "chop", "range_bound", "range-bound",
    "dead", "non_tradable", "not_trending", "market_condition_not_trending",
    "session", "outside_session", "not in session",
    "risk", "daily_limit", "max_trades", "max_daily_loss",
    "consecutive_losses", "loss_floor",
    "news_blackout", "news blackout",
    "done_for_day", "wait",
)

# Reason substrings that indicate the SYSTEM, not the market, produced the
# no-trade -- these must never be classified NO_TRADE_HEALTHY even if they
# also happen to match a legitimate-looking word above.
_SYSTEM_FAILURE_NO_TRADE_PATTERNS = (
    "missing_data", "missing data", "stale_bar", "stale bar",
    "detector_exception", "detector exception", "exception",
    "never_evaluated", "never evaluated", "journal_failure", "journal failure",
    "feed_gap", "feed gap", "malformed",
)

# Staleness ceiling (minutes) per bar interval before that interval is
# considered gapped/stale. Keyed by MINUTES, not a display label, because
# context.bar_history stores a mixed-timeframe stream per instrument/day (see
# BarHistory._path_for -- one file holds every timeframe) and the only way to
# tell them apart is each record's own `timeframe` field (parsed via
# context.five_min_feed.normalize_minutes). Session-aware thresholds live in
# ops/feed_gap_alarm.py; this reuses its 31-minute floor for the 15m tier and
# scales proportionally for the others.
_LIVENESS_STALENESS_CEILING_BY_MINUTES = {5: 11, 15: 31, 30: 61, 60: 91, 240: 271}
_MINUTES_TO_LABEL = {5: "5m", 15: "15m", 30: "30m", 60: "1h", 240: "4h"}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str | None:
    try:
        return _sha256_text(path.read_text(encoding="utf-8"))
    except OSError:
        return None


def _git(root: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(root),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3.0,
        )
    except Exception:
        return None
    return result.stdout.strip() or None


# ── Strategy_Inventory.md parsing ────────────────────────────────────────────

_TABLE_ROW_RE = re.compile(r"^\|\s*(?P<name>[^|]+?)\s*\|.*\|\s*(?P<verdict>[^|]+?)\s*\|\s*$")
_LAST_UPDATED_RE = re.compile(r"\*Last updated:\s*([0-9]{4}-[0-9]{2}-[0-9]{2})\*")


def parse_strategy_inventory(markdown_text: str) -> dict[str, Any]:
    """Parse the Strategy_Inventory.md master table into {name: verdict_raw}.

    Only reads the master table (the first `| ... | Verdict |` block) -- the
    per-strategy prose profiles below it are not machine-parsed. Returns raw
    verdict text (asterisks/emoji stripped) so callers can normalize or
    display it as-is.
    """
    last_updated_match = _LAST_UPDATED_RE.search(markdown_text)
    last_updated = last_updated_match.group(1) if last_updated_match else None

    rows: dict[str, str] = {}
    in_table = False
    for line in markdown_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("| Strategy |") and "Verdict" in stripped:
            in_table = True
            continue
        if not in_table:
            continue
        if stripped.startswith("|---") or stripped.startswith("| ---"):
            continue
        if not stripped.startswith("|"):
            break
        match = _TABLE_ROW_RE.match(stripped)
        if not match:
            continue
        name = match.group("name").strip()
        verdict_raw = match.group("verdict").strip().strip("*").strip()
        if name and verdict_raw:
            rows[name] = verdict_raw

    sections: dict[str, str] = {}
    heading_re = re.compile(r"^### (.+?)\s*$", re.MULTILINE)
    headings = list(heading_re.finditer(markdown_text))
    for i, heading in enumerate(headings):
        section_name = heading.group(1).strip()
        start = heading.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(markdown_text)
        sections[section_name] = markdown_text[start:end]

    return {"last_updated": last_updated, "rows": rows, "sections": sections}


_EVIDENCE_EPOCH_RE = re.compile(r"[Ff]orward evidence epoch:\s*([0-9TZ:.+-]+)")


def _extract_evidence_epoch(sections: dict[str, str], row_name: str | None) -> str | None:
    """A per-lane forward-evidence epoch is only provable when the doc's own
    prose profile for this exact row states one explicitly (e.g. "Forward
    evidence epoch: 2026-07-27T04:19:13Z") -- the document's top-level "Last
    updated" date is a DIFFERENT concept (when the doc was last edited, not
    when this lane's forward evidence window opened) and must never be
    substituted here. No match -> caller reports UNKNOWN, never a guess."""
    if not row_name:
        return None
    section_text = sections.get(row_name)
    if not section_text:
        return None
    match = _EVIDENCE_EPOCH_RE.search(section_text)
    return match.group(1) if match else None


def _normalize_verdict(verdict_raw: str) -> str:
    """Map a Strategy_Inventory.md verdict onto ALLOWED_CLASSIFICATIONS.

    Matches on the leading verdict token (verdicts like "WAIT -- build
    detector" carry a trailing qualifier); unrecognized text -> UNKNOWN rather
    than a guess.
    """
    head = verdict_raw.split("--")[0].split("—")[0].strip().upper()
    for key, mapped in _VERDICT_MAP.items():
        if head == key or head.startswith(key):
            return mapped
    return UNKNOWN


def build_strategy_evidence(
    inventory_markdown: str | None,
    *,
    enabled_concepts: list[str],
    instruments: list[str],
    disabled_concepts_per_instrument: dict[str, list[str]],
    env_gated_active: list[tuple[str, str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Evidence-classification row per TRACKED (strategy concept, instrument)
    pair -- every enabled concept x instrument, whether or not it is currently
    eligible to fire. A per-instrument exclusion (e.g. MES strat_4hr_retrigger,
    OVERFIT) must stay VISIBLE with `eligible: False` and its exclusion
    reason, not disappear from the registry -- an excluded strategy is exactly
    the kind of state this snapshot exists to surface. `env_gated_active` adds
    rows for active (name, instrument, value) proof-mode lanes that live
    outside risk_rules.yaml entirely.
    """
    parsed = (
        parse_strategy_inventory(inventory_markdown) if inventory_markdown
        else {"last_updated": None, "rows": {}, "sections": {}}
    )
    rows = parsed["rows"]
    sections = parsed["sections"]
    evidence_date = parsed["last_updated"]

    def _row(strategy: str, instrument: str, row_name: str | None, *, eligible: bool, exclusion_reason: str | None) -> dict[str, Any]:
        verdict_raw = rows.get(row_name) if row_name else None
        return {
            "strategy": strategy,
            "instrument": instrument,
            "eligible": eligible,
            "exclusion_reason": exclusion_reason,
            "classification": _normalize_verdict(verdict_raw) if verdict_raw else UNKNOWN,
            "classification_raw": verdict_raw or UNKNOWN,
            "classification_source": (
                "docs/strategy-rules/Strategy_Inventory.md" if verdict_raw else UNKNOWN
            ),
            "inventory_row_matched": row_name,
            "evidence_date": evidence_date if verdict_raw else None,
            "research_status": UNKNOWN,
            "runtime_parity_status": UNKNOWN,
            "paper_forward_status": UNKNOWN,
            "latest_valid_evidence_ref": (
                f"docs/strategy-rules/Strategy_Inventory.md#{row_name}" if row_name else None
            ),
            "evidence_sha": None,
            "candidate_count": None,
            "executable_candidate_count": None,
            "fill_count": None,
            "net_pnl": None,
            "profit_factor": None,
            "h1_h2_status": None,
            "max_drawdown": None,
            "current_blocker": (
                None if row_name and verdict_raw else
                "no Strategy_Inventory.md row mapped for this strategy/instrument"
            ),
            "notes": UNKNOWN if not verdict_raw else "",
            "pending_reconciliation": False,
            "source_of_truth_conflict": False,
            "conflicting_sources": [],
        }

    out: list[dict[str, Any]] = []
    for concept in enabled_concepts:
        row_map = CONCEPT_TO_INVENTORY_ROW.get(concept, {})
        for instrument in instruments:
            excluded = concept in (disabled_concepts_per_instrument.get(instrument) or [])
            out.append(
                _row(
                    concept, instrument, row_map.get(instrument),
                    eligible=not excluded,
                    exclusion_reason="disabled_concepts_per_instrument" if excluded else None,
                )
            )

    for name, instrument, value in (env_gated_active or []):
        row_map = ENV_CONCEPT_TO_INVENTORY_ROW.get(name, {})
        out.append(
            _row(name, instrument, row_map.get(instrument), eligible=True, exclusion_reason=None)
        )

    return out


# ── Runtime lanes ────────────────────────────────────────────────────────────

def build_runtime_lanes(
    *,
    enabled_concepts: list[str],
    instruments: list[str],
    disabled_concepts_per_instrument: dict[str, list[str]],
    entry_fill_model: str,
    entry_tolerance_ticks_by_root: dict[str, float],
    schedule_mode: str,
    contracts_by_instrument: dict[str, int] | None,
    repo_commit: str | None,
    evidence_epoch_lookup,
) -> list[dict[str, Any]]:
    """Only ELIGIBLE lanes (enabled and not per-instrument-excluded) -- these
    actually route orders. `evidence_epoch_lookup(concept, instrument)` must
    return the lane's OWN forward-evidence epoch or UNKNOWN; it must never be
    the Strategy_Inventory.md document's "Last updated" date, which describes
    when the doc was edited, not when this lane's evidence window opened.

    entry_fill_model/entry_tolerance_ticks_by_root are genuinely GLOBAL here,
    not a per-lane approximation: every concept below routes through the SAME
    Tradovate broker instance, which reads TRADOVATE_ENTRY_EXECUTION_MODE and
    ENTRY_SLIPPAGE_TOLERANCE_TICKS_<ROOT> once per instrument regardless of
    which strategy generated the signal (execution/tradovate_broker.py has no
    per-strategy override) -- sharing them across concepts on the same
    instrument is not an approximation, it is what the broker actually does.
    Lanes that do NOT share this path (env-gated PaperBroker-only proof/paper
    lanes) are reported separately by build_env_gated_lanes with their own,
    independently-resolved execution context.
    """
    contracts_by_instrument = contracts_by_instrument or {}
    lanes: list[dict[str, Any]] = []
    for concept in enabled_concepts:
        for instrument in instruments:
            if concept in (disabled_concepts_per_instrument.get(instrument) or []):
                continue
            tolerance = entry_tolerance_ticks_by_root.get(instrument)
            lanes.append(
                {
                    "strategy": concept,
                    "instrument": instrument,
                    "runtime_state": "paper_forward" if schedule_mode == "current" else schedule_mode,
                    "gate_source": "risk_rules.strategy.enabled_concepts",
                    "broker": "tradovate",
                    "entry_model": entry_fill_model if entry_fill_model else UNKNOWN,
                    "entry_tolerance_ticks": tolerance if tolerance is not None else UNKNOWN,
                    "contracts": contracts_by_instrument.get(instrument, 1),
                    "schedule_mode": schedule_mode,
                    "evidence_epoch": evidence_epoch_lookup(concept, instrument),
                    "current_runtime_sha": repo_commit or UNKNOWN,
                    "expected_evidence_sha": UNKNOWN,
                    "drift_status": UNKNOWN,
                }
            )
    return lanes


# *_MODE values that do NOT actually route an order. "off"/"legacy"/""/"0"/
# "false" mean the flag is unset; "observe_only" is the proof-mode contract's
# own audit-only value (context/mnq_orb_breakout_inverse_paper.py and its
# siblings: "observe_only cannot create" a position) -- real per module, not
# a guess, but still not a trading lane. Anything else (paper_sim,
# tradovate_demo, ...) is treated as active.
_ENV_MODE_INERT_VALUES = {"", "0", "false", "off", "legacy", "observe_only"}


def active_env_gated_flags(env: dict[str, str]) -> list[tuple[str, str, str]]:
    """(env_name, instrument, value) for every PROOF_CRITICAL_RUNTIME_OVERRIDES
    *_MODE flag currently set to a value that actually routes an order."""
    out: list[tuple[str, str, str]] = []
    for name in PROOF_CRITICAL_RUNTIME_OVERRIDES:
        if not name.endswith("_MODE"):
            continue
        raw = env.get(name)
        value = (raw or "").strip().lower()
        if value in _ENV_MODE_INERT_VALUES:
            continue
        instrument = "MNQ" if name.startswith("MNQ_") else "MES" if name.startswith("MES_") else UNKNOWN
        out.append((name, instrument, value))
    return out


def build_env_gated_lanes(env: dict[str, str], *, evidence_epoch_lookup=None) -> list[dict[str, Any]]:
    """Runtime lanes gated by a PROOF_CRITICAL_RUNTIME_OVERRIDES *_MODE env
    flag rather than risk_rules.yaml enabled_concepts (e.g. proof-mode evidence
    trackers under execution/, or the frozen inverse-ORB paper lane). Only
    lanes actually routing an order are included (see
    _ENV_MODE_INERT_VALUES) -- an `observe_only` flag is audit-only, not a
    runtime lane. Execution context (broker/entry_model/tolerance/contracts)
    is resolved from ENV_LANE_EXECUTION_CONTEXT when the lane's OWN source
    module documents it; otherwise it stays UNKNOWN rather than inheriting the
    unrelated global Tradovate config -- most of these lanes force PaperBroker
    and do NOT share the Tradovate path at all.
    """
    evidence_epoch_lookup = evidence_epoch_lookup or (lambda name, instrument: UNKNOWN)
    lanes: list[dict[str, Any]] = []
    for name, instrument, value in active_env_gated_flags(env):
        verified = ENV_LANE_EXECUTION_CONTEXT.get(name)
        if verified and value == verified.get("active_value"):
            broker = verified["broker"]
            entry_model = verified["entry_model"]
            tolerance = verified["entry_tolerance_ticks"].get(instrument, UNKNOWN)
            contracts = verified["contracts"].get(instrument, UNKNOWN)
            source_note = f"execution context verified from {verified['source']}"
        else:
            broker = UNKNOWN
            entry_model = UNKNOWN
            tolerance = UNKNOWN
            contracts = UNKNOWN
            source_note = (
                f"env-gated proof-mode flag {name}={value!r}; no verified execution-context "
                "source for this lane in ENV_LANE_EXECUTION_CONTEXT (most proof/observe lanes "
                "force PaperBroker with their own per-order overrides, not the global Tradovate config)"
            )
        lanes.append(
            {
                "strategy": name,
                "instrument": instrument,
                "runtime_state": "env_gated_active",
                "gate_source": f"env:{name}",
                "broker": broker,
                "entry_model": entry_model,
                "entry_tolerance_ticks": tolerance,
                "contracts": contracts,
                "schedule_mode": UNKNOWN,
                "evidence_epoch": evidence_epoch_lookup(name, instrument),
                "current_runtime_sha": UNKNOWN,
                "expected_evidence_sha": UNKNOWN,
                "drift_status": UNKNOWN,
                "notes": source_note,
            }
        )
    return lanes


# ── Trade chain health + no-trade liveness ───────────────────────────────────

def _classify_no_trade_reason(reason: str | None) -> tuple[str | None, bool | None]:
    if not reason:
        return None, None
    lowered = reason.lower()
    for pattern in _SYSTEM_FAILURE_NO_TRADE_PATTERNS:
        if pattern in lowered:
            return reason, False
    for pattern in _LEGITIMATE_NO_TRADE_PATTERNS:
        if pattern in lowered:
            return reason, True
    return reason, None  # unrecognized reason text -- neither confirmed healthy nor confirmed a failure


_FEED_LOOKBACK_BARS = 2000  # generous: several days of 5m bars for one instrument, mixed-timeframe


def _bucket_bars_by_minutes(bars: list[dict]) -> dict[int, list[dict]]:
    """Split a mixed-timeframe bar stream by each record's own `timeframe`
    field (context.bar_history stores every timeframe in one per-instrument-
    per-day file -- see BarHistory._path_for). Bars with an unparseable or
    missing `timeframe` are dropped rather than guessed into a bucket, since a
    5m bar silently counted as 15m (or vice versa) is exactly the failure mode
    this function exists to prevent."""
    buckets: dict[int, list[dict]] = {}
    for bar in bars:
        minutes = normalize_minutes(bar.get("timeframe"))
        if minutes is None:
            continue
        buckets.setdefault(minutes, []).append(bar)
    return buckets


def build_feed_liveness(
    bar_history: Any,
    instrument: str,
    *,
    for_date: date,
    now: datetime,
    required_minutes: tuple[int, ...] = (5, 15),
) -> dict[str, Any]:
    """Per-instrument feed liveness across the timeframes this lane actually
    needs, each checked against the bars ACTUALLY LABELED that timeframe --
    never a blanket "last bar received, whatever timeframe it happened to be."
    A fresh 15m bar must not be able to paper over a stale 5m feed, or vice
    versa.

    `bar_history` is a context.bar_history.BarHistory instance (or any object
    exposing `.recent(instrument, n, for_date=..., lookback_days=...)` --
    injected so this stays testable without real files). Timeframes this
    instrument's bar file has never recorded at all (bucket is empty) are
    reported stale, not silently skipped.
    """
    try:
        bars = bar_history.recent(instrument, _FEED_LOOKBACK_BARS, for_date=for_date, lookback_days=3)
    except Exception:
        bars = []
    buckets = _bucket_bars_by_minutes(bars)

    out: dict[str, Any] = {}
    for minutes in required_minutes:
        staleness_ceiling = _LIVENESS_STALENESS_CEILING_BY_MINUTES.get(minutes, minutes * 2 + 1)
        label = _MINUTES_TO_LABEL.get(minutes, f"{minutes}m")
        tf_bars = buckets.get(minutes) or []
        bar = tf_bars[-1] if tf_bars else None
        ts_raw = (bar or {}).get("ts")
        age_minutes = None
        if ts_raw:
            try:
                parsed = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                age_minutes = (now - parsed).total_seconds() / 60.0
            except ValueError:
                age_minutes = None
        stale = age_minutes is None or age_minutes > staleness_ceiling
        out[label] = {
            "minutes": minutes,
            "bars_seen_this_timeframe": len(tf_bars),
            "last_bar_ts": ts_raw,
            "staleness_minutes": round(age_minutes, 1) if age_minutes is not None else None,
            "staleness_ceiling_minutes": staleness_ceiling,
            "stale": stale,
        }
    return out


def classify_no_trade_liveness(
    *,
    instrument: str,
    entries: list[dict],
    feed_liveness: dict[str, Any],
) -> dict[str, Any]:
    """Distinguish NO_TRADE_HEALTHY from NO_TRADE_SYSTEM_FAILURE for one
    instrument/day, per the operator's addendum: a trade-chain that shows
    "0 attempts, 0 fills, 0 orphans" can PASS while the system never actually
    ran. This checks (a) at least one strategy-evaluation decision exists for
    the day, (b) the feed was not stale/gapped, and (c) any NO_TRADE reason is
    legitimate (no setup / chop / session / risk / limits), not a system fault
    (missing data / detector exception / stale bars / never evaluated)."""
    instrument_entries = [e for e in entries if e.get("instrument") == instrument]
    decisions = [e for e in instrument_entries if e.get("decision")]
    traded = any(e.get("decision") == "TRADE" for e in decisions)
    stale_timeframes = [tf for tf, data in feed_liveness.items() if data.get("stale")]

    if traded:
        return {
            "diagnosis": "TRADED",
            "reason": None,
            "reason_legitimate": None,
            "strategy_evaluated": True,
            "stale_timeframes": stale_timeframes,
            "summary": f"{instrument}: at least one TRADE decision recorded today.",
        }

    no_trade_entries = [e for e in decisions if e.get("decision") == "NO_TRADE"]
    latest_reason = no_trade_entries[-1].get("reason") if no_trade_entries else None
    reason, legitimate = _classify_no_trade_reason(latest_reason)

    strategy_evaluated = bool(decisions)
    if stale_timeframes:
        diagnosis = "NO_TRADE_SYSTEM_FAILURE"
        stale_detail = "; ".join(
            f"{tf} last bar {feed_liveness[tf].get('last_bar_ts')!r}" for tf in stale_timeframes
        )
        summary = f"{instrument}: feed stale on {', '.join(stale_timeframes)} ({stale_detail})."
    elif not strategy_evaluated:
        diagnosis = "NO_TRADE_SYSTEM_FAILURE"
        summary = f"{instrument}: no strategy-evaluation decision recorded today despite a live feed."
    elif legitimate is False:
        diagnosis = "NO_TRADE_SYSTEM_FAILURE"
        summary = f"{instrument}: NO_TRADE reason {reason!r} indicates a system fault, not a market condition."
    elif legitimate is True:
        diagnosis = "NO_TRADE_HEALTHY"
        summary = f"{instrument}: strategy evaluated normally, no valid setup ({reason!r})."
    else:
        diagnosis = UNKNOWN
        summary = f"{instrument}: NO_TRADE reason {reason!r} not recognized as legitimate or a system fault."

    return {
        "diagnosis": diagnosis,
        "reason": reason,
        "reason_legitimate": legitimate,
        "strategy_evaluated": strategy_evaluated,
        "stale_timeframes": stale_timeframes,
        "summary": summary,
    }


def build_trade_chain_health(
    entries: list[dict],
    *,
    instruments: list[str],
    feed_liveness_by_instrument: dict[str, dict[str, Any]] | None = None,
    broker_open_positions: dict[str, bool] | None = None,
) -> dict[str, Any]:
    """Reconcile the day's journal into the trade-chain accounting the
    operator specified:

        attempts = fills + cancellations + rejects + known_no_fills
        fills = resolved + legitimately_open

    An OUTCOME's `result` is the fill/no-fill signal: WIN/LOSS/BREAKEVEN are
    resolved fills, CANCELLED is either a cancellation (never attempted) or a
    known no-fill (IOC/limit expired unfilled) depending on
    `no_fill_reason` -- broker-rejected attempts are also folded into
    known_no_fills unless a distinct reject reason is present, since this
    journal format does not carry a separate REJECTED result value (see
    execution/no_fill_taxonomy.py; NO_FILL_BROKER_REJECTED is the reject
    signal within CANCELLED outcomes).
    """
    feed_liveness_by_instrument = feed_liveness_by_instrument or {}
    broker_open_positions = broker_open_positions or {}

    attempts = 0
    fills = 0
    cancellations = 0
    rejects = 0
    known_no_fills = 0
    resolved = 0
    orphan_count = 0
    missing_outcome_count = 0
    stale_order_count = 0
    duplicate_order_identity_count = 0
    last_complete_trade_chain_at = None
    last_anomaly_at = None
    last_anomaly_summary = None

    seen_order_ids: set = set()
    open_trade_by_instrument: dict[str, dict] = {}

    for entry in entries:
        decision = entry.get("decision")
        risk_check = entry.get("risk_check") or {}
        approved = decision == "TRADE" and risk_check.get("result") == "APPROVED"
        inline_outcome = entry.get("outcome") or {}

        if approved:
            attempts += 1
            instrument = entry.get("instrument")
            # Current journal writes always follow an approved TRADE with a
            # standalone OUTCOME entry (JournalLogger.log_outcome) -- an
            # inline `outcome` on the TRADE row itself is a legacy format not
            # produced by any live write path today, so it is intentionally
            # not double-counted here; only the standalone OUTCOME branch
            # below advances fills/resolved/etc.
            if not inline_outcome.get("result"):
                open_trade_by_instrument[instrument] = entry

        if entry.get("type") == "OUTCOME":
            outcome = entry.get("outcome") or {}
            result = outcome.get("result")
            instrument = entry.get("instrument")
            if result in ("WIN", "LOSS", "BREAKEVEN"):
                fills += 1
                resolved += 1
                last_complete_trade_chain_at = entry.get("ts") or last_complete_trade_chain_at
            elif result == "CANCELLED":
                no_fill_reason = outcome.get("no_fill_reason")
                if no_fill_reason == "NO_FILL_BROKER_REJECTED":
                    rejects += 1
                elif no_fill_reason:
                    known_no_fills += 1
                else:
                    cancellations += 1
            open_trade_by_instrument.pop(instrument, None)

        if entry.get("type") == "ORDER_IDS":
            order_ids = entry.get("order_ids") or {}
            for oid in order_ids.values():
                if oid in seen_order_ids:
                    duplicate_order_identity_count += 1
                    last_anomaly_at = entry.get("ts") or last_anomaly_at
                    last_anomaly_summary = f"duplicate order id {oid!r}"
                else:
                    seen_order_ids.add(oid)

    legitimately_open = 0
    for instrument, open_entry in open_trade_by_instrument.items():
        broker_open = broker_open_positions.get(instrument)
        if broker_open is False:
            orphan_count += 1
            last_anomaly_at = open_entry.get("ts") or last_anomaly_at
            last_anomaly_summary = f"{instrument}: journal shows open, broker reports flat (orphan)"
        elif broker_open is None:
            missing_outcome_count += 1
        else:
            legitimately_open += 1
    fills_accounted = resolved + legitimately_open
    if legitimately_open == 0 and missing_outcome_count == 0:
        fills_accounted = resolved

    attempts_eq_sum = attempts == (fills + cancellations + rejects + known_no_fills) or (
        # trades still open (no OUTCOME yet) are attempts without a fill/no-fill
        # signal yet -- valid only when every unresolved trade is legitimately
        # open or missing (never silently dropped).
        attempts - (fills + cancellations + rejects + known_no_fills)
        == legitimately_open + missing_outcome_count
    )
    fills_eq_sum = fills == (resolved + legitimately_open) or fills == resolved

    broker_journal_parity = (
        "FAIL" if orphan_count > 0 else
        "PASS" if broker_open_positions else
        UNKNOWN
    )
    flat_state_parity = (
        "FAIL" if orphan_count > 0 else
        "PASS" if not open_trade_by_instrument else
        "WARN" if legitimately_open else
        UNKNOWN
    )

    liveness = {
        instrument: classify_no_trade_liveness(
            instrument=instrument,
            entries=entries,
            feed_liveness=feed_liveness_by_instrument.get(instrument, {}),
        )
        for instrument in instruments
    }
    liveness_failure = any(
        row["diagnosis"] == "NO_TRADE_SYSTEM_FAILURE" for row in liveness.values()
    )

    if not attempts_eq_sum or not fills_eq_sum or orphan_count > 0 or duplicate_order_identity_count > 0:
        overall_state = "FAIL"
    elif liveness_failure or stale_order_count > 0 or missing_outcome_count > 0:
        overall_state = "WARN"
    elif broker_journal_parity == UNKNOWN and not entries:
        overall_state = "UNKNOWN"
    else:
        overall_state = "PASS"

    return {
        "counts": {
            "attempts": attempts,
            "fills": fills,
            "cancellations": cancellations,
            "rejects": rejects,
            "known_no_fills": known_no_fills,
            "resolved": resolved,
            "legitimately_open": legitimately_open,
            "orphan_count": orphan_count,
            "missing_outcome_count": missing_outcome_count,
            "stale_order_count": stale_order_count,
            "duplicate_order_identity_count": duplicate_order_identity_count,
        },
        "accounting": {
            "formula": "attempts = fills + cancellations + rejects + known_no_fills (+ still-open); fills = resolved + legitimately_open",
            "attempts_equation_holds": attempts_eq_sum,
            "fills_equation_holds": fills_eq_sum,
        },
        "broker_journal_parity": broker_journal_parity,
        "flat_state_parity": flat_state_parity,
        "last_complete_trade_chain_at": last_complete_trade_chain_at,
        "last_anomaly_at": last_anomaly_at,
        "last_anomaly_summary": last_anomaly_summary,
        "liveness": liveness,
        "overall_state": overall_state,
    }


# ── Snapshot assembly ────────────────────────────────────────────────────────

def build_system_status_snapshot(
    *,
    repo_root: str | Path | None = None,
    risk_rules_path: str | Path = "risk_rules.yaml",
    log_dir: str | Path = "logs",
    for_date: date | None = None,
    generated_at: datetime | None = None,
    env: dict[str, str] | None = None,
    changed_files: list[str] | None = None,
) -> dict[str, Any]:
    """Build the full snapshot dict. Pure aside from reading existing files
    and (via live_box_drift_report) two cheap `git` subprocess calls."""
    import os as _os

    root = Path(repo_root or Path(__file__).resolve().parents[1]).resolve()
    risk_path = Path(risk_rules_path)
    if not risk_path.is_absolute():
        risk_path = root / risk_path
    log_path = Path(log_dir)
    if not log_path.is_absolute():
        log_path = root / log_path
    the_date = for_date or date.today()
    now = generated_at or datetime.now(timezone.utc)
    env = env if env is not None else dict(_os.environ)

    unknown_fields: list[str] = []

    def mark_unknown(path: str) -> None:
        unknown_fields.append(path)

    drift = live_box_drift_report(repo_root=root, risk_rules_path=risk_path, log_dir=log_path, for_date=the_date)

    origin_main_commit = _git(root, "rev-parse", "origin/main")
    if origin_main_commit is None:
        mark_unknown("repo.origin_main_commit")

    release_manifest_path = root / "release_manifest.json"
    deployed_sha = UNKNOWN
    if release_manifest_path.exists():
        try:
            import json as _json
            manifest = _json.loads(release_manifest_path.read_text(encoding="utf-8"))
            deployed_sha = (manifest.get("repo") or {}).get("commit") or UNKNOWN
        except (OSError, ValueError):
            deployed_sha = UNKNOWN
    if deployed_sha == UNKNOWN:
        mark_unknown("deployed_sha")

    # ── config ────────────────────────────────────────────────────────────
    try:
        import yaml
        rules = yaml.safe_load(risk_path.read_text(encoding="utf-8")) or {}
    except (OSError, Exception):
        rules = {}
        mark_unknown("risk_rules")

    strategy_cfg = rules.get("strategy", {}) or {}
    enabled_concepts = list(strategy_cfg.get("enabled_concepts") or [])
    disabled_per_instrument = strategy_cfg.get("disabled_concepts_per_instrument") or {}
    instruments = list((rules.get("instruments") or {}).get("allowed") or [])
    schedule_mode = str(env.get("SCHEDULE_MODE") or (rules.get("schedule") or {}).get("mode") or "current").strip().lower()

    tolerance_defaults = {"MES": 16.0, "MNQ": 32.0}
    tolerance_global = env.get("ENTRY_SLIPPAGE_TOLERANCE_TICKS")
    entry_tolerance_ticks_by_root: dict[str, float] = {}
    for root_symbol in instruments:
        raw = env.get(f"ENTRY_SLIPPAGE_TOLERANCE_TICKS_{root_symbol}") or tolerance_global
        try:
            value = float(raw) if raw not in (None, "") else None
        except (TypeError, ValueError):
            value = None
        entry_tolerance_ticks_by_root[root_symbol] = value if value is not None else tolerance_defaults.get(root_symbol, UNKNOWN)

    # Runtime lanes trade on the real Tradovate demo broker, not the internal
    # PaperBroker/replay simulator -- so the effective entry model here is the
    # LIVE broker's TRADOVATE_ENTRY_EXECUTION_MODE (execution/tradovate_broker.py
    # `_entry_execution_mode`, default "legacy"), not ENTRY_FILL_MODEL (which
    # only governs the offline replay/paper simulator and is irrelevant to a
    # deployed demo lane).
    entry_fill_model = (env.get("TRADOVATE_ENTRY_EXECUTION_MODE") or "legacy").strip().lower() or "legacy"

    inventory_path = root / "docs" / "strategy-rules" / "Strategy_Inventory.md"
    inventory_markdown = inventory_path.read_text(encoding="utf-8") if inventory_path.exists() else None
    if inventory_markdown is None:
        mark_unknown("strategy_evidence")

    parsed_inventory = (
        parse_strategy_inventory(inventory_markdown) if inventory_markdown
        else {"last_updated": None, "rows": {}, "sections": {}}
    )
    env_gated_active = active_env_gated_flags(env)

    def _concept_evidence_epoch(concept: str, instrument: str) -> str:
        row_name = CONCEPT_TO_INVENTORY_ROW.get(concept, {}).get(instrument)
        epoch = _extract_evidence_epoch(parsed_inventory["sections"], row_name)
        if epoch is None:
            mark_unknown(f"runtime_lanes[{concept}/{instrument}].evidence_epoch")
            return UNKNOWN
        return epoch

    def _env_evidence_epoch(name: str, instrument: str) -> str:
        row_name = ENV_CONCEPT_TO_INVENTORY_ROW.get(name, {}).get(instrument)
        epoch = _extract_evidence_epoch(parsed_inventory["sections"], row_name)
        if epoch is None:
            mark_unknown(f"runtime_lanes[{name}/{instrument}].evidence_epoch")
            return UNKNOWN
        return epoch

    strategy_evidence = build_strategy_evidence(
        inventory_markdown,
        enabled_concepts=enabled_concepts,
        instruments=instruments,
        disabled_concepts_per_instrument=disabled_per_instrument,
        env_gated_active=env_gated_active,
    )
    for row in strategy_evidence:
        if row["classification"] == UNKNOWN:
            mark_unknown(f"strategy_evidence[{row['strategy']}/{row['instrument']}].classification")

    runtime_lanes = build_runtime_lanes(
        enabled_concepts=enabled_concepts,
        instruments=instruments,
        disabled_concepts_per_instrument=disabled_per_instrument,
        entry_fill_model=entry_fill_model,
        entry_tolerance_ticks_by_root=entry_tolerance_ticks_by_root,
        schedule_mode=schedule_mode,
        contracts_by_instrument=None,
        repo_commit=drift.get("commit"),
        evidence_epoch_lookup=_concept_evidence_epoch,
    )
    runtime_lanes += build_env_gated_lanes(env, evidence_epoch_lookup=_env_evidence_epoch)

    # ── trade chain + liveness ───────────────────────────────────────────
    journal = JournalLogger(log_dir=str(log_path))
    entries = journal.read_day(the_date)
    journal_file = log_path / f"journal_{the_date.isoformat()}.jsonl"

    default_decision_minutes = _default_decision_minutes(rules, env)
    active_concepts_by_instrument: dict[str, set] = {}
    for lane in runtime_lanes:
        instr = lane.get("instrument")
        if instr in instruments:
            active_concepts_by_instrument.setdefault(instr, set()).add(lane["strategy"])

    feed_liveness_by_instrument: dict[str, Any] = {}
    try:
        from context.bar_history import BarHistory
        bar_history = BarHistory(log_dir=str(log_path))
        for instrument in instruments:
            required_minutes = _required_decision_minutes(
                active_concepts_by_instrument.get(instrument, set()), default_decision_minutes
            )
            feed_liveness_by_instrument[instrument] = build_feed_liveness(
                bar_history, instrument, for_date=the_date, now=now, required_minutes=required_minutes
            )
    except Exception:
        mark_unknown("trade_chain.liveness.feed")

    trade_chain = build_trade_chain_health(
        entries,
        instruments=instruments,
        feed_liveness_by_instrument=feed_liveness_by_instrument,
        broker_open_positions=None,  # requires a live broker read; not available to a repo-only snapshot generator
    )
    mark_unknown("trade_chain.broker_journal_parity")

    # ── change-scope / required-tests (Session Safety addendum) ─────────
    change_scope_status: dict[str, Any] = {"status": "NOT_APPLICABLE", "changed_categories": [], "missing_required_tests": []}
    if changed_files is not None:
        from ops.change_scope_test_gate import evaluate_test_coverage
        change_scope_status = evaluate_test_coverage(changed_files)
    else:
        mark_unknown("repo_health.change_scope_test_coverage")

    repo_health = {
        "dirty_worktree": bool(drift.get("git_dirty")),
        "dirty_file_count": drift.get("git_dirty_count"),
        "deployed_sha_mismatch": (
            deployed_sha != UNKNOWN and drift.get("commit") not in (None, UNKNOWN) and deployed_sha != drift.get("commit")
        ) if deployed_sha != UNKNOWN and drift.get("commit") else UNKNOWN,
        "active_stash_count": _git_stash_count(root),
        "local_main_divergence": _local_main_divergence(root),
        "change_scope_test_coverage": change_scope_status,
    }
    if repo_health["active_stash_count"] is None:
        mark_unknown("repo_health.active_stash_count")
        repo_health["active_stash_count"] = UNKNOWN
    if repo_health["local_main_divergence"] is None:
        mark_unknown("repo_health.local_main_divergence")
        repo_health["local_main_divergence"] = UNKNOWN

    blockers = _build_blockers(
        drift=drift,
        strategy_evidence=strategy_evidence,
        env_gated_lanes=[lane for lane in runtime_lanes if lane["gate_source"].startswith("env:")],
        trade_chain=trade_chain,
        repo_health=repo_health,
        now=now,
    )

    source_of_truth_conflict = any(row.get("source_of_truth_conflict") for row in strategy_evidence)

    generator_sha = _sha256_file(Path(__file__))

    snapshot: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "generator": {
            "module": "ops.system_status_snapshot",
            "version": SCHEMA_VERSION,
            "script_sha256": generator_sha,
        },
        "repo": {
            "head_commit": drift.get("commit"),
            "head_branch": drift.get("branch"),
            "origin_main_commit": origin_main_commit or UNKNOWN,
            "dirty": drift.get("git_dirty"),
            "identity_source": drift.get("identity_source"),
        },
        "deployed_sha": deployed_sha,
        "runtime_drift": {
            "ok": drift.get("ok"),
            "status": drift.get("status"),
            "summary": drift.get("summary"),
            "mismatches": drift.get("mismatches"),
            "missing_pins": drift.get("missing_pins"),
            "unpinned_runtime_overrides": drift.get("unpinned_runtime_overrides"),
        },
        "data_freshness": {
            "journal_path": str(journal_file),
            "journal_exists": journal_file.exists(),
            "journal_last_entry_ts": entries[-1].get("ts") if entries else None,
        },
        "runtime_lanes": runtime_lanes,
        "strategy_evidence": strategy_evidence,
        "trade_chain": {"date": the_date.isoformat(), **trade_chain},
        "repo_health": repo_health,
        "blockers": blockers,
        "source_of_truth_conflict": source_of_truth_conflict,
        "unknown_fields": sorted(set(unknown_fields)),
    }
    return snapshot


def _git_stash_count(root: Path) -> int | None:
    out = _git(root, "stash", "list")
    if out is None:
        return None
    return 0 if not out else len(out.splitlines())


def _local_main_divergence(root: Path) -> dict[str, int] | None:
    branch = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch is None:
        return None
    counts = _git(root, "rev-list", "--left-right", "--count", "origin/main...HEAD")
    if counts is None:
        return None
    try:
        behind_str, ahead_str = counts.split()
        return {"ahead": int(ahead_str), "behind": int(behind_str)}
    except ValueError:
        return None


REQUIRED_TOP_LEVEL_KEYS = (
    "schema_version", "generated_at", "generator", "repo", "deployed_sha",
    "runtime_drift", "data_freshness", "runtime_lanes", "strategy_evidence",
    "trade_chain", "repo_health", "blockers", "source_of_truth_conflict", "unknown_fields",
)


def validate_snapshot_schema(snapshot: Any) -> list[str]:
    """Return validation errors; an empty list means the snapshot is safe to write."""
    if not isinstance(snapshot, dict):
        return ["snapshot is not a dict"]
    errors = [f"missing required key: {key}" for key in REQUIRED_TOP_LEVEL_KEYS if key not in snapshot]
    if snapshot.get("schema_version") not in (None, SCHEMA_VERSION):
        errors.append(f"unexpected schema_version: {snapshot.get('schema_version')!r}")
    return errors


def write_snapshot_atomic(path: str | Path, snapshot: dict[str, Any]) -> dict[str, Any]:
    """Validate, then atomically replace `path` with `snapshot`.

    On validation failure this raises ValueError WITHOUT touching `path` at
    all -- the previous last-known-good snapshot (if any) survives untouched.
    A snapshot-generation failure must never alter trading behavior and must
    never leave a partially-written or blank status file on disk.
    """
    import json

    errors = validate_snapshot_schema(snapshot)
    if errors:
        raise ValueError(f"refusing to write invalid snapshot: {'; '.join(errors)}")

    from agent.daily_summary import atomic_write_text

    target = Path(path)
    atomic_write_text(target, json.dumps(snapshot, indent=2, sort_keys=True, default=str))
    return {"written": True, "path": str(target)}


def _build_blockers(
    *,
    drift: dict[str, Any],
    strategy_evidence: list[dict[str, Any]],
    env_gated_lanes: list[dict[str, Any]],
    trade_chain: dict[str, Any],
    repo_health: dict[str, Any],
    now: datetime,
) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    ts = now.isoformat()

    def add(id_: str, severity: str, category: str, summary: str, *, strategy=None, instrument=None, ref=None) -> None:
        blockers.append(
            {
                "id": id_,
                "severity": severity,
                "category": category,
                "strategy": strategy,
                "instrument": instrument,
                "summary": summary,
                "evidence_ref": ref,
                "first_seen": ts,
                "last_seen": ts,
            }
        )

    if drift.get("status") != "ok":
        add(
            "runtime-drift", "BLOCKER" if drift.get("status") == "error" else "WARN",
            "runtime_parity", drift.get("summary") or "live_box_guard reports drift", ref="ops/live_box_guard.py",
        )

    for row in strategy_evidence:
        if row["classification"] == UNKNOWN:
            add(
                f"evidence-unknown-{row['strategy']}-{row['instrument']}", "WARN", "evidence_gap",
                f"{row['strategy']}/{row['instrument']}: no Strategy_Inventory.md row mapped -- classification UNKNOWN",
                strategy=row["strategy"], instrument=row["instrument"],
                ref="docs/strategy-rules/Strategy_Inventory.md",
            )

    for lane in env_gated_lanes:
        add(
            f"env-gated-unmapped-{lane['strategy']}", "INFO", "evidence_gap",
            f"{lane['strategy']} is active via env but has no Strategy_Inventory.md mapping in this snapshot",
            strategy=lane["strategy"], instrument=lane.get("instrument"),
        )

    for instrument, row in (trade_chain.get("liveness") or {}).items():
        if row.get("diagnosis") == "NO_TRADE_SYSTEM_FAILURE":
            add(
                f"no-trade-system-failure-{instrument}", "BLOCKER", "liveness",
                row.get("summary") or f"{instrument}: system-failure no-trade diagnosis", instrument=instrument,
            )

    if trade_chain.get("overall_state") == "FAIL":
        add("trade-chain-fail", "BLOCKER", "trade_chain", "Trade chain accounting does not reconcile or shows an orphan/duplicate order")

    if repo_health.get("deployed_sha_mismatch") is True:
        add("deployed-sha-mismatch", "WARN", "source_of_truth", "Deployed release SHA does not match repo HEAD")

    coverage = repo_health.get("change_scope_test_coverage") or {}
    if coverage.get("status") == "FAIL":
        add(
            "change-scope-test-gap", "WARN", "test_coverage",
            f"Changed files touch {coverage.get('changed_categories')} without a matching test file in the same diff",
        )

    return blockers
