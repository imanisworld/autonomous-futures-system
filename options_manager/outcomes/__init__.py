"""Causal, append-only forward outcome events for the options advisory lane.

Every event records what was observed, from which provider, at which
source timestamp, under which system commit, for which session/thesis --
before any outcome is known. Storage lives in ``options_manager.storage``
(the existing options SQLite database); this package only defines the
event shape and its content hash. No execution, broker, network, or
clock access anywhere here.
"""

from .events import (
    EVENT_TYPES,
    SETUP_STATES,
    ForwardOutcomeEvent,
    event_content_hash,
    validate_forward_outcome_event,
)
from .reducer import OUTCOMES, UNDETERMINED, ForwardOutcomeSummary, reduce_forward_outcome

__all__ = [
    "EVENT_TYPES",
    "SETUP_STATES",
    "ForwardOutcomeEvent",
    "event_content_hash",
    "validate_forward_outcome_event",
    "OUTCOMES",
    "UNDETERMINED",
    "ForwardOutcomeSummary",
    "reduce_forward_outcome",
]
