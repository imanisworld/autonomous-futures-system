"""Paper-only runtime contract for the wide-stop hypothetical-ledger lane.

Implements `docs/wide-stop-hypothetical-ledger-lane-spec-2026-09-07.md`
(approved 2026-09-07, D1-D7; 4HR cap amended 2026-09-08; 3-2-2
forward ledger capped at the operator's $5,000 ceiling 2026-09-09). The lane
produces a forward IOC-real record for three day strategies the $1,500 book's
`max_stop_ticks` / `min_rr_ratio` reject 95-100% of the time.

Contract, in one place:
  - isolated hypothetical ledgers, each with its own balance, peak, daily state
    and journal root; the real book is never read or written;
  - `wide_stop_4k` remains the 4HR forward ledger;
  - `wide_stop_5k` is the current 3-2-2 forward ledger and never assumes more
    than the operator's $5,000 capital ceiling;
  - legacy `wide_stop_6k` is retained only for historical/shadow Miyagi evidence
    and is not fill-eligible;
  - the global `RiskEngine` and `risk_rules.yaml` are untouched — the lane
    evaluates a *copy* of the config with exactly two gates overlaid, plus its
    own lane-scoped daily-loss and drawdown floors;
  - a candidate the global engine rejects for any reason other than those two
    gates is rejected by the lane too;
  - one contract, always; production `ioc_limit` at eight ticks (D4); the
    strategy's documented static bracket; no runner, no breakeven, no time exit;
  - **no promotion path.** Nothing here makes any member eligible on the real
    book.

This module has no demo or live mode. Any invalid configuration falls back to
observe_only here, while `config.settings` rejects it at process startup.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

VALID_MODES = ("observe_only", "paper_sim")
DEFAULT_MODE = "observe_only"

#: Every row this lane emits carries this label. Nothing may aggregate lane
#: P&L into the real book's P&L.
LABEL = "hypothetical_ledger"

CONTRACTS = 1
#: D4 — matches the inverse ORB lane's frozen contract. The tolerance bounds
#: only the adverse side; see docs/ioc-limit-bracket-guard-readacross-2026-09-08.md
#: for why that is not what governs bracket validity at fill.
MARKETABLE_TICKS = 8.0
#: Spec §3: $1.24 round turn plus the frozen slippage, at the metrics layer.
COMMISSION_ROUND_TRIP = 1.24
JOURNAL_ROOT = "hypothetical_ledger"

#: The only two global rejections the lane is permitted to overturn (spec §3).
LANE_OVERRIDABLE_REJECTIONS = frozenset({"stop_too_wide", "rr_below_minimum"})


@dataclass(frozen=True)
class Ledger:
    """One hypothetical ledger. Balances here are *not* real equity."""

    name: str
    starting_balance: float
    max_stop_ticks: float
    min_rr_ratio: float
    daily_loss_limit: float
    max_drawdown_percent: float
    fill_eligible: tuple[str, ...]
    shadow_only: tuple[str, ...]

    @property
    def members(self) -> tuple[str, ...]:
        return self.fill_eligible + self.shadow_only

    @property
    def worst_case_stop_dollars(self) -> float:
        """Per contract, at the family cap. MNQ: 0.25 tick = $0.50."""
        return self.max_stop_ticks * 0.50 * CONTRACTS


LEDGERS: dict[str, Ledger] = {
    "wide_stop_4k": Ledger(
        name="wide_stop_4k",
        starting_balance=4_000.0,
        max_stop_ticks=300.0,
        min_rr_ratio=1.0,
        daily_loss_limit=300.0,        # 2 x worst case ($150), amended 2026-09-08
        max_drawdown_percent=0.20,
        fill_eligible=("strat_4hr_retrigger",),
        shadow_only=(),
    ),
    "wide_stop_5k": Ledger(
        name="wide_stop_5k",
        starting_balance=5_000.0,
        max_stop_ticks=600.0,
        min_rr_ratio=0.0,              # disabled; 3-2-2 median R:R is 0.24
        daily_loss_limit=600.0,        # 2 x worst case ($300)
        max_drawdown_percent=0.20,
        fill_eligible=("strat_322_first_live",),
        shadow_only=(),
    ),
    "wide_stop_6k": Ledger(
        name="wide_stop_6k",
        starting_balance=6_000.0,
        max_stop_ticks=600.0,
        min_rr_ratio=0.0,
        daily_loss_limit=600.0,
        max_drawdown_percent=0.20,
        fill_eligible=(),
        # Historical D5 contract retained for continuity only. Miyagi remains
        # a research detector and cannot fill this or any other lane.
        shadow_only=("strat_12hr_miyagi",),
    ),
}

INSTRUMENT = "MNQ"


# ─────────────────────────────── configuration ──────────────────────────────


def mode(cfg=None) -> str:
    raw = getattr(cfg, "wide_stop_ledger_mode", None)
    if raw is None:
        raw = os.getenv("WIDE_STOP_LEDGER_MODE", DEFAULT_MODE)
    value = str(raw or DEFAULT_MODE).strip().lower()
    return value if value in VALID_MODES else DEFAULT_MODE


def epoch_start(cfg=None) -> Optional[str]:
    raw = getattr(cfg, "wide_stop_ledger_epoch_start", None)
    if raw is None:
        raw = os.getenv("WIDE_STOP_LEDGER_EPOCH_START") or None
    return str(raw) if raw else None


# ─────────────────────────────── membership ─────────────────────────────────


def _root(instrument: Optional[str]) -> str:
    return (instrument or "").upper().replace("1!", "")


def ledger_for(instrument: Optional[str], strategy: Optional[str]) -> Optional[Ledger]:
    """The ledger owning this candidate, or None if it is not a member."""
    if _root(instrument) != INSTRUMENT:
        return None
    for ledger in LEDGERS.values():
        if strategy in ledger.members:
            return ledger
    return None


def role(instrument: Optional[str], strategy: Optional[str]) -> Optional[str]:
    """'fill_eligible', 'shadow_only', or None for a non-member."""
    ledger = ledger_for(instrument, strategy)
    if ledger is None:
        return None
    return "fill_eligible" if strategy in ledger.fill_eligible else "shadow_only"


def is_fill_eligible(instrument: Optional[str], strategy: Optional[str]) -> bool:
    return role(instrument, strategy) == "fill_eligible"


# ─────────────────────────── lane-scoped risk config ────────────────────────


def lane_config(cfg, ledger: Ledger):
    """A *copy* of the config with the family overrides applied.

    Only the four lane-scoped controls in spec §4 differ. Every other gate —
    session, confluence, trend, open-position, consecutive-loss — evaluates
    exactly as it does for the real book, which is what "all existing gates
    unchanged" means. The caller must pass this to a separate `RiskEngine`;
    the global engine and `risk_rules.yaml` are never mutated.
    """
    stop_caps = dict(getattr(cfg, "max_stop_ticks", {}) or {})
    stop_caps[INSTRUMENT] = ledger.max_stop_ticks
    lane_cfg = copy.copy(cfg)
    setattr(lane_cfg, "max_stop_ticks", stop_caps)
    setattr(lane_cfg, "min_rr_ratio", ledger.min_rr_ratio)
    setattr(lane_cfg, "max_daily_loss", ledger.daily_loss_limit)
    setattr(lane_cfg, "max_drawdown_percent", ledger.max_drawdown_percent)
    # Static bracket only: runner_mode would additionally skip the R:R gate
    # entirely, which is not the override this lane is authorized to make.
    setattr(lane_cfg, "runner_mode", False)
    return lane_cfg


def global_rejection_is_overridable(failed_rules) -> bool:
    """True only when every global rejection is one the lane may overturn.

    Spec §3: the lane overrides `max_stop_ticks` and `min_rr_ratio` and nothing
    else. A candidate the global engine rejected for a trend, session or
    confluence reason stays rejected here.
    """
    if failed_rules is None:
        return False
    if isinstance(failed_rules, str):
        failed_rules = [failed_rules]
    rules = {str(rule) for rule in failed_rules if rule}
    if not rules:
        return False
    return rules <= LANE_OVERRIDABLE_REJECTIONS


# ────────────────────────────────── journal ─────────────────────────────────


def journal_dir(log_dir, ledger: Ledger) -> Path:
    """`logs/hypothetical_ledger/<ledger>` — never the real book's root."""
    return Path(log_dir) / JOURNAL_ROOT / ledger.name


# ────────────────────────────────── decision ────────────────────────────────


@dataclass(frozen=True)
class LedgerDecision:
    mode: str
    epoch_start: Optional[str]
    active: bool
    contracts: int
    marketable_ticks: float
    reason: str

    def audit(
        self,
        *,
        instrument: str,
        strategy: str,
        stop_ticks: Optional[float] = None,
        rr_ratio: Optional[float] = None,
    ) -> dict:
        """The journal block for one candidate. Always carries the label."""
        ledger = ledger_for(instrument, strategy)
        member_role = role(instrument, strategy)
        admissible = None
        if ledger is not None and stop_ticks is not None:
            admissible = stop_ticks <= ledger.max_stop_ticks and (
                rr_ratio is None or rr_ratio >= ledger.min_rr_ratio
            )
        return {
            "candidate": "wide_stop_hypothetical_ledger_v1",
            "label": LABEL,
            "hypothetical": True,
            "promotion_path": False,
            "paper_mode": self.mode,
            "accounting_epoch_start": self.epoch_start,
            "active": self.active,
            "ledger": ledger.name if ledger else None,
            "ledger_starting_balance": ledger.starting_balance if ledger else None,
            "role": member_role,
            "family_stop_cap_ticks": ledger.max_stop_ticks if ledger else None,
            "family_min_rr_ratio": ledger.min_rr_ratio if ledger else None,
            "contracts": self.contracts,
            "marketable_ticks": self.marketable_ticks,
            "dynamic_sizing_diagnostic_only": True,
            "admissible_under_family_caps": admissible,
            "stop_ticks": stop_ticks,
            "rr_ratio": rr_ratio,
            "reason": self.reason,
        }


def evaluate(cfg=None) -> LedgerDecision:
    selected = mode(cfg)
    active = selected == "paper_sim"
    return LedgerDecision(
        mode=selected,
        epoch_start=epoch_start(cfg),
        active=active,
        contracts=CONTRACTS,
        marketable_ticks=MARKETABLE_TICKS,
        reason=(
            "paper_sim: isolated hypothetical $4k/$5k forward ledgers; legacy "
            "$6k shadow ledger; family stop caps 300/600 ticks, one contract, "
            "eight-tick marketable IOC, static bracket, no promotion path"
            if active
            else "observe_only: no lane ledger, no lane fills, real book unchanged"
        ),
    )
