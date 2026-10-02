"""Read-only stopping readout for the unapproved 212 floor-outcome draft.

This module does not collect bars, score a path, or open an outcome file.
``study_readout`` counts activations from episode and gate fields only, then
returns the fields a person may see before the one look.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

from alert_ranker.session_calendar import nyse_session_for

STUDY_SCORER_VERSION = "options_212c_floor_outcome-v0.1"
PATH_RECORD_VERSION = "options_212c_floor_outcome_path-v0.1"
TRIAL_ID = "T-2026-10-02-prereg-options-212c-floor-outcome-2026-10-02-01"
ELIGIBLE_START = "2026-10-05"
ACTIVATION_CAP = 25
SESSION_CAP = 60
FAMILY = "STRAT_212_CONTINUATION"
ACTIVATION_GATE = "WOULD_OTHERWISE_QUALIFY"
V1_UNIVERSE = frozenset(
    {
        "AAPL",
        "AMZN",
        "BAC",
        "COIN",
        "GE",
        "GOOGL",
        "INTC",
        "IWM",
        "JPM",
        "MRK",
        "MSFT",
        "NFLX",
        "NVDA",
        "PLTR",
        "QQQ",
        "SPY",
        "TLT",
        "TSLA",
        "WMT",
        "XOM",
    }
)
PATH_RECORD_ROOT = (
    "logs/research_sealed/"
    "T-2026-10-02-prereg-options-212c-floor-outcome-2026-10-02-01/"
    "path-records/options_212c_floor_outcome_path-v0.1"
)
PATH_RECORD_INTEGRITY = (
    "One immutable JSON file per eligible NYSE session. "
    "SHA-256 of the exact file bytes is appended to manifest.jsonl in the same directory. "
    "A missing file, a hash mismatch, or a missing 5-minute bar from first_sight_at "
    "through session close is DATA_INVALID for the affected episode. "
    "Do not refetch historical bars and do not repair the file after any outcome is viewed."
)

# Names the generic runner's compute_metrics does not emit. Their presence in
# required_metrics makes a generic one-look fail closed until a study scorer
# and its validation check exist.
STUDY_ONLY_METRICS = (
    "wins",
    "losses",
    "timeouts",
    "ambiguous_count",
    "data_invalid_count",
    "timeout_rate",
    "mean_r",
    "mae_distribution",
    "mfe_distribution",
    "concentration_by_ticker",
    "concentration_by_session_date",
    "concentration_by_clock_bucket",
    "outcome_concentration",
)
READOUT_FIELDS = ("sessions_elapsed", "stop_condition_met", "stop_condition")


def sealed_path_record_relpath(session_date: str) -> str:
    """Return the frozen relative path of one session's sealed path record."""
    return f"{PATH_RECORD_ROOT}/{session_date}.json"


def study_readout(
    completed_session_dates: Sequence[str],
    episodes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return sessions elapsed and whether the stopping rule has fired.

    ``episodes`` are ``ep-v0.1`` gate rows. Path, R, and outcome fields on
    those rows are ignored. This function does not open a file.
    """
    sessions = _eligible_sessions(completed_session_dates)
    activations = _activation_keys(episodes, sessions)
    activation_cap = len(activations) >= ACTIVATION_CAP
    session_cap = len(sessions) >= SESSION_CAP
    if activation_cap and session_cap:
        condition: str | None = "activation_cap_and_session_cap"
    elif activation_cap:
        condition = "activation_cap"
    elif session_cap:
        condition = "session_cap"
    else:
        condition = None
    return {
        "sessions_elapsed": len(sessions),
        "stop_condition_met": condition is not None,
        "stop_condition": condition,
    }


def _eligible_sessions(completed_session_dates: Sequence[str]) -> set[str]:
    sessions: set[str] = set()
    for value in completed_session_dates:
        if not isinstance(value, str):
            continue
        try:
            day = date.fromisoformat(value)
        except ValueError:
            continue
        if day.isoformat() < ELIGIBLE_START:
            continue
        if nyse_session_for(day) is None:
            continue
        sessions.add(day.isoformat())
    return sessions


def _activation_keys(
    episodes: Sequence[Mapping[str, Any]],
    sessions: set[str],
) -> set[tuple[str, str, str, str]]:
    keys: set[tuple[str, str, str, str]] = set()
    for episode in episodes:
        key = _activation_key(episode, sessions)
        if key is not None:
            keys.add(key)
    return keys


def _activation_key(
    episode: Mapping[str, Any],
    sessions: set[str],
) -> tuple[str, str, str, str] | None:
    if episode.get("gate_bucket_floor") != ACTIVATION_GATE:
        return None
    if episode.get("family") != FAMILY:
        return None
    symbol = episode.get("symbol")
    session_date = episode.get("session_date")
    first_bar_start = episode.get("first_bar_start")
    if symbol not in V1_UNIVERSE:
        return None
    if not isinstance(session_date, str) or session_date not in sessions:
        return None
    if not isinstance(first_bar_start, str) or not first_bar_start:
        return None
    return (symbol, session_date, first_bar_start, FAMILY)
