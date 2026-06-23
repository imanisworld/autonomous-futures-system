"""Signa-driven companion options paper lane.

When a futures trade is fully approved and opened, this lane derives an INTERNAL
paper options trade (long-premium call/put on the matching ETF) using Signa as a
stricter confirmation filter, and tracks its outcome in a SEPARATE paper ledger
(``logs/options_companion.sqlite``).

V1 places NO live or broker-paper options orders. "Paper" means an internal SQLite
ledger marked to live option quotes — nothing is ever submitted to a broker. The
``ChainProvider`` only consumes read-only quotes/chains; order/account endpoints are
forbidden by the provider's path guard.

The lane is fully isolated from the futures execution path and from the advisory
scanner (``alert_ranker``). It never mutates futures state, daily counts, or the
futures journal. Everything is gated on ``config.options_companion_enabled`` (default
False) so a disabled lane changes nothing.
"""

from .chain_provider import (
    ChainContract,
    ChainProvider,
    ChainSnapshot,
    OptionQuote,
    PublicChainProvider,
)
from .evaluator import CompanionConfig, evaluate_companion, run_companion_create
from .mapping import map_companion_candidates
from .notify import (
    notify_companion_create,
    notify_companion_error,
    notify_companion_resolved,
)
from .resolver import resolve_open_companions, run_companion_resolve
from .signa_gate import CompanionSignaResult, evaluate_companion_signa
from .selection import CompanionSelection, SelectionRejected, select_contract
from .store import CompanionRow, OptionsCompanionStore
from .status import companion_summary

__all__ = [
    "ChainContract",
    "ChainProvider",
    "ChainSnapshot",
    "OptionQuote",
    "PublicChainProvider",
    "CompanionConfig",
    "evaluate_companion",
    "run_companion_create",
    "map_companion_candidates",
    "notify_companion_create",
    "notify_companion_error",
    "notify_companion_resolved",
    "resolve_open_companions",
    "run_companion_resolve",
    "CompanionSignaResult",
    "evaluate_companion_signa",
    "CompanionSelection",
    "SelectionRejected",
    "select_contract",
    "CompanionRow",
    "OptionsCompanionStore",
    "companion_summary",
]
