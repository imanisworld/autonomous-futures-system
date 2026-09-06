"""Extension point for a future *human-approved* promotion action.

The validation layer (``evidence`` -> ``policy`` -> ``record``) is complete
without this file. A later action must consume a ``PromotionVerdict`` that
is READY, plus an explicit human approval it did not manufacture, and must
be implemented as a separate object so the validation layer is never
rewritten to accommodate it. Only the no-op exists today.
"""

from __future__ import annotations

from typing import Protocol

from .models import READY, PromotionVerdict


class PromotionAction(Protocol):
    def perform(self, verdict: PromotionVerdict, *, human_approval: str) -> str: ...


class NoPromotionAction:
    """The only action shipped: records that nothing was done."""

    def perform(self, verdict: PromotionVerdict, *, human_approval: str = "") -> str:
        if verdict.verdict != READY:
            return f"no action: verdict is {verdict.verdict}"
        return "no action: READY FOR PROMOTION recorded; merge requires human approval"
