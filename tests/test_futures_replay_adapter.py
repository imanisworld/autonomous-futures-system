"""U3: futures replay adapter into the canonical Experiment Runner.

Golden proof: the frozen week fixture (data/replay/week, manifest
expected_behavior win/no_trade/loss/no_trade/win) produces canonical
trade_execution rows that match the existing ReplayEngine journal path
field-for-field, and match frozen expected values exactly.
"""
from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from context.htf_loader import HTFLookup
from ops import evidence_row as er
from ops import research_experiment_runner as runner
from ops.research_experiment_adapters import futures_replay as fr
from ops.research_experiment_adapters import register_builtin_adapters
from replay.replay_engine import ReplayEngine

ROOT = Path(__file__).resolve().parents[1]
WEEK = ROOT / "data/replay/week"
EXPERIMENT_ID = "E-2026-10-07-futures-replay-golden-01"
TRIAL_ID = "T-2026-10-07-futures-replay-golden-01"
MANIFEST_REL = "data/replay_u3/manifest.json"

ASSUMPTIONS = {
    "entry_fill_model": "market",
    "same_bar_ambiguity_rule": "stop_first",
    "stop_handling": "fixed_stop",
    "target_handling": "fixed_limit",
    "slippage_assumption": "adverse_ticks=0",
    "commission": "usd_per_contract_per_side=0.74",
    "exchange_broker_fees": "usd_per_contract_per_side=0",
    "sizing_assumptions": "replay_risk_engine",
}

PARTITIONS = {
    "development": {"start": "2026-05-01T00:00:00Z", "end": "2026-06-01T00:00:00Z"},
    "validation": {"start": "2026-06-01T00:00:00Z", "end": "2026-07-01T00:00:00Z"},
    "untouched_oos": {"start": "2026-07-01T00:00:00Z", "end": "2026-08-01T00:00:00Z"},
}

# Frozen expected canonical rows for the week fixture (baseline arm).
GOLDEN = [
    {
        "instrument": "MNQ", "signal_ts": "2026-05-18T14:30:00Z", "direction": "LONG",
        "fill_price": 19498.5, "exit_price": 19548.5, "exit_reason": "TARGET_HIT",
        "replay_result": "WIN", "contracts": 2, "gross_pnl": 200.0, "costs_fees": 2.96,
        "net_pnl": 197.04, "decision_ts": "2026-05-18T14:35:00Z",
        "fill_ts": "2026-05-18T14:35:00Z",
    },
    {
        "instrument": "MNQ", "signal_ts": "2026-05-20T14:30:00Z", "direction": "LONG",
        "fill_price": 19498.5, "exit_price": 19478.5, "exit_reason": "STOP_HIT",
        "replay_result": "LOSS", "contracts": 2, "gross_pnl": -80.0, "costs_fees": 2.96,
        "net_pnl": -82.96, "decision_ts": "2026-05-20T14:35:00Z",
        "fill_ts": "2026-05-20T14:35:00Z",
    },
    {
        "instrument": "MES", "signal_ts": "2026-05-22T14:30:00Z", "direction": "LONG",
        "fill_price": 5301.5, "exit_price": 5326.5, "exit_reason": "TARGET_HIT",
        "replay_result": "WIN", "contracts": 1, "gross_pnl": 125.0, "costs_fees": 1.48,
        "net_pnl": 123.52, "decision_ts": "2026-05-22T14:35:00Z",
        "fill_ts": "2026-05-22T14:35:00Z",
    },
]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    runner.clear_execution_adapters()
    monkeypatch.delenv("WEBULL_FUTURES_MIRROR_ENABLED", raising=False)
    yield
    runner.clear_execution_adapters()


def _seed(
    root: Path,
    *,
    partitions: dict | None = PARTITIONS,
    evaluation_partition: str | None = "development",
    assumptions: dict | None = None,
    changed: list | None = None,
    manifest_mutator=None,
) -> Path:
    data_dir = root / "data/replay_u3"
    data_dir.mkdir(parents=True)
    manifest = json.loads((WEEK / "manifest.json").read_text(encoding="utf-8"))
    for day in manifest["days"]:
        shutil.copyfile(WEEK / day["path"], data_dir / day["path"])
        day["sha256"] = _sha(data_dir / day["path"])
    if manifest_mutator is not None:
        manifest_mutator(manifest, data_dir)
    (data_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    (root / "docs/research-experiment-specs").mkdir(parents=True)
    (root / "docs/research-evidence").mkdir(parents=True)
    shutil.copyfile(
        ROOT / "docs/research-experiment-spec.schema.json",
        root / "docs/research-experiment-spec.schema.json",
    )
    (root / "docs/research-oos-consumption-ledger.jsonl").write_text("", encoding="utf-8")
    prereg = "docs/prereg-futures-replay-golden-2026-10-07.md"
    (root / prereg).write_text("# prereg\n", encoding="utf-8")
    population = "week fixture futures replay"
    (root / "docs/research-trial-ledger.jsonl").write_text(
        json.dumps(
            {
                "trial_id": TRIAL_ID,
                "event": "PLANNED",
                "recorded_at": "2026-10-07T00:00:00Z",
                "recorded_by": "test",
                "prereg_path": prereg,
                "prereg_commit": None,
                "family_id": "futures_replay_golden",
                "family_label": "futures_replay_golden",
                "population": population,
                "variant_set": {"count": 1, "manifest": prereg},
                "prior_exposed": "none",
                "attempts_in_family_before": 0,
                "pre_ledger_attempts": "UNKNOWN",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    spec = {
        "schema_version": 1,
        "experiment_id": EXPERIMENT_ID,
        "trial_id": TRIAL_ID,
        "status": "APPROVED",
        "approved_by": "Operator",
        "approved_at": "2026-10-07T00:00:00Z",
        "supersedes": None,
        "hypothesis": "U3 golden: canonical rows equal the replay journal",
        "prereg_path": prereg,
        "baseline": {"commit_sha": "c" * 40, "label": "baseline"},
        "candidate": {"commit_sha": "c" * 40, "change": "max contracts", "label": "candidate"},
        "data": {
            "source": "frozen replay week fixture",
            "window": {"start": "2026-05-18", "end": "2026-05-23"},
            "dataset_id": MANIFEST_REL,
            "dataset_hash": _sha(data_dir / "manifest.json"),
        },
        "population": population,
        "setup_type": fr.SETUP_TYPE,
        "evidence_type": "trade_execution",
        "execution_assumptions": dict(assumptions or ASSUMPTIONS),
        "timeframe": "5m",
        "changed_variables": changed
        if changed is not None
        else [{"name": "max_contracts_hard_cap", "baseline_value": 6, "candidate_value": 1}],
        "held_constant": ["population", "execution_assumptions"],
        "execution": {
            "entry_logic": "replay engine",
            "exit_logic": "replay engine",
            "stop_logic": "replay engine",
            "target_logic": "replay engine",
            "sizing": "replay risk engine",
            "friction": "frozen execution_assumptions",
        },
        "required_metrics": ["population_size", "expectancy", "win_rate"],
        "acceptance_criteria": None,
        "rejection_criteria": None,
        "evidence_path": f"docs/research-evidence/{TRIAL_ID}/",
        "variant_manifest": None,
        "notes": "U3 golden fixture",
    }
    if partitions is not None:
        spec["chronological_partitions"] = partitions
    if evaluation_partition is not None:
        spec["evaluation_partition"] = evaluation_partition
    spec_path = root / "docs/research-experiment-specs" / f"{EXPERIMENT_ID}.json"
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    runner._git(root, "init")
    runner._git(root, "config", "user.email", "t@example.com")
    runner._git(root, "config", "user.name", "t")
    runner._git(root, "add", ".")
    runner._git(root, "commit", "-m", "seed")
    head = runner._git(root, "rev-parse", "HEAD").stdout.strip()
    spec["baseline"]["commit_sha"] = head
    spec["candidate"]["commit_sha"] = head
    spec_path.write_text(json.dumps(spec, indent=2) + "\n", encoding="utf-8")
    return spec_path


@pytest.fixture
def wired(monkeypatch, config):
    """Register the adapter with the test config and the seeded repo HEAD."""

    def _wire(spec_path: Path):
        head = json.loads(spec_path.read_text())["baseline"]["commit_sha"]
        monkeypatch.setattr(fr, "CODE_SHA_PROVIDER", lambda: head)
        monkeypatch.setattr(fr, "BASE_CONFIG_PROVIDER", lambda: config)
        runner.register_execution_adapter(fr.SETUP_TYPE, fr.run_futures_replay)
        return head

    return _wire


def _ctx(spec_path: Path, arm: str = "baseline", *, partition="development"):
    spec = json.loads(spec_path.read_text())
    spec["_runner_arm"] = arm
    window = None
    if partition is not None:
        window = dict(spec["chronological_partitions"][partition])
    return runner.ExperimentContext(
        root=spec_path.parents[2],
        spec=spec,
        spec_path=spec_path,
        spec_hash="x",
        evaluation_partition=partition,
        partition_window=window,
    )


def _direct_engine_outcomes(config, tmp_path: Path) -> list[dict]:
    """The existing legitimate path: ReplayEngine.run_manifest on the frozen fixture."""
    model = fr.parse_execution_assumptions(ASSUMPTIONS)
    log_dir = tmp_path / "direct"
    cfg = fr.build_arm_config(config, model, {}, log_dir=str(log_dir))
    ReplayEngine(config=cfg, log_dir=str(log_dir), htf_lookup=HTFLookup()).run_manifest(
        WEEK / "manifest.json"
    )
    decisions, outcomes = fr._read_journal(log_dir)
    out = []
    for decision in decisions:
        outcome = outcomes[decision["paper_order_id"]]
        out.append({"decision": decision, "outcome": outcome})
    return out


# ─── Golden proof ────────────────────────────────────────────────────────────


def test_golden_adapter_rows_match_existing_replay_path_exactly(tmp_path, wired, config):
    spec_path = _seed(tmp_path / "repo")
    wired(spec_path)
    arm = fr.run_futures_replay(_ctx(spec_path))
    direct = _direct_engine_outcomes(config, tmp_path)

    assert len(arm.members) == len(direct) == len(GOLDEN) == 3
    for row, legacy, golden in zip(arm.members, direct, GOLDEN):
        setup = legacy["decision"]["setup"]
        outcome = legacy["outcome"]
        # Field-for-field against the legacy journal (no normalization).
        assert row["instrument"] == legacy["decision"]["instrument"]
        assert row["strategy_identity"] == setup["strategy"]
        assert row["direction"] == setup["direction"]
        assert row["intended_entry"] == setup["entry"]
        assert row["stop"] == setup["stop"]
        assert row["target"] == setup["target"]
        assert row["fill_price"] == outcome["entry_price"]
        assert row["exit_price"] == outcome["exit_price"]
        assert row["exit_reason"] == outcome["exit_reason"]
        assert row["replay_result"] == outcome["result"]
        assert row["contracts"] == outcome["contracts"]
        assert row["gross_pnl"] == outcome["pnl_dollars"]
        assert row["signal_ts"] == golden["signal_ts"]
        assert legacy["decision"]["bar_ts"].startswith(golden["signal_ts"][:19])
        # Frozen expected values.
        for key, value in golden.items():
            assert row[key] == value, key
        er.validate_trade_execution_row(
            row,
            expected_execution_model_id=er.execution_model_id(ASSUMPTIONS),
            expected_data_fingerprint=er.data_identity_from_spec(
                json.loads(spec_path.read_text())
            ),
        )
    # Manifest frozen expected_behavior: win / no_trade / loss / no_trade / win.
    assert [r["replay_result"] for r in arm.members] == ["WIN", "LOSS", "WIN"]
    assert arm.raw["candidates_total"] == 3
    assert arm.raw["dataset_manifest_sha256"] == json.loads(spec_path.read_text())["data"]["dataset_hash"]


def test_golden_r_multiple_mae_mfe_and_costs_are_mechanical(tmp_path, wired):
    spec_path = _seed(tmp_path / "repo")
    wired(spec_path)
    rows = fr.run_futures_replay(_ctx(spec_path)).members
    win, loss, mes = rows
    # MNQ: 20pt stop = 80 ticks * $0.50 * 2 contracts = $80 initial risk.
    assert win["r_multiple"] == pytest.approx(197.04 / 80.0)
    assert loss["r_multiple"] == pytest.approx(-82.96 / 80.0)
    # MES: 10pt stop = 40 ticks * $1.25 * 1 contract = $50 initial risk.
    assert mes["r_multiple"] == pytest.approx(123.52 / 50.0)
    for row in rows:
        assert row["mfe"] >= 0 and row["mae"] <= 0
        assert row["net_pnl"] == pytest.approx(row["gross_pnl"] - row["costs_fees"])
        assert row["exit_ts"] >= row["fill_ts"] >= row["earliest_legal_order_ts"]
    # The winner's path reached the target, so MFE covers the target distance.
    assert win["mfe"] >= win["target"] - win["fill_price"]
    # The loser's path reached the stop, so MAE covers the stop distance.
    assert loss["mae"] <= loss["stop"] - loss["fill_price"]


def test_candidate_signal_id_is_deterministic_across_runs(tmp_path, wired):
    spec_path = _seed(tmp_path / "repo")
    wired(spec_path)
    first = [r["candidate_signal_id"] for r in fr.run_futures_replay(_ctx(spec_path)).members]
    second = [r["candidate_signal_id"] for r in fr.run_futures_replay(_ctx(spec_path)).members]
    assert first == second
    assert len(set(first)) == 3


def test_full_runner_produces_valid_canonical_evidence(tmp_path, wired):
    spec_path = _seed(tmp_path / "repo")
    wired(spec_path)
    root = spec_path.parents[2]
    report = runner.execute_experiment(root, spec_path)
    assert report.status == "VALID", report.errors
    names = {c.name: c.passed for c in report.integrity_checks}
    assert names["evidence_contract"] and names["partition_membership"]
    envelope = json.loads(
        (root / f"docs/research-evidence/{TRIAL_ID}/evidence_envelope.json").read_text()
    )
    assert envelope["evidence_type"] == "trade_execution"
    assert envelope["execution_model_id"] == er.execution_model_id(ASSUMPTIONS)
    assert envelope["evaluation_partition"] == "development"
    raw = json.loads((root / f"docs/research-evidence/{TRIAL_ID}/candidate_raw.json").read_text())
    assert raw["raw"]["arm_config_overrides"] == {"max_contracts_hard_cap": 1}
    base = json.loads((root / f"docs/research-evidence/{TRIAL_ID}/baseline_raw.json").read_text())
    # changed_variables reached the engine: the 1-contract hard cap resizes MNQ.
    assert [m["contracts"] for m in base["members"]] == [2, 2, 1]
    assert [m["contracts"] for m in raw["members"]] == [1, 1, 1]
    assert [m["gross_pnl"] for m in raw["members"]] == [100.0, -40.0, 125.0]
    assert report.baseline_metrics["filled_trades"]["count"] == 3


def test_builtin_registration_includes_futures_replay():
    register_builtin_adapters()
    assert fr.SETUP_TYPE in runner.EXECUTION_ADAPTERS


# ─── U2 partition flow ──────────────────────────────────────────────────────


def test_partition_window_truncates_replay_and_filters_members(tmp_path, wired):
    parts = {
        "development": {"start": "2026-05-01T00:00:00Z", "end": "2026-05-20T00:00:00Z"},
        "validation": {"start": "2026-05-20T00:00:00Z", "end": "2026-05-21T00:00:00Z"},
        "untouched_oos": {"start": "2026-05-21T00:00:00Z", "end": "2026-06-01T00:00:00Z"},
    }
    spec_path = _seed(tmp_path / "repo", partitions=parts)
    wired(spec_path)
    arm = fr.run_futures_replay(_ctx(spec_path))
    assert [r["signal_ts"] for r in arm.members] == ["2026-05-18T14:30:00Z"]
    assert arm.raw["replay_truncated_at"] == "2026-05-20T00:00:00Z"
    replayed = {f["path"]: f["candles_replayed"] for f in arm.raw["candle_files"]}
    # No candle at/after the development end is ever fed to the engine.
    assert replayed["day_3_loss.jsonl"] == 0
    assert replayed["day_5_win.jsonl"] == 0


def test_validation_partition_counts_only_in_window_rows(tmp_path, wired):
    parts = {
        "development": {"start": "2026-05-01T00:00:00Z", "end": "2026-05-20T00:00:00Z"},
        "validation": {"start": "2026-05-20T00:00:00Z", "end": "2026-05-21T00:00:00Z"},
        "untouched_oos": {"start": "2026-05-21T00:00:00Z", "end": "2026-06-01T00:00:00Z"},
    }
    spec_path = _seed(tmp_path / "repo", partitions=parts, evaluation_partition="validation")
    wired(spec_path)
    arm = fr.run_futures_replay(_ctx(spec_path, partition="validation"))
    assert [r["signal_ts"] for r in arm.members] == ["2026-05-20T14:30:00Z"]
    assert arm.raw["candidates_outside_window"] == 1


def test_trade_unresolved_at_partition_end_fails_closed(tmp_path, wired):
    parts = {
        "development": {"start": "2026-05-01T00:00:00Z", "end": "2026-05-18T14:35:00Z"},
        "validation": {"start": "2026-05-18T14:35:00Z", "end": "2026-05-21T00:00:00Z"},
        "untouched_oos": {"start": "2026-05-21T00:00:00Z", "end": "2026-06-01T00:00:00Z"},
    }
    spec_path = _seed(tmp_path / "repo", partitions=parts)
    wired(spec_path)
    with pytest.raises(fr.FuturesReplayAdapterError, match="no OUTCOME"):
        fr.run_futures_replay(_ctx(spec_path))


def test_untouched_oos_remains_once_only_through_adapter(tmp_path, wired):
    parts = {
        "development": {"start": "2026-04-01T00:00:00Z", "end": "2026-04-15T00:00:00Z"},
        "validation": {"start": "2026-04-15T00:00:00Z", "end": "2026-05-01T00:00:00Z"},
        "untouched_oos": {"start": "2026-05-01T00:00:00Z", "end": "2026-06-01T00:00:00Z"},
    }
    spec_path = _seed(tmp_path / "repo", partitions=parts, evaluation_partition="untouched_oos")
    wired(spec_path)
    root = spec_path.parents[2]
    first = runner.execute_experiment(root, spec_path)
    assert first.status == "VALID", first.errors
    second = runner.execute_experiment(root, spec_path)
    assert second.status == "INVALID"
    assert any("oos" in e.lower() or "consumed" in e.lower() for e in second.errors)


def test_legacy_spec_without_partitions_scores_all_rows(tmp_path, wired):
    spec_path = _seed(tmp_path / "repo", partitions=None, evaluation_partition=None)
    wired(spec_path)
    arm = fr.run_futures_replay(_ctx(spec_path, partition=None))
    assert len(arm.members) == 3
    assert arm.raw["replay_truncated_at"] is None


# ─── Frozen execution assumptions ───────────────────────────────────────────


@pytest.mark.parametrize(
    "key,value,match",
    [
        ("same_bar_ambiguity_rule", "target_first", "pessimistic"),
        ("same_bar_ambiguity_rule", "pessimistic", "pessimistic"),
        ("entry_fill_model", "ioc_limit", "unsupported"),
        ("target_handling", "runner_trail", "unsupported"),
        ("stop_handling", "trailing", "unsupported"),
        ("sizing_assumptions", "1 contract", "unsupported"),
        ("commission", "$1.48 round trip", "usd_per_contract_per_side"),
        ("commission", "usd_per_contract_per_side=-1", ">= 0"),
        ("commission", "usd_per_contract_per_side=nan", "finite"),
        ("slippage_assumption", "1 tick adverse", "adverse_ticks"),
        ("exchange_broker_fees", "usd_per_contract_per_side=abc", "decimal"),
    ],
)
def test_execution_assumption_vocabulary_is_closed(key, value, match):
    bad = dict(ASSUMPTIONS, **{key: value})
    with pytest.raises(fr.FuturesReplayAdapterError, match=match):
        fr.parse_execution_assumptions(bad)


def test_missing_or_unknown_assumption_keys_fail_closed():
    missing = dict(ASSUMPTIONS)
    missing.pop("commission")
    with pytest.raises(fr.FuturesReplayAdapterError, match="missing"):
        fr.parse_execution_assumptions(missing)
    with pytest.raises(fr.FuturesReplayAdapterError, match="unsupported keys"):
        fr.parse_execution_assumptions(dict(ASSUMPTIONS, extra="x"))


def test_mutable_config_fill_defaults_cannot_leak_into_arm(config, tmp_path):
    hostile = replace(
        config,
        fill_slippage_ticks=7.0,
        fill_pessimistic_both_hit=False,
        breakeven_at_1r=True,
        runner_mode=True,
        exit_mode="runner_live",
        entry_fill_model="ioc_limit",
        entry_tolerance_ticks_by_root={"MNQ": 9},
    )
    model = fr.parse_execution_assumptions(dict(ASSUMPTIONS, slippage_assumption="adverse_ticks=1"))
    cfg = fr.build_arm_config(hostile, model, {}, log_dir=str(tmp_path))
    assert cfg.fill_slippage_ticks == 1.0
    assert cfg.fill_pessimistic_both_hit is True
    assert cfg.breakeven_at_1r is False
    assert cfg.runner_mode is False and cfg.exit_mode == "static"
    assert cfg.entry_fill_model == "market"
    assert cfg.entry_tolerance_ticks_by_root == {}
    assert cfg.live_trading_enabled is False and cfg.paper_mode is True


def test_slippage_assumption_reaches_the_engine(tmp_path, wired):
    spec_path = _seed(
        tmp_path / "repo",
        assumptions=dict(ASSUMPTIONS, slippage_assumption="adverse_ticks=1"),
    )
    wired(spec_path)
    win, loss, _ = fr.run_futures_replay(_ctx(spec_path)).members
    assert win["fill_price"] == 19498.75  # entry + 1 tick adverse
    assert loss["exit_price"] == 19478.25  # stop - 1 tick adverse
    assert win["exit_price"] == 19548.5  # target limit fills clean


# ─── changed_variables / arms ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "changed,match",
    [
        ([{"name": "fill_slippage_ticks", "baseline_value": 0, "candidate_value": 1}], "owned"),
        ([{"name": "entry_fill_model", "baseline_value": "market", "candidate_value": "stop_market"}], "owned"),
        ([{"name": "no_such_field", "baseline_value": 1, "candidate_value": 2}], "not a SystemConfig"),
        ([{"name": "max_trades_per_day", "baseline_value": "3", "candidate_value": "4"}], "type"),
        ([{"name": "max_trades_per_day", "baseline_value": 3}], "candidate_value"),
        ([], "non-empty"),
    ],
)
def test_changed_variables_are_validated(config, changed, match):
    spec = {"changed_variables": changed}
    with pytest.raises(fr.FuturesReplayAdapterError, match=match):
        fr.arm_overrides(spec, "candidate", config)


def test_arm_sha_must_equal_executing_code_sha(tmp_path, wired, monkeypatch):
    spec_path = _seed(tmp_path / "repo")
    wired(spec_path)
    monkeypatch.setattr(fr, "CODE_SHA_PROVIDER", lambda: "d" * 40)
    with pytest.raises(fr.FuturesReplayAdapterError, match="executing code SHA"):
        fr.run_futures_replay(_ctx(spec_path))


def test_executing_code_sha_refuses_dirty_checkout(tmp_path, monkeypatch):
    repo = tmp_path / "code"
    repo.mkdir()
    runner._git(repo, "init")
    runner._git(repo, "config", "user.email", "t@example.com")
    runner._git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("x = 1\n")
    runner._git(repo, "add", ".")
    runner._git(repo, "commit", "-m", "seed")
    monkeypatch.setattr(fr, "_code_root", lambda: repo)
    head = runner._git(repo, "rev-parse", "HEAD").stdout.strip()
    assert fr.executing_code_sha() == head
    (repo / "a.py").write_text("x = 2\n")
    with pytest.raises(fr.FuturesReplayAdapterError, match="tracked modifications"):
        fr.executing_code_sha()


def test_evidence_type_must_be_trade_execution(tmp_path, wired):
    spec_path = _seed(tmp_path / "repo")
    wired(spec_path)
    ctx = _ctx(spec_path)
    ctx.spec["evidence_type"] = "coverage"
    with pytest.raises(fr.FuturesReplayAdapterError, match="trade_execution"):
        fr.run_futures_replay(ctx)


def test_broker_mirror_enabled_fails_closed(tmp_path, wired, monkeypatch):
    spec_path = _seed(tmp_path / "repo")
    wired(spec_path)
    monkeypatch.setenv("WEBULL_FUTURES_MIRROR_ENABLED", "true")
    with pytest.raises(fr.FuturesReplayAdapterError, match="mirror"):
        fr.run_futures_replay(_ctx(spec_path))


# ─── Dataset binding ────────────────────────────────────────────────────────


def test_manifest_hash_mismatch_fails_closed(tmp_path, wired):
    spec_path = _seed(tmp_path / "repo")
    wired(spec_path)
    ctx = _ctx(spec_path)
    ctx.spec["data"]["dataset_hash"] = "0" * 64
    with pytest.raises(fr.FuturesReplayAdapterError, match="manifest SHA-256 mismatch"):
        fr.run_futures_replay(ctx)


def test_tampered_candle_file_fails_closed(tmp_path, wired):
    spec_path = _seed(tmp_path / "repo")
    wired(spec_path)
    day = spec_path.parents[2] / "data/replay_u3/day_3_loss.jsonl"
    day.write_text(day.read_text() + "\n")
    with pytest.raises(fr.FuturesReplayAdapterError, match="sha256 mismatch"):
        fr.run_futures_replay(_ctx(spec_path))


def test_manifest_day_without_sha256_fails_closed(tmp_path, wired):
    def drop(manifest, _data_dir):
        manifest["days"][0].pop("sha256")

    spec_path = _seed(tmp_path / "repo", manifest_mutator=drop)
    wired(spec_path)
    with pytest.raises(fr.FuturesReplayAdapterError, match="sha256"):
        fr.run_futures_replay(_ctx(spec_path))


@pytest.mark.parametrize("dataset_id", ["/etc/manifest.json", "../outside/manifest.json", ""])
def test_dataset_id_must_be_repo_relative(tmp_path, dataset_id):
    spec = {"data": {"dataset_id": dataset_id, "dataset_hash": "a" * 64}}
    with pytest.raises(fr.FuturesReplayAdapterError, match="repo-relative"):
        fr.bind_dataset(tmp_path, spec)


# ─── Journal translation ────────────────────────────────────────────────────


def _decision(order_id="PAPER-1", decision="TRADE", strategy="orb_reclaim", **extra):
    row = {
        "decision": decision,
        "instrument": "MNQ",
        "session": "new_york",
        "bar_ts": "2026-05-18T14:30:00+00:00",
        "paper_order_id": order_id,
        "setup": {
            "direction": "LONG",
            "entry": 19498.5,
            "stop": 19478.5,
            "target": 19548.5,
            "strategy": strategy,
        },
    }
    row.update(extra)
    return row


def _outcome(order_id="PAPER-1", **extra):
    row = {
        "result": "WIN",
        "entry_price": 19498.5,
        "exit_price": 19548.5,
        "exit_reason": "TARGET_HIT",
        "pnl_dollars": 200.0,
        "contracts": 2,
        "signal_timestamp": "2026-05-18T14:30:00+00:00",
        "paper_order_id": order_id,
        "execution_audit": {
            "source": "replay_historical_resolution",
            "historical_signal_bar_ts": "2026-05-18T14:30:00+00:00",
            "historical_entry_bar_ts": "2026-05-18T14:30:00+00:00",
            "historical_resolution_bar_ts": "2026-05-18T14:35:00+00:00",
        },
        "instrument": "MNQ",
    }
    row.update(extra)
    return row


@pytest.fixture
def week_index(tmp_path):
    spec_path = _seed(tmp_path / "repo")
    dataset = fr.bind_dataset(spec_path.parents[2], json.loads(spec_path.read_text()))
    return fr._CandleIndex(dataset)


def _translate(decisions, outcomes, index):
    return fr.translate_journal(
        decisions=decisions,
        outcomes=outcomes,
        candles=index,
        model=fr.parse_execution_assumptions(ASSUMPTIONS),
        execution_model_id="em-x",
        data_fingerprint="dataset_hash:x",
        arm="baseline",
    )


def test_translation_rejects_unresolved_trade(week_index):
    with pytest.raises(fr.FuturesReplayAdapterError, match="no OUTCOME"):
        _translate([_decision()], {}, week_index)


def test_translation_rejects_orphan_outcome(week_index):
    with pytest.raises(fr.FuturesReplayAdapterError, match="without a TRADE"):
        _translate([], {"PAPER-9": _outcome("PAPER-9")}, week_index)


def test_translation_rejects_pnl_that_disagrees_with_prices(week_index):
    with pytest.raises(fr.FuturesReplayAdapterError, match="price-derived"):
        _translate([_decision()], {"PAPER-1": _outcome(pnl_dollars=250.0)}, week_index)


def test_translation_rejects_unsupported_result(week_index):
    with pytest.raises(fr.FuturesReplayAdapterError, match="unsupported OUTCOME result"):
        _translate([_decision()], {"PAPER-1": _outcome(result="OPEN")}, week_index)


def test_translation_rejects_intrabar_212_122_path(week_index):
    with pytest.raises(fr.FuturesReplayAdapterError, match="2-1-2/1-2-2"):
        _translate([_decision(strategy="strat_212")], {"PAPER-1": _outcome()}, week_index)
    same_bar = _outcome(execution_audit={"source": "strat_212_122_same_bar_resolution"})
    with pytest.raises(fr.FuturesReplayAdapterError, match="same-bar"):
        _translate([_decision()], {"PAPER-1": same_bar}, week_index)


def test_translation_rejects_signal_timestamp_mismatch(week_index):
    with pytest.raises(fr.FuturesReplayAdapterError, match="signal_timestamp"):
        _translate(
            [_decision()],
            {"PAPER-1": _outcome(signal_timestamp="2026-05-18T14:35:00+00:00")},
            week_index,
        )


def test_cancelled_entry_becomes_canonical_no_fill(week_index):
    rows = _translate(
        [_decision()],
        {
            "PAPER-1": _outcome(
                result="CANCELLED", exit_price=None, exit_reason="ENTRY_NOT_TRIGGERED",
                pnl_dollars=0.0,
            )
        },
        week_index,
    )
    assert rows[0]["fill_state"] == "NO_FILL"
    assert rows[0]["no_fill_reason"] == "ENTRY_NOT_TRIGGERED"
    er.validate_trade_execution_row(rows[0])


def test_risk_rejected_becomes_no_fill_with_reject_reason(week_index):
    rows = _translate(
        [_decision(decision="RISK_REJECTED", risk_check={"failed_rule": "MAX_TRADES"})],
        {},
        week_index,
    )
    assert rows[0]["fill_state"] == "NO_FILL"
    assert rows[0]["reject_reason"] == "MAX_TRADES"
    er.validate_trade_execution_row(rows[0])
    with pytest.raises(fr.FuturesReplayAdapterError, match="RISK_REJECTED decision has an OUTCOME"):
        _translate(
            [_decision(decision="RISK_REJECTED")],
            {"PAPER-1": _outcome()},
            week_index,
        )
