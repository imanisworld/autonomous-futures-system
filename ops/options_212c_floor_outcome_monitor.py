"""Read-only stopping readout for the unapproved 212 floor-outcome draft.

This module does not collect bars, score a path, or open an outcome file.
``study_readout`` counts activations only from hash-verified sealed session
records, the same bytes the one-look will score, then returns the fields a
person may see before that look.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from typing import Any, Mapping, Sequence

from alert_ranker.session_calendar import nyse_session_for

STUDY_SCORER_VERSION = "options_212c_floor_outcome-v0.1"
PATH_RECORD_VERSION = "options_212c_floor_outcome_path-v0.1"
TRIAL_ID = "T-2026-10-02-prereg-options-212c-floor-outcome-2026-10-02-01"
ELIGIBLE_START = "2026-10-05"
ACTIVATION_CAP = 25
SESSION_CAP = 60
FAMILY = "STRAT_212_CONTINUATION"
REDUCER_VERSION = "ep-v0.1"
ACTIVATION_GATE = "WOULD_OTHERWISE_QUALIFY"
RECOGNIZED_GATES = frozenset(
    {
        "UNSUPPORTED_FAMILY",
        "TARGET_GEOMETRY_REJECTED",
        "MARKET_ALIGNMENT_REJECTED",
        "LATE_AT_FIRST_SIGHT",
        "WOULD_OTHERWISE_QUALIFY",
    }
)
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
EPISODE_IDENTITY_KEYS = (
    "symbol",
    "session_date",
    "direction",
    "first_bar_start",
    "family",
)
SESSION_SEAL_FIELDS = (
    "path_record_version",
    "trial_id",
    "session_date",
    "session_open",
    "session_close",
    "source",
    "captured_at",
    "episodes",
)
EPISODE_SNAPSHOT_FIELDS = (
    "episode_id",
    "symbol",
    "session_date",
    "direction",
    "first_bar_start",
    "family",
    "reducer_version",
    "gate_bucket_floor",
    "entry_trigger",
    "invalidation",
    "structural_risk",
    "first_sight_at",
    "first_sight_price",
    "first_sight_after_close",
    "floor_target_1",
    "floor_target_2",
    "bars",
)
FIVE_MINUTE_GRID = (
    "The sealed bars for an episode are exactly the full 5-minute bars whose start "
    "is greater than or equal to first_sight_at, through the last bar whose end "
    "(start plus 5 minutes) is less than or equal to session_close, in time order. "
    "A bar that starts before first_sight_at is excluded. "
    "A bar whose end is after session_close is excluded. "
    "A gap inside that grid, or a bar outside it, is DATA_INVALID."
)
THRESHOLD_CROSSING_RULE = (
    "25 activations is a stop trigger evaluated after a completed eligible session, "
    "not an exact final-N requirement. The full threshold-crossing session is included, "
    "including later activations in that same session. No later session is added."
)
PATH_RECORD_INTEGRITY = (
    "One immutable JSON file per eligible NYSE session. "
    "SHA-256 of the exact file bytes is appended to manifest.jsonl in the same directory. "
    "The file freezes the episode snapshot and the 5-minute bars. "
    "A missing file, a hash mismatch, a missing snapshot field, or a missing or "
    "extra 5-minute bar on the frozen grid is DATA_INVALID for the affected episode. "
    "The one-look reads only that sealed file. "
    "Do not refetch historical bars or episode fields, and do not repair the file "
    "after any outcome is viewed."
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
READOUT_FIELDS = ("sessions_elapsed", "stop_condition_met", "stop_condition", "advance_refused")
_IDENTITY_KEY = tuple[str, str, str, str, str]


def sealed_path_record_relpath(session_date: str) -> str:
    """Return the frozen relative path of one session's sealed path record."""
    return f"{PATH_RECORD_ROOT}/{session_date}.json"


def canonical_seal_bytes(record: Mapping[str, Any]) -> bytes:
    """Return the exact bytes whose SHA-256 is the session manifest digest.

    Compact UTF-8 JSON, keys sorted at every object, with one trailing newline.
    The digest is stored in the manifest, not inside this body.
    """
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return (payload + "\n").encode("utf-8")


def seal_sha256(record: Mapping[str, Any]) -> str:
    """Return the manifest SHA-256 of one sealed session record."""
    return hashlib.sha256(canonical_seal_bytes(record)).hexdigest()


def bind_seal(record: Mapping[str, Any]) -> dict[str, Any]:
    """Attach the manifest digest to a sealed session record. Does not write a file."""
    return {"sha256": seal_sha256(record), "record": record}


def study_readout(sealed_sessions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return sessions elapsed and whether the stopping rule has fired.

    Each item is ``{"sha256", "record"}``. ``record`` is the sealed session
    file body. The digest must equal :func:`seal_sha256` of that body. Loose
    ``ep-v0.1`` rows are not an input. This function does not open a file and
    does not return snapshot, bar, direction, or activation-count fields.

    A missing or mismatched digest cannot be ordered, so the readout refuses
    with ``sessions_elapsed`` 0. Seals must be consecutive eligible NYSE
    sessions from ``ELIGIBLE_START``. A later seal with an earlier eligible
    session missing refuses at that gap: the consecutive prefix stays elapsed
    and the later seal is not entered. A verified seal whose episode is missing
    an identity field, ``reducer_version``, or a recognized ``gate_bucket_floor``
    refuses at that session the same way.
    """
    placed, unplaceable = _place_seals(sealed_sessions)
    if unplaceable:
        return _refused(0)
    included: list[str] = []
    count = 0
    expected = ELIGIBLE_START
    for session_date, keys in placed:
        if session_date != expected or keys is None:
            return _refused(len(included))
        included.append(session_date)
        count += len(keys)
        if count >= ACTIVATION_CAP or len(included) >= SESSION_CAP:
            break
        next_session = _next_eligible_session(session_date)
        if next_session is None:
            return _refused(len(included))
        expected = next_session
    return _readout(len(included), count, refused=False)


def _readout(elapsed: int, activation_count: int, *, refused: bool) -> dict[str, Any]:
    activation_cap = activation_count >= ACTIVATION_CAP
    session_cap = elapsed >= SESSION_CAP
    if refused:
        condition: str | None = None
    elif activation_cap and session_cap:
        condition = "activation_cap_and_session_cap"
    elif activation_cap:
        condition = "activation_cap"
    elif session_cap:
        condition = "session_cap"
    else:
        condition = None
    return {
        "sessions_elapsed": elapsed,
        "stop_condition_met": condition is not None,
        "stop_condition": condition,
        "advance_refused": refused,
    }


def _refused(elapsed: int) -> dict[str, Any]:
    return _readout(elapsed, 0, refused=True)


def _place_seals(
    sealed_sessions: Sequence[Mapping[str, Any]],
) -> tuple[list[tuple[str, set[_IDENTITY_KEY] | None]], bool]:
    placed: list[tuple[str, set[_IDENTITY_KEY] | None]] = []
    for item in sealed_sessions:
        placed_seal = _place_seal(item)
        if placed_seal is None:
            return [], True
        placed.append(placed_seal)
    placed.sort(key=lambda item: item[0])
    return placed, False


def _place_seal(item: Mapping[str, Any]) -> tuple[str, set[_IDENTITY_KEY] | None] | None:
    if not isinstance(item, Mapping):
        return None
    digest = item.get("sha256")
    record = item.get("record")
    if not isinstance(digest, str) or not digest or not isinstance(record, Mapping):
        return None
    try:
        expected = seal_sha256(record)
    except (TypeError, ValueError):
        return None
    if expected != digest:
        return None
    session_date = record.get("session_date")
    if not isinstance(session_date, str) or not session_date:
        return None
    if record.get("path_record_version") != PATH_RECORD_VERSION or record.get("trial_id") != TRIAL_ID:
        return (session_date, None)
    episodes = record.get("episodes")
    if not isinstance(episodes, list):
        return None
    keys: set[_IDENTITY_KEY] = set()
    for episode in episodes:
        key = _activation_key(episode, session_date)
        if key is False:
            return (session_date, None)
        if key is not None:
            keys.add(key)
    return (session_date, keys)


def _next_eligible_session(session_date: str) -> str | None:
    """Return the next NYSE session after ``session_date``, skipping closed days."""
    try:
        day = date.fromisoformat(session_date)
    except ValueError:
        return None
    for _ in range(366):
        day += timedelta(days=1)
        if nyse_session_for(day) is not None:
            return day.isoformat()
    return None


def _activation_key(episode: Mapping[str, Any], session_date: str) -> _IDENTITY_KEY | None | bool:
    """Return an activation identity, ``None`` for a non-activation, or ``False`` to refuse."""
    if not isinstance(episode, Mapping):
        return False
    if episode.get("reducer_version") != REDUCER_VERSION:
        return False
    gate = episode.get("gate_bucket_floor")
    if gate not in RECOGNIZED_GATES:
        return False
    parts: list[str] = []
    for key in EPISODE_IDENTITY_KEYS:
        value = episode.get(key)
        if not isinstance(value, str) or not value:
            return False
        parts.append(value)
    symbol, episode_session, direction, _first_bar_start, family = parts
    if symbol not in V1_UNIVERSE or direction not in {"LONG", "SHORT"} or family != FAMILY:
        return False
    if episode_session != session_date:
        return False
    episode_id = episode.get("episode_id")
    if episode_id != "|".join(parts):
        return False
    if gate != ACTIVATION_GATE:
        return None
    return (symbol, episode_session, direction, parts[3], family)
