"""options_manager/validation — real-setup validation fixtures.

Increment 23. Runs manually-authored RealSetupFixture entries through the
existing advisory-only row-building and scanning path, then pairs each
fixture's scan verdict with its separately-recorded real-world outcome.
Performs no I/O of any kind: no candle fetch, no option-chain fetch, no
market-data fetch, no broker call, no order placement, no execution, no
alert sending, no file access at runtime. Does not import
replay/replay_engine.py, the live context.market_context loader,
alert_ranker, options_companion, execution, webhook, broker systems, or
risk/risk_engine.py.
"""

from __future__ import annotations

from .base import (
    DataProvenance,
    RealSetupClassification,
    RealSetupFixture,
    RealSetupOutcome,
    RealSetupValidationEntry,
    RealSetupValidationSummary,
    classify_real_setup_outcome,
)
from .fixtures import (
    build_real_setup_validation_dataset,
    run_real_setup_validation_dataset,
    summarize_real_setup_validation_dataset,
)
from .fixture_status import (
    FixtureCandidate,
    FixtureCandidateSummary,
    FixtureStatus,
    PROOF_PACKET_FORWARD_CAPTURE_FIELDS,
    build_fixture_candidate_inventory,
    summarize_fixture_candidate_inventory,
)
from .management_cases import (
    DecisionBasis,
    DecisionType,
    EvidenceStatus,
    ManagementCase,
    ManagementCaseSummary,
    ManagementClassification,
    PositionSizing,
    ThesisStatus,
    build_active_management_case_dataset,
    build_management_case_dataset,
    summarize_management_case_dataset,
)
from .proof_packet import (
    ProofPacket,
    ProofPacketStatus,
    validate_proof_packet,
)
from .proof_packet_intake import (
    IntakeResult,
    check_proof_packet_intake,
)

__all__ = [
    "DataProvenance",
    "RealSetupClassification",
    "RealSetupFixture",
    "RealSetupOutcome",
    "RealSetupValidationEntry",
    "RealSetupValidationSummary",
    "classify_real_setup_outcome",
    "build_real_setup_validation_dataset",
    "run_real_setup_validation_dataset",
    "summarize_real_setup_validation_dataset",
    "DecisionBasis",
    "DecisionType",
    "EvidenceStatus",
    "ManagementCase",
    "ManagementCaseSummary",
    "ManagementClassification",
    "PositionSizing",
    "ThesisStatus",
    "build_active_management_case_dataset",
    "build_management_case_dataset",
    "summarize_management_case_dataset",
    "FixtureCandidate",
    "FixtureCandidateSummary",
    "FixtureStatus",
    "PROOF_PACKET_FORWARD_CAPTURE_FIELDS",
    "build_fixture_candidate_inventory",
    "summarize_fixture_candidate_inventory",
    "ProofPacket",
    "ProofPacketStatus",
    "validate_proof_packet",
    "IntakeResult",
    "check_proof_packet_intake",
]
