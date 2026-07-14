"""MNQ vwap_hold proof mode — strategy-restoration candidate #3 (2026-07-14).

Scoped narrowly: MNQ + vwap_hold + NEW YORK SESSION only. This module never
runs for any other instrument, strategy, or session — MES vwap_hold (dead:
+$0.53/trade raw, negative after cost, see docs/strategy-matrix-tranche1-
2026-07-14.md) and every other lane are untouched by anything here.

Mirrors context/mnq_orb_breakout_proof.py's architecture (same tri-state
mode, same campaign-dedupe shape, same ProofDecision contract) with ONE
addition the orb lanes never needed: a scoped strategy-permission-gate
exception. vwap_hold is SHADOW_ONLY in risk_rules.yaml's
strategy_permission_gate (demoted 2026-06-26 when its IOC-limit fills starved
and its static-exit fills lost), so unlike orb_breakout — which reached TRADE
and died at the fill — vwap_hold candidates are blocked at
STRATEGY_NOT_PAPER_ELIGIBLE in strategy/signal_engine.py before any broker
logic runs. `permission_gate_exception()` below opens that gate ONLY for
MNQ + vwap_hold + new_york + mode=="paper_sim". The global SHADOW_ONLY
demotion stays intact everywhere else, including this lane's own
observe_only mode (zero behavior change: the observable evidence is the
blocked-candidate row itself) and including tradovate_demo (deliberate
deviation from the orb lanes' mode contract: vwap_hold under IOC/static on
Tradovate demo is the exact configuration proven to lose — this lane never
re-enables it, so tradovate_demo behaves like observe_only at the permission
gate and is documented as such).

Why this candidate: docs/strategy-matrix-tranche1-2026-07-14.md — MNQ
vwap_hold under market entry + runner exit: n=341, +$12.75/trade raw
(+$10.51 after $1.24 commission + 2-tick RT slippage), PF 1.67, BOTH
walk-forward halves positive; NY-session subset +$22.72/trade, PF 2.18,
halves both positive. The paired fill study (scripts/
vwap_hold_paired_fill_comparison.py, identical sha256-fingerprinted 348-arm
population) proved the old "vwap edge is fiction" verdict decomposes into
IOC starvation (fills 105/348) + static-exit drag — an execution-model
problem, which is exactly what this proof-lane pattern (market entry +
runner exit + PaperBroker) corrects.

Modes (config.mnq_vwap_hold_proof_mode / MNQ_VWAP_HOLD_PROOF_MODE env,
default "observe_only" — "live" is never a valid value, enforced in
config/settings.py's _validate_config):

  - observe_only:   nothing changes anywhere. The permission gate stays
                    closed, vwap_hold candidates keep dying at
                    STRATEGY_NOT_PAPER_ELIGIBLE, and those blocked rows ARE
                    the observation evidence (candidate appeared, correctly
                    classified, blocked at the expected gate).
  - paper_sim:      permission_gate_exception() opens the permission gate for
                    MNQ+vwap_hold+new_york specifically; the resulting TRADE
                    proceeds normally through risk, with the broker forced to
                    PaperBroker and force_market_entry=force_runner_exit=True
                    on the BracketOrder — identical to the orb lanes'
                    paper_sim semantics.
  - tradovate_demo: accepted for contract parity with the other proof modes
                    but DOES NOT open the permission gate (see above).
                    vwap_hold stays blocked; behaves like observe_only.

Campaign dedupe: at most one proof attempt per (day, direction) — vwap_hold
fires ~0.5x/day historically (348 arms / 622 days) and re-alerts along the
same VWAP hold produce no stable level to key on (VWAP moves every bar,
unlike an ORB boundary), so the day+direction key is the deterministic,
conservative choice.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

VALID_MODES = ("observe_only", "paper_sim", "tradovate_demo")
DEFAULT_MODE = "observe_only"
PROOF_SESSION = "new_york"


def mnq_vwap_hold_proof_mode(cfg=None) -> str:
    """Validated proof mode. Defense in depth: config/settings.py's
    _validate_config already fails the whole process closed at load time on an
    invalid value, but this helper never trusts that alone — any value outside
    VALID_MODES here (including a stray "live") falls back to observe_only,
    the safest behavior, rather than propagating an invalid state further."""
    raw = getattr(cfg, "mnq_vwap_hold_proof_mode", None)
    if raw is None:
        raw = os.getenv("MNQ_VWAP_HOLD_PROOF_MODE", DEFAULT_MODE)
    raw = str(raw or DEFAULT_MODE).strip().lower()
    return raw if raw in VALID_MODES else DEFAULT_MODE


def is_mnq_vwap_hold_candidate(instrument: Optional[str], strategy: Optional[str]) -> bool:
    root = (instrument or "").upper().replace("1!", "")
    return root == "MNQ" and strategy == "vwap_hold"


def _session_in_scope(session: Optional[str]) -> bool:
    return str(session or "").strip().lower() == PROOF_SESSION


def permission_gate_exception(
    instrument: Optional[str], strategy: Optional[str], session: Optional[str], cfg=None
) -> bool:
    """True ONLY when the strategy-permission gate should let this specific
    candidate through: MNQ + vwap_hold + new_york + mode=="paper_sim".

    This is the single narrow opening in vwap_hold's SHADOW_ONLY demotion.
    It is deliberately NOT opened for tradovate_demo (IOC/static on demo is
    the proven-negative configuration this lane exists to avoid) and NOT for
    observe_only (zero-behavior-change contract). Everything outside the
    exact scope — MES, other sessions, other strategies — stays blocked
    exactly as before.
    """
    return (
        mnq_vwap_hold_proof_mode(cfg) == "paper_sim"
        and is_mnq_vwap_hold_candidate(instrument, strategy)
        and _session_in_scope(session)
    )


def _campaign_path(log_dir: str, for_date=None) -> Path:
    d = for_date or date.today()
    return Path(log_dir) / f"mnq_vwap_hold_proof_campaigns_{d.isoformat()}.json"


def _campaign_key(direction) -> str:
    return f"{direction}"


def campaign_already_attempted(log_dir: str, *, direction, for_date=None) -> bool:
    """Fail-soft: an unreadable/missing campaign file means 'not yet attempted',
    never blocks a legitimate first attempt."""
    path = _campaign_path(log_dir, for_date)
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    keys = raw.get("keys") if isinstance(raw, dict) else None
    return isinstance(keys, list) and _campaign_key(direction) in keys


def record_campaign_attempt(log_dir: str, *, direction, for_date=None) -> None:
    """Fail-soft: a persistence hiccup must never affect trading — best-effort
    only, mirrors the ORDER_IDS persistence pattern in webhook/runner.py."""
    path = _campaign_path(log_dir, for_date)
    try:
        try:
            raw = json.loads(path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            raw = {}
        keys = raw.get("keys") if isinstance(raw, dict) else None
        if not isinstance(keys, list):
            keys = []
        key = _campaign_key(direction)
        if key not in keys:
            keys.append(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"keys": keys}, separators=(",", ":")))
        tmp.replace(path)
    except OSError:
        pass


@dataclass(frozen=True)
class ProofDecision:
    """Same three-way contract as the orb lanes (see
    context/mnq_orb_reclaim_proof.py), with one extra no-op case:

    - observe_only / tradovate_demo / session-out-of-scope
                              -> suppress=False, apply_override=False. Pure
      audit; whatever decision exists proceeds unaffected. (Under this lane's
      permission-gate design a vwap_hold TRADE only exists at all when the
      gate was opened — a session-out-of-scope TRADE can only mean an operator
      re-promoted vwap_hold globally in risk_rules.yaml, in which case the
      normal path is their explicit choice and this lane stays hands-off.)
    - paper_sim, in-scope, first attempt this campaign
                              -> suppress=False, apply_override=True: market
      entry + runner exit + forced PaperBroker at the broker call.
    - paper_sim, in-scope, duplicate campaign
                              -> suppress=True: the caller must redirect the
      decision to NO_TRADE (no TRADE_INTENT, no risk, no broker). Without
      this, a duplicate would flow down the NORMAL (IOC/static/Tradovate)
      path through the opened permission gate — the one configuration this
      lane must never produce.
    """
    mode: str
    suppress: bool
    apply_override: bool
    duplicate_campaign: bool
    session_in_scope: bool
    force_market_entry: bool
    force_runner_exit: bool
    force_paper_broker: bool
    reason: str

    def to_audit_dict(self) -> dict:
        return {
            "proof_mode": self.mode,
            "suppress": self.suppress,
            "apply_override": self.apply_override,
            "duplicate_campaign": self.duplicate_campaign,
            "session_in_scope": self.session_in_scope,
            "force_market_entry": self.force_market_entry,
            "force_runner_exit": self.force_runner_exit,
            "force_paper_broker": self.force_paper_broker,
            "reason": self.reason,
        }


def _noop(mode: str, session_ok: bool, reason: str) -> ProofDecision:
    return ProofDecision(
        mode=mode,
        suppress=False,
        apply_override=False,
        duplicate_campaign=False,
        session_in_scope=session_ok,
        force_market_entry=False,
        force_runner_exit=False,
        force_paper_broker=False,
        reason=reason,
    )


def evaluate_mnq_vwap_hold_proof(
    *, cfg, log_dir: str, session, direction, for_date=None
) -> ProofDecision:
    """Pure decision only — does not itself journal, call risk, or call a
    broker. The caller (webhook/runner.py) is responsible for acting on it."""
    mode = mnq_vwap_hold_proof_mode(cfg)
    session_ok = _session_in_scope(session)
    if mode == "observe_only":
        return _noop(mode, session_ok,
                     "observe_only: recorded as an observation, existing decision unaffected")
    if mode == "tradovate_demo":
        return _noop(mode, session_ok,
                     "tradovate_demo: does not open the vwap_hold permission gate "
                     "(IOC/static on demo is the proven-negative configuration); no-op")
    if not session_ok:
        return _noop(mode, session_ok,
                     "session_out_of_scope: proof lane is new_york-only; existing decision unaffected")
    if campaign_already_attempted(log_dir, direction=direction, for_date=for_date):
        return ProofDecision(
            mode=mode,
            suppress=True,
            apply_override=False,
            duplicate_campaign=True,
            session_in_scope=session_ok,
            force_market_entry=False,
            force_runner_exit=False,
            force_paper_broker=False,
            reason="duplicate_campaign: this direction already had its one proof attempt today",
        )
    return ProofDecision(
        mode=mode,
        suppress=False,
        apply_override=True,
        duplicate_campaign=False,
        session_in_scope=session_ok,
        force_market_entry=True,
        force_runner_exit=True,
        force_paper_broker=True,
        reason="paper_sim: market-entry + runner-exit proof trade on forced PaperBroker",
    )
