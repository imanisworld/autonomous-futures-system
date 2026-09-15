"""Population identity for the forward campaign; no collection activation.

The frozen v1 campaign is the only legacy contract allowed to imply MNQ and
its epoch. Explicit identities are required for every other population.
"""
from __future__ import annotations

import json
from pathlib import Path

from config.futures_contracts import contract_economics

CAMPAIGN_ID = "forward_ab_2026_08_v1"
SCHEMA_VERSION = "1.0.0"
LEGACY_EPOCH = CAMPAIGN_ID
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "forward_evidence_campaign.json"
PopulationKey = tuple[str, str, str, str]  # strategy, instrument, variant, epoch


def population_key(record: dict) -> PopulationKey:
    if record.get("campaign_id") != CAMPAIGN_ID or record.get("evidence_schema_version") != SCHEMA_VERSION:
        raise ValueError("wrong campaign/schema identity")
    instrument = record.get("instrument")
    epoch = record.get("evidence_epoch")
    # Only the original MNQ-only contract proves missing fields. An explicit
    # new epoch cannot inherit that old contract's instrument or vice versa.
    if "instrument" not in record and "evidence_epoch" not in record:
        instrument = "MNQ"
    if "evidence_epoch" not in record and instrument == "MNQ":
        epoch = LEGACY_EPOCH
    contract_economics(instrument)
    fields = (record.get("strategy"), instrument, record.get("variant"), epoch)
    if not all(isinstance(value, str) and value.strip() and value == value.strip() for value in fields):
        raise ValueError("explicit strategy/instrument/variant/evidence_epoch required")
    return fields


def configured_populations() -> tuple[PopulationKey, ...]:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    if config.get("campaign_id") != CAMPAIGN_ID:
        raise ValueError("wrong campaign config identity")
    keys = []
    for row in config["populations"]:
        record = {
            "campaign_id": config["campaign_id"],
            "evidence_schema_version": config["evidence_schema_version"],
            "instrument": row.get("instrument", config.get("instrument")),
            **row,
        }
        if "evidence_epoch" in config and "evidence_epoch" not in row:
            record["evidence_epoch"] = config["evidence_epoch"]
        keys.append(population_key(record))
    if len(keys) != len(set(keys)):
        raise ValueError("duplicate configured evidence population")
    return tuple(keys)


def record_key(record: dict) -> tuple[PopulationKey, str]:
    candidate_id = record.get("candidate_id")
    if not isinstance(candidate_id, str) or not candidate_id:
        raise ValueError("candidate identity required")
    return population_key(record), candidate_id


def state_key(record: dict) -> str:
    population, candidate_id = record_key(record)
    if population[1] == "MNQ" and population[3] == LEGACY_EPOCH:
        return candidate_id  # keep legacy retained positions and dedupe readable
    return json.dumps([population, candidate_id], separators=(",", ":"))
