"""Options entry-timing forward test: first-sight (CONTROL) vs trigger reclaim (RECLAIM).

RESEARCH / PAPER EVIDENCE ONLY. Implements
docs/prereg-options-trigger-reclaim-entry-2026-09-23.md (#949); where this module
and the prereg disagree, the prereg wins. Reads a READ-ONLY copy of the options
scanner SQLite store. Never writes to it, never touches the scanner, alerts,
risk rules, broker code or deployment.

Data lineage (all OPTIONS_PAPER_V1, ACTIVE lane):
  options_shadow_journal            one row per episode: exact contract, trigger,
                                    stop, target, entry ASK, premium stop, outcome
  options_v1_diagnostic_snapshots   per scanner cycle while the row is OPEN:
                                    underlying price + bid/ask of the SAME contract
  options_contract_marks            same cycles: volume / open interest / delta
The CONTROL arm re-derives the stored outcome from the snapshots with the
production resolution order (``alert_ranker.scanner_legacy._resolve_v1_candidate``
plus ``v1_evidence_hardening``): missing/zero bid -> stay open; bid <= premium
stop -> LOSS at the bid (AMBIGUOUS pessimistic LOSS if the target is also hit);
else underlying stop-first, then target, exit at the bid. P&L = (exit bid -
entry ask) x 100 x 1 contract, the V1 cost model ("entry_at_ask_exit_at_bid_no_commission").
"""
from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

UTC = timezone.utc
ET = ZoneInfo("America/New_York")
PREREG_DOC = "docs/prereg-options-trigger-reclaim-entry-2026-09-23.md"
PREREG_ID = "options-trigger-reclaim-entry-2026-09-23"
POLICY_ID = "OPTIONS_PAPER_V1"
# #949 squash-merge commit time (d362a3f): 2026-09-23T10:07:08-04:00.
FORWARD_START = datetime(2026, 9, 23, 14, 7, 8, tzinfo=UTC)

MIN_ELIGIBLE_PAIRS = 50
MIN_DISTINCT_DAYS = 20
MIN_RECLAIM_ENTRIES = 30

# Frozen V1 constants (alert_ranker/paper_v1.py) re-checked at the reclaim observation.
PREMIUM_STOP_MULTIPLIER = 0.75
CONTRACT_MULTIPLIER = 100
MAX_TRADE_RISK_DOLLARS = 300.0
MAX_AGGREGATE_OPEN_RISK_DOLLARS = 1000.0
MAX_SPREAD_PERCENT = 10.0
MIN_OPTION_VOLUME = 100.0
MIN_OPEN_INTEREST = 500.0
DELTA_MIN, DELTA_MAX = 0.30, 0.70
MIN_REMAINING_RR = 1.0


def _f(v) -> Optional[float]:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _ts(v) -> Optional[datetime]:
    if not v:
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d.astimezone(UTC) if d.tzinfo else None


def connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{Path(path).resolve()}?mode=ro", uri=True)


# ─── episodes ────────────────────────────────────────────────────────────────

@dataclass
class Snapshot:
    ts: datetime
    event: str
    price: Optional[float]
    bid: Optional[float]
    ask: Optional[float]
    quote_ts: Optional[str]
    error: str
    volume: Optional[float] = None
    open_interest: Optional[float] = None
    delta: Optional[float] = None


@dataclass
class Episode:
    shadow_id: int
    opened_at: datetime
    ticker: str
    direction: str
    contract: str
    expiry: Optional[str]
    trigger: Optional[float]
    stop: Optional[float]
    target: Optional[float]
    first_price: Optional[float]
    entry_ask: Optional[float]
    entry_bid: Optional[float]
    premium_stop: Optional[float]
    geometry: Optional[str]
    stored_status: str
    stored_outcome: dict
    snapshots: list = field(default_factory=list)


def _lane(selected: dict, inputs: dict) -> str:
    # Same rule as alert_ranker.v1_diagnostics._lane: a missing lane is ACTIVE.
    return str(selected.get("paper_evidence_lane") or inputs.get("paper_evidence_lane") or "ACTIVE").upper()


def load_episodes(conn: sqlite3.Connection) -> list[Episode]:
    rows = conn.execute(
        "select id,timestamp,ticker,direction,status,setup_inputs_json,selected_contract_json,outcome_json "
        "from options_shadow_journal order by id"
    ).fetchall()
    out = []
    for sid, ts, ticker, direction, status, si, sc, oc in rows:
        si, sc, oc = json.loads(si or "{}"), json.loads(sc or "{}"), json.loads(oc or "{}")
        if sc.get("paper_policy_id") != POLICY_ID or _lane(sc, si) != "ACTIVE":
            continue
        ep = Episode(
            shadow_id=sid, opened_at=_ts(ts), ticker=ticker, direction=str(direction or "").upper(),
            contract=str(sc.get("contract") or ""), expiry=(str(sc.get("expiry") or "")[:10] or None),
            trigger=_f(si.get("setup_entry_trigger")),
            stop=_f(sc.get("stop") if sc.get("stop") is not None else (si.get("underlying_invalidation") or si.get("stop"))),
            target=_f(sc.get("target") if sc.get("target") is not None else (si.get("target_1") or si.get("target"))),
            first_price=_f(si.get("price")),
            entry_ask=_f(sc.get("entry_quote") or sc.get("option_mark") or sc.get("option_ask")),
            entry_bid=_f(sc.get("option_bid")), premium_stop=_f(sc.get("premium_stop")),
            geometry=sc.get("paper_entry_geometry") or si.get("paper_entry_geometry"),
            stored_status=str(status), stored_outcome=oc,
        )
        marks = {}
        for mts, vol, oi, dl in conn.execute(
            "select timestamp,volume,open_interest,delta from options_contract_marks where shadow_id=?", (sid,)
        ):
            marks[mts] = (_f(vol), _f(oi), _f(dl))
        for sts, ev, px, bid, ask, qts, err in conn.execute(
            "select timestamp,event,underlying_price,option_bid,option_ask,quote_timestamp,error "
            "from options_v1_diagnostic_snapshots where shadow_id=? order by timestamp, id", (sid,)
        ):
            vol, oi, dl = marks.get(sts, (None, None, None))
            ep.snapshots.append(Snapshot(_ts(sts), str(ev), _f(px), _f(bid), _f(ask), qts, str(err or ""),
                                         vol, oi, dl))
        out.append(ep)
    return out


# ─── resolution (production order) ───────────────────────────────────────────

def _level_hit(direction: str, price: Optional[float], stop: float, target: float) -> Optional[str]:
    if price is None:
        return None
    if direction == "LONG":
        if price <= stop:
            return "stop_hit"
        if price >= target:
            return "target_hit"
    elif direction == "SHORT":
        if price >= stop:
            return "stop_hit"
        if price <= target:
            return "target_hit"
    return None


def _target_hit(direction: str, target: float, price: Optional[float]) -> bool:
    if price is None:
        return False
    return price >= target if direction == "LONG" else price <= target


def resolve(direction: str, stop: float, target: float, entry_ask: float, premium_stop: float,
            path: list[Snapshot]) -> Optional[dict]:
    """First resolution along ``path`` (snapshots strictly after entry); None = still open."""
    for s in path:
        if s.error:
            continue  # chain/contract error cycles: production resolves only on price with no bid -> stays open
        if s.bid is None or s.bid <= 0:
            continue
        if s.bid <= premium_stop:
            ambiguous = _target_hit(direction, target, s.price)
            return {"status": "LOSS", "closed_reason": "ambiguous_same_snapshot_pessimistic_loss" if ambiguous
                    else "premium_stop_hit", "exit": s.bid, "at": s.ts,
                    "pnl": round((s.bid - entry_ask) * CONTRACT_MULTIPLIER, 2)}
        hit = _level_hit(direction, s.price, stop, target)
        if hit:
            return {"status": "LOSS" if hit == "stop_hit" else "WIN", "closed_reason": hit, "exit": s.bid,
                    "at": s.ts, "pnl": round((s.bid - entry_ask) * CONTRACT_MULTIPLIER, 2)}
    return None


def _after_entry(ep: Episode) -> tuple[Optional[Snapshot], list[Snapshot]]:
    entry = next((s for s in ep.snapshots if s.event == "ENTRY"), None)
    if entry is None:
        return None, []
    return entry, [s for s in ep.snapshots if s.ts > entry.ts and s.event != "ENTRY"]


def control_arm(ep: Episode) -> dict:
    entry, path = _after_entry(ep)
    if entry is None or ep.entry_ask is None or ep.premium_stop is None or None in (ep.stop, ep.target):
        return {"state": "BLOCKED", "reason": "missing_entry_lineage"}
    r = resolve(ep.direction, ep.stop, ep.target, ep.entry_ask, ep.premium_stop, path)
    if r is None:
        return {"state": "OPEN", "entry_at": entry.ts, "entry": ep.entry_ask}
    return {"state": "RESOLVED", "entry_at": entry.ts, "entry": ep.entry_ask, **r}


# ─── eligibility + RECLAIM ───────────────────────────────────────────────────

def failed_side_at_first_sight(ep: Episode) -> bool:
    if ep.first_price is None or ep.trigger is None:
        return False
    return ep.first_price < ep.trigger if ep.direction == "LONG" else (
        ep.first_price > ep.trigger if ep.direction == "SHORT" else False)


def reclaimed(direction: str, price: Optional[float], trigger: float) -> bool:
    if price is None:
        return False
    return price >= trigger if direction == "LONG" else price <= trigger


def eligibility(ep: Episode) -> tuple[bool, str]:
    if ep.opened_at is None or ep.opened_at < FORWARD_START:
        return False, "before_forward_start"
    if not ep.contract:
        return False, "no_exact_contract"
    if None in (ep.trigger, ep.stop, ep.target):
        return False, "missing_trigger_stop_target"
    if ep.geometry and ep.geometry != "AHEAD":
        return False, f"entry_geometry_{ep.geometry}"
    entry, _ = _after_entry(ep)
    if entry is None or entry.ask is None or entry.bid is None or not entry.quote_ts:
        return False, "no_decision_time_quote_evidence"
    if not failed_side_at_first_sight(ep):
        return False, "first_sight_not_on_failed_side"
    return True, "eligible"


def _remaining_rr(direction, price, stop, target) -> Optional[float]:
    if None in (price, stop, target):
        return None
    reward, risk = ((target - price), (price - stop)) if direction == "LONG" else ((price - target), (stop - price))
    return None if risk <= 0 else reward / risk


def aggregate_open_risk_at(episodes: list[Episode], at: datetime, exclude_id: int) -> float:
    """Planned risk of other ACTIVE rows open at ``at`` (entry ask x 25% x 100, opened <= at < resolved)."""
    total = 0.0
    for e in episodes:
        if e.shadow_id == exclude_id or e.entry_ask is None or e.opened_at is None or e.opened_at > at:
            continue
        c = control_arm(e)
        closed_at = c.get("at") if c.get("state") == "RESOLVED" else None
        if closed_at is not None and closed_at <= at:
            continue
        total += e.entry_ask * (1 - PREMIUM_STOP_MULTIPLIER) * CONTRACT_MULTIPLIER
    return round(total, 2)


def reclaim_gates(ep: Episode, s: Snapshot, episodes: list[Episode]) -> list[str]:
    """V1 gates re-evaluable at the reclaim observation without hindsight. [] = all pass."""
    fails = []
    rr = _remaining_rr(ep.direction, s.price, ep.stop, ep.target)
    if rr is None or rr < MIN_REMAINING_RR:
        fails.append("remaining_rr_below_1")
    spread = (s.ask - s.bid) / ((s.ask + s.bid) / 2) * 100 if s.ask and s.bid else None
    if spread is None or spread > MAX_SPREAD_PERCENT:
        fails.append("spread")
    if s.volume is not None and s.volume < MIN_OPTION_VOLUME:
        fails.append("volume")
    if s.open_interest is not None and s.open_interest < MIN_OPEN_INTEREST:
        fails.append("open_interest")
    if s.delta is not None and not (DELTA_MIN <= abs(s.delta) <= DELTA_MAX):
        fails.append("delta")
    risk = s.ask * (1 - PREMIUM_STOP_MULTIPLIER) * CONTRACT_MULTIPLIER
    if risk > MAX_TRADE_RISK_DOLLARS:
        fails.append("trade_risk_cap")
    if aggregate_open_risk_at(episodes, s.ts, ep.shadow_id) + risk > MAX_AGGREGATE_OPEN_RISK_DOLLARS:
        fails.append("aggregate_risk_cap")
    return fails


def reclaim_arm(ep: Episode, episodes: list[Episode]) -> dict:
    entry, path = _after_entry(ep)
    if entry is None:
        return {"state": "BLOCKED", "reason": "missing_entry_lineage"}
    skipped = []
    for i, s in enumerate(path):
        if s.error:
            continue
        # The episode resolves on the underlying levels first (checked before reclaim on the same cycle).
        if _level_hit(ep.direction, s.price, ep.stop, ep.target):
            return {"state": "NO_ENTRY", "reason": "episode_resolved_before_reclaim", "pnl": 0.0,
                    "resolved_at": s.ts, "ineligible_reclaims_skipped": skipped}
        if not reclaimed(ep.direction, s.price, ep.trigger):
            continue
        if s.ask is None or s.bid is None or s.ask <= 0 or s.bid <= 0 or not s.quote_ts:
            return {"state": "BLOCKED", "reason": "missing_quote_at_reclaim", "at": s.ts}
        fails = reclaim_gates(ep, s, episodes)
        if fails:
            skipped.append({"at": s.ts.isoformat(), "failed": fails})
            continue
        r = resolve(ep.direction, ep.stop, ep.target, s.ask, round(s.ask * PREMIUM_STOP_MULTIPLIER, 4), path[i + 1:])
        if r is None:
            return {"state": "BLOCKED", "reason": "reclaim_open_when_contract_marks_end", "entry_at": s.ts,
                    "entry": s.ask, "seconds_to_reclaim": (s.ts - entry.ts).total_seconds()}
        return {"state": "ENTERED", "entry_at": s.ts, "entry": s.ask,
                "entry_spread_percent": round((s.ask - s.bid) / ((s.ask + s.bid) / 2) * 100, 3),
                "seconds_to_reclaim": (s.ts - entry.ts).total_seconds(),
                "ineligible_reclaims_skipped": skipped, **r}
    # Snapshots ended with the episode still unresolved on the underlying.
    return {"state": "BLOCKED", "reason": "episode_unresolved_when_contract_marks_end"}


# ─── reproduction (parity) ───────────────────────────────────────────────────

def _stored_resolved_at(outcome: dict) -> Optional[datetime]:
    """Stored ``resolved_at`` is naive ET wall time in these rows; read it as ET."""
    raw = outcome.get("resolved_at")
    if not raw:
        return None
    d = datetime.fromisoformat(str(raw))
    return (d.replace(tzinfo=ET) if d.tzinfo is None else d).astimezone(UTC)


def reproduction(episodes: list[Episode], contract_symbols: dict[int, set]) -> dict:
    """CONTROL arm vs every stored resolved V1 ACTIVE row (all epochs, historical).

    Also proves lineage: the ENTRY snapshot ask equals the stored entry ask, and every
    contract mark for the row quotes exactly the stored contract symbol."""
    rows = []
    for ep in episodes:
        if ep.stored_status not in ("WIN", "LOSS"):
            continue
        c = control_arm(ep)
        st = ep.stored_outcome
        want = {"status": ep.stored_status, "closed_reason": st.get("closed_reason"),
                "exit": _f(st.get("exit_mark")), "pnl": _f(st.get("pnl_dollars")),
                "entry": ep.entry_ask, "at": _stored_resolved_at(st)}
        got = {"status": c.get("status"), "closed_reason": c.get("closed_reason"), "exit": c.get("exit"),
               "pnl": c.get("pnl"), "entry": c.get("entry"), "at": c.get("at")}
        at_ok = (want["at"] is not None and got["at"] is not None
                 and abs((want["at"] - got["at"]).total_seconds()) <= 60)
        entry_snap, _ = _after_entry(ep)
        entry_ask_ok = (entry_snap is not None and entry_snap.ask is not None and ep.entry_ask is not None
                        and abs(entry_snap.ask - ep.entry_ask) < 1e-9)
        symbols = contract_symbols.get(ep.shadow_id, set())
        symbol_ok = symbols == {ep.contract}
        match = (entry_ask_ok and symbol_ok and c.get("state") == "RESOLVED" and want["status"] == got["status"]
                 and want["closed_reason"] == got["closed_reason"]
                 and want["exit"] is not None and got["exit"] is not None and abs(want["exit"] - got["exit"]) < 1e-9
                 and want["pnl"] is not None and got["pnl"] is not None and abs(want["pnl"] - got["pnl"]) < 0.005
                 and at_ok)
        rows.append({"shadow_id": ep.shadow_id, "match": match, "entry_ask_lineage": entry_ask_ok,
                     "contract_symbol_lineage": symbol_ok,
                     "stored": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in want.items()},
                     "rederived": {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in got.items()},
                     "contract": ep.contract})
    n_ok = sum(r["match"] for r in rows)
    return {"rows": rows, "matched": n_ok, "total": len(rows),
            "verdict": "PASS" if rows and n_ok == len(rows) else "FAIL"}


def contract_symbols_by_row(conn: sqlite3.Connection) -> dict[int, set]:
    out: dict[int, set] = defaultdict(set)
    for sid, sym in conn.execute("select shadow_id, option_symbol from options_contract_marks"):
        out[sid].add(str(sym))
    return out


# ─── blind counts + the single look ──────────────────────────────────────────

FORBIDDEN = ("pnl", "net", "profit", "pf", "drawdown", "win", "loss", "exit", "entry_price", "dollars", "return")


def assert_blind(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            low = str(k).lower()
            for frag in FORBIDDEN:
                if frag in low:
                    raise AssertionError(f"blind violation: {path}/{k}")
            assert_blind(v, f"{path}/{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            assert_blind(v, f"{path}[{i}]")


def evaluate(episodes: list[Episode]) -> list[dict]:
    out = []
    for ep in episodes:
        ok, why = eligibility(ep)
        if ok:
            out.append({"episode": ep, "control": control_arm(ep), "reclaim": reclaim_arm(ep, episodes),
                        "day": ep.opened_at.astimezone(ET).date().isoformat()})
        elif ep.opened_at and ep.opened_at >= FORWARD_START:
            out.append({"episode": ep, "ineligible": why})
    return out


def _scorable(p: dict) -> bool:
    return ("control" in p and p["control"]["state"] == "RESOLVED"
            and p["reclaim"]["state"] in ("ENTERED", "NO_ENTRY"))


def counts_report(pairs: list[dict], *, as_of: datetime, source: dict) -> dict:
    eligible = [p for p in pairs if "control" in p]
    scorable = [p for p in eligible if _scorable(p)]
    rep = {
        "prereg": PREREG_ID, "mode": "counts", "as_of": as_of.isoformat(),
        "forward_start": FORWARD_START.isoformat(), "source": source,
        "forward_active_v1_episodes": len(pairs),
        "ineligible_by_reason": dict(sorted(defaultdict(int, {}).items())),
        "eligible_episodes": len(eligible),
        "eligible_scorable_pairs": len(scorable),
        "distinct_trading_days_scorable": len({p["day"] for p in scorable}),
        "reclaim_entries_scorable": sum(1 for p in scorable if p["reclaim"]["state"] == "ENTERED"),
        "reclaim_no_entry_scorable": sum(1 for p in scorable if p["reclaim"]["state"] == "NO_ENTRY"),
        "control_still_open": sum(1 for p in eligible if p["control"]["state"] == "OPEN"),
        "blocked_by_reason": {},
        "gate": {"min_pairs": MIN_ELIGIBLE_PAIRS, "min_days": MIN_DISTINCT_DAYS, "min_reclaim_entries": MIN_RECLAIM_ENTRIES},
    }
    inel = defaultdict(int)
    for p in pairs:
        if "ineligible" in p:
            inel[p["ineligible"]] += 1
    rep["ineligible_by_reason"] = dict(sorted(inel.items()))
    blk = defaultdict(int)
    for p in eligible:
        for arm in ("control", "reclaim"):
            if p[arm]["state"] == "BLOCKED":
                blk[f"{arm}:{p[arm]['reason']}"] += 1
    rep["blocked_by_reason"] = dict(sorted(blk.items()))
    rep["status"] = ("READY_FOR_SINGLE_LOOK" if rep["eligible_scorable_pairs"] >= MIN_ELIGIBLE_PAIRS
                     and rep["distinct_trading_days_scorable"] >= MIN_DISTINCT_DAYS
                     and rep["reclaim_entries_scorable"] >= MIN_RECLAIM_ENTRIES else "COLLECTING")
    assert_blind(rep)
    return rep


class LookRefused(RuntimeError):
    pass


def _pf(v):
    gp = sum(x for x in v if x > 0); gl = -sum(x for x in v if x < 0)
    return gp / gl if gl > 0 else (math.inf if gp > 0 else 0.0)


def _dd(v):
    eq = pk = dd = 0.0
    for x in v:
        eq += x; pk = max(pk, eq); dd = max(dd, pk - eq)
    return round(dd, 2)


def look_report(pairs: list[dict], *, as_of: datetime) -> dict:
    scorable = sorted([p for p in pairs if "control" in p and _scorable(p)], key=lambda p: p["episode"].opened_at)
    days = {p["day"] for p in scorable}
    entries = [p for p in scorable if p["reclaim"]["state"] == "ENTERED"]
    if not (len(scorable) >= MIN_ELIGIBLE_PAIRS and len(days) >= MIN_DISTINCT_DAYS and len(entries) >= MIN_RECLAIM_ENTRIES):
        raise LookRefused(f"scoring gate not reached ({len(scorable)} pairs, {len(days)} days, {len(entries)} reclaim entries)")
    c = [p["control"]["pnl"] for p in scorable]
    r = [p["reclaim"].get("pnl", 0.0) if p["reclaim"]["state"] == "ENTERED" else 0.0 for p in scorable]
    diff = [b - a for a, b in zip(c, r)]
    h = len(diff) // 2
    r_entered = [p["reclaim"]["pnl"] for p in entries]

    def med(v):
        s = sorted(v); n = len(s)
        return None if not n else (s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2)

    crit = {
        "reclaim_per_episode_gt_control": sum(r) / len(r) > sum(c) / len(c),
        "reclaim_total_positive": sum(r) > 0,
        "reclaim_pf_gt_control": _pf(r_entered) > _pf([x for x in c]),
        "reclaim_drawdown_not_worse": _dd(r) <= _dd(c),
        "both_halves_diff_non_negative": sum(diff[:h]) >= 0 and sum(diff[h:]) >= 0,
    }
    verdict = "FORWARD TEST SUPPORTS RECLAIM" if all(crit.values()) else "NO EVIDENCE OF IMPROVEMENT"
    return {
        "prereg": PREREG_ID, "mode": "look", "as_of": as_of.isoformat(), "verdict": verdict, "criteria": crit,
        "n_pairs": len(scorable), "days": len(days), "reclaim_entries": len(entries),
        "reclaim_no_entry": len(scorable) - len(entries),
        "control": {"net": round(sum(c), 2), "pf": _pf(c), "max_dd": _dd(c), "per_episode_mean": sum(c) / len(c),
                    "per_episode_median": med(c)},
        "reclaim": {"net": round(sum(r), 2), "pf_entered": _pf(r_entered), "max_dd": _dd(r),
                    "per_episode_mean": sum(r) / len(r), "per_episode_median": med(r),
                    "per_entry_mean": sum(r_entered) / len(r_entered), "per_entry_median": med(r_entered)},
        "diff_halves": [round(sum(diff[:h]), 2), round(sum(diff[h:]), 2)],
    }
