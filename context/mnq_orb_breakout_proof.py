"""MNQ orb_breakout proof mode — restoration of the broader strategy system,
first candidate (2026-07-13/14).

Scoped narrowly: MNQ + orb_breakout only. This module never runs for any
other instrument or strategy — MES orb_breakout (proven dead at every cap
and exit mode, see docs/orb-breakout-entry-study-2026-07-11.md), orb_reclaim,
and every other strategy are all untouched by anything here.

Mirrors context/mnq_orb_reclaim_proof.py's architecture exactly (same
tri-state mode, same campaign-dedupe shape, same ProofDecision contract) so
webhook/runner.py's existing integration pattern extends with a parallel
hook rather than new machinery. The two proof lanes are independent and
never suppress or interact with each other.

Why this candidate first: docs/orb-breakout-entry-study-2026-07-11.md found
MNQ orb_breakout NO_TRADE 18/18 times on ENTRY_DETACHED_FROM_PRICE (a static
`orb.high + 2 ticks` anchor going stale before the 15-minute decision engine
evaluates it) and showed the SAME market-entry + runner-exit fix already
proven for orb_reclaim resolves it: unbounded market entry + runner exit
(1.0R activation / 0.5R trail) -> 60 resolved trades, +$1,043.75, $17.40/
trade, 58.3% WR, PF 1.77, BOTH walk-forward halves positive (+26.64 / +8.15).
Static exit (the box's non-proof default) is noise-level and fails
walk-forward (second half negative) -- market entry alone is not a fix; the
runner exit is load-bearing, exactly as this proof-mode pattern provides via
its own per-order force_runner_exit override (never dependent on the box's
global EXIT_MODE pin). The study's own recommendation: "a scoped MNQ
orb_breakout proof lane -- modeled on PR #259's MNQ orb_reclaim proof mode
... is the natural next build."

Modes (config.mnq_orb_breakout_proof_mode / MNQ_ORB_BREAKOUT_PROOF_MODE env,
default "observe_only" -- "live" is never a valid value, enforced in
config/settings.py's _validate_config):

  - observe_only:   the setup is recorded as an audit observation only. The
                     caller MUST keep the final decision non-TRADE -- no
                     TRADE_INTENT, no risk call, no broker call.
  - paper_sim:      the caller may let the setup proceed normally through
                     risk, but must force the broker to PaperBroker (regardless
                     of the box's normal paper_mode/BROKER selection) and set
                     force_market_entry=force_runner_exit=True on the
                     BracketOrder.
  - tradovate_demo: same per-order overrides as paper_sim, but the caller does
                     NOT override broker selection -- whatever broker the box
                     already resolves to (expected: Tradovate demo) is used.
                     Real-money safety is unaffected: TradovateBroker.execute_bracket
                     still independently blocks TRADOVATE_ENV=live unless
                     LIVE_TRADING_ENABLED=true, exactly as it does for every
                     other order.

Campaign dedupe: at most one proof-mode attempt per (day, orb_high, orb_low,
direction) -- a repeated orb_breakout alert riding the same ORB move does not
create a second proof trade, whether or not the first one filled.
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


def mnq_orb_breakout_proof_mode(cfg=None) -> str:
    """Validated proof mode. Defense in depth: config/settings.py's
    _validate_config already fails the whole process closed at load time on an
    invalid value, but this helper never trusts that alone -- any value outside
    VALID_MODES here (including a stray "live") falls back to observe_only,
    the safest behavior, rather than propagating an invalid state further."""
    raw = getattr(cfg, "mnq_orb_breakout_proof_mode", None)
    if raw is None:
        raw = os.getenv("MNQ_ORB_BREAKOUT_PROOF_MODE", DEFAULT_MODE)
    raw = str(raw or DEFAULT_MODE).strip().lower()
    return raw if raw in VALID_MODES else DEFAULT_MODE


def is_mnq_orb_breakout_candidate(instrument: Optional[str], strategy: Optional[str]) -> bool:
    root = (instrument or "").upper().replace("1!", "")
    return root == "MNQ" and strategy == "orb_breakout"


def _campaign_path(log_dir: str, for_date=None) -> Path:
    d = for_date or date.today()
    return Path(log_dir) / f"mnq_orb_breakout_proof_campaigns_{d.isoformat()}.json"


def _campaign_key(orb_high, orb_low, direction) -> str:
    return f"{orb_high}|{orb_low}|{direction}"


def campaign_already_attempted(
    log_dir: str, *, orb_high, orb_low, direction, for_date=None
) -> bool:
    """Fail-soft: an unreadable/missing campaign file means 'not yet attempted',
    never blocks a legitimate first attempt."""
    path = _campaign_path(log_dir, for_date)
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    keys = raw.get("keys") if isinstance(raw, dict) else None
    return isinstance(keys, list) and _campaign_key(orb_high, orb_low, direction) in keys


def record_campaign_attempt(
    log_dir: str, *, orb_high, orb_low, direction, for_date=None
) -> None:
    """Fail-soft: a persistence hiccup must never affect trading -- best-effort
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
        key = _campaign_key(orb_high, orb_low, direction)
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
    """Three-way state, deliberately NOT a simple allow/disallow boolean:

    - observe_only            -> suppress=False, apply_override=False. The
      EXISTING orb_breakout decision (TRADE_INTENT -> risk -> broker, current
      entry-type/exit-mode config) proceeds completely unaffected -- this mode
      is a pure audit/no-op by construction, never a gate on today's behavior.
    - paper_sim/tradovate_demo, first attempt this campaign
                              -> suppress=False, apply_override=True. Proceeds
      normally through TRADE_INTENT/risk, with the market-entry + runner-exit
      (+ forced PaperBroker for paper_sim) override applied at the broker call.
    - paper_sim/tradovate_demo, duplicate campaign
                              -> suppress=True, apply_override=False. The
      caller must redirect the decision to NO_TRADE (no TRADE_INTENT, no risk,
      no broker) -- this is the ONLY case that alters the existing decision,
      and it only ever triggers once an operator has explicitly opted into an
      active proof mode.
    """
    mode: str
    suppress: bool
    apply_override: bool
    duplicate_campaign: bool
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
            "force_market_entry": self.force_market_entry,
            "force_runner_exit": self.force_runner_exit,
            "force_paper_broker": self.force_paper_broker,
            "reason": self.reason,
        }


def evaluate_mnq_orb_breakout_proof(
    *, cfg, log_dir: str, orb_high, orb_low, direction, for_date=None
) -> ProofDecision:
    """Pure decision only -- does not itself journal, call risk, or call a
    broker. The caller (webhook/runner.py) is responsible for acting on it."""
    mode = mnq_orb_breakout_proof_mode(cfg)
    if mode == "observe_only":
        return ProofDecision(
            mode=mode,
            suppress=False,
            apply_override=False,
            duplicate_campaign=False,
            force_market_entry=False,
            force_runner_exit=False,
            force_paper_broker=False,
            reason="observe_only: recorded as an observation, existing decision unaffected",
        )
    if campaign_already_attempted(
        log_dir, orb_high=orb_high, orb_low=orb_low, direction=direction, for_date=for_date
    ):
        return ProofDecision(
            mode=mode,
            suppress=True,
            apply_override=False,
            duplicate_campaign=True,
            force_market_entry=False,
            force_runner_exit=False,
            force_paper_broker=False,
            reason="duplicate_campaign: this ORB boundary/direction already attempted today",
        )
    return ProofDecision(
        mode=mode,
        suppress=False,
        apply_override=True,
        duplicate_campaign=False,
        force_market_entry=True,
        force_runner_exit=True,
        force_paper_broker=(mode == "paper_sim"),
        reason=f"{mode}: market-entry + runner-exit-only proof trade",
    )
