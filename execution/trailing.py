"""Shared trailing-stop math for the runner exit.

One source of truth used by the sim (``paper_broker`` runner mode) and — later —
the live trail driver, so sim and live agree exactly. Pure and side-effect-free.
"""
from __future__ import annotations

from typing import Tuple


def compute_trailed_stop(
    *,
    is_long: bool,
    entry: float,
    original_stop: float,
    max_favorable: float,
    activation_r: float = 1.0,
    trail_r: float = 0.5,
) -> Tuple[float, bool]:
    """Return ``(active_stop, trailing)`` for a 1-contract runner position.

    R = |entry - original_stop|. Once the favourable excursion (``max_favorable``
    vs ``entry``) reaches ``activation_r * R``, the stop trails ``trail_r * R``
    behind ``max_favorable`` — but never looser than the original stop. Before
    activation (or with non-positive R) it returns the original stop and
    ``trailing=False``.

    ``max_favorable`` is the best price seen so far (highest high for LONG, lowest
    low for SHORT). Callers should feed it from PRIOR bars only (no intra-bar
    look-ahead) when resolving an exit.
    """
    R = abs(entry - original_stop)
    if R <= 0:
        return original_stop, False

    favorable = (max_favorable - entry) if is_long else (entry - max_favorable)
    if favorable < activation_r * R:
        return original_stop, False

    offset = trail_r * R
    trailed = (max_favorable - offset) if is_long else (max_favorable + offset)
    # Never loosen past the original stop.
    active = max(trailed, original_stop) if is_long else min(trailed, original_stop)
    return active, True
