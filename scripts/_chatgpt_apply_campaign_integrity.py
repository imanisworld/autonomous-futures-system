from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one match, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# ---- forward campaign report: config-defined populations + duplicate integrity ----
replace_once(
    "ops/forward_campaign_report.py",
    'PAIR_VARIANTS = ("control", "modified")\n\n\n',
    '''PAIR_VARIANTS = ("control", "modified")
_CAMPAIGN_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "forward_evidence_campaign.json"


def _configured_population_keys() -> tuple[tuple[str, str], ...]:
    config = json.loads(_CAMPAIGN_CONFIG_PATH.read_text(encoding="utf-8"))
    if config.get("campaign_id") != CAMPAIGN_ID:
        raise RuntimeError(
            f"campaign config id {config.get('campaign_id')!r} does not match {CAMPAIGN_ID!r}"
        )
    populations = config.get("populations") or []
    return tuple((str(row["strategy"]), str(row["variant"])) for row in populations)


CONFIGURED_POPULATIONS = _configured_population_keys()


def _dedupe_rows(
    rows: list[dict[str, Any]],
) -> tuple[dict[str, dict[str, Any]], int, list[str]]:
    """First-wins dedupe that distinguishes identical retries from conflicts."""
    unique: dict[str, dict[str, Any]] = {}
    identical_duplicates = 0
    conflicting_ids: set[str] = set()
    for row in rows:
        candidate_id = str(row.get("candidate_id"))
        prior = unique.get(candidate_id)
        if prior is None:
            unique[candidate_id] = row
            continue
        if row == prior:
            identical_duplicates += 1
        else:
            conflicting_ids.add(candidate_id)
    return unique, identical_duplicates, sorted(conflicting_ids)


''',
)

old_build = '''def build_report(path: str | Path) -> dict[str, Any]:
    rows = _rows(Path(path))
    candidate_rows = [row for row in rows if row.get("record_type") == "CANDIDATE"]
    outcome_rows = [row for row in rows if row.get("record_type") == "OUTCOME"]
    candidates = {str(row.get("candidate_id")): row for row in candidate_rows}
    outcomes = {str(row.get("candidate_id")): row for row in outcome_rows}
    keys = sorted({(row.get("strategy"), row.get("variant")) for row in candidates.values()})
    populations = [
        _all_arm_population(
            strategy,
            variant,
            [row for row in candidates.values() if (row.get("strategy"), row.get("variant")) == (strategy, variant)],
            outcomes,
        )
        for strategy, variant in keys
    ]
    pair_strategies = sorted({
        str(row.get("strategy")) for row in candidates.values()
        if row.get("variant") in PAIR_VARIANTS
    })
    timestamps = [str(row.get("observed_at")) for row in rows if row.get("observed_at")]
    return {
        "campaign_id": CAMPAIGN_ID,
        "source_path": str(path),
        "campaign_start_timestamp": min(timestamps) if timestamps else None,
        "campaign_end_timestamp": max(timestamps) if timestamps else None,
        "raw_candidate_rows": len(candidate_rows),
        "raw_outcome_rows": len(outcome_rows),
        "candidate_rows": len(candidates),
        "outcome_rows": len(outcomes),
        "duplicate_candidate_rows_ignored": len(candidate_rows) - len(candidates),
        "duplicate_outcome_rows_ignored": len(outcome_rows) - len(outcomes),
        "populations": populations,
        "matched_pairs": [
            _matched_pair_report(
                strategy,
                [row for row in candidates.values() if row.get("strategy") == strategy],
                outcomes,
            )
            for strategy in pair_strategies
        ],
        "review_gate": {
            "minimum_trading_days": 20,
            "minimum_resolved_filled_outcomes_per_variant": 30,
            "automatic_promotion": False,
        },
    }
'''

new_build = '''def build_report(path: str | Path) -> dict[str, Any]:
    rows = _rows(Path(path))
    candidate_rows = [row for row in rows if row.get("record_type") == "CANDIDATE"]
    outcome_rows = [row for row in rows if row.get("record_type") == "OUTCOME"]
    candidates, identical_candidate_duplicates, conflicting_candidate_ids = _dedupe_rows(candidate_rows)
    outcomes, identical_outcome_duplicates, conflicting_outcome_ids = _dedupe_rows(outcome_rows)

    configured_keys = list(CONFIGURED_POPULATIONS)
    configured_set = set(configured_keys)
    observed_keys = {
        (str(row.get("strategy")), str(row.get("variant")))
        for row in candidates.values()
    }
    unexpected_keys = sorted(observed_keys - configured_set)

    populations = [
        _all_arm_population(
            strategy,
            variant,
            [
                row for row in candidates.values()
                if (str(row.get("strategy")), str(row.get("variant"))) == (strategy, variant)
            ],
            outcomes,
        )
        for strategy, variant in configured_keys
    ]
    unexpected_populations = [
        _all_arm_population(
            strategy,
            variant,
            [
                row for row in candidates.values()
                if (str(row.get("strategy")), str(row.get("variant"))) == (strategy, variant)
            ],
            outcomes,
        )
        for strategy, variant in unexpected_keys
    ]

    evidence_integrity_ok = not conflicting_candidate_ids and not conflicting_outcome_ids
    if not evidence_integrity_ok:
        for population in populations + unexpected_populations:
            population["review_eligible"] = False
            population["review_blockers"] = ["EVIDENCE_INTEGRITY_CONFLICT"]
            population["classification_if_not_eligible"] = "WAIT / PROMISING BUT UNPROVEN"

    pair_strategies = sorted({
        str(row.get("strategy")) for row in candidates.values()
        if row.get("variant") in PAIR_VARIANTS
    })
    timestamps = [str(row.get("observed_at")) for row in rows if row.get("observed_at")]
    return {
        "campaign_id": CAMPAIGN_ID,
        "source_path": str(path),
        "campaign_start_timestamp": min(timestamps) if timestamps else None,
        "campaign_end_timestamp": max(timestamps) if timestamps else None,
        "raw_candidate_rows": len(candidate_rows),
        "raw_outcome_rows": len(outcome_rows),
        "candidate_rows": len(candidates),
        "outcome_rows": len(outcomes),
        "duplicate_candidate_rows_ignored": identical_candidate_duplicates,
        "duplicate_outcome_rows_ignored": identical_outcome_duplicates,
        "conflicting_candidate_rows": len(conflicting_candidate_ids),
        "conflicting_outcome_rows": len(conflicting_outcome_ids),
        "evidence_integrity": {
            "ok": evidence_integrity_ok,
            "conflicting_candidate_ids": conflicting_candidate_ids,
            "conflicting_outcome_ids": conflicting_outcome_ids,
        },
        "configured_population_count": len(configured_keys),
        "populations": populations,
        "unexpected_populations": unexpected_populations,
        "matched_pairs": [
            _matched_pair_report(
                strategy,
                [row for row in candidates.values() if row.get("strategy") == strategy],
                outcomes,
            )
            for strategy in pair_strategies
        ],
        "review_gate": {
            "minimum_trading_days": 20,
            "minimum_resolved_filled_outcomes_per_variant": 30,
            "automatic_promotion": False,
            "evidence_integrity_ok": evidence_integrity_ok,
            "blocked_by_evidence_integrity": not evidence_integrity_ok,
        },
    }
'''
replace_once("ops/forward_campaign_report.py", old_build, new_build)


# ---- collector census: stop claiming cadence health for event-driven futures lanes ----
old_collectors = '''    # --- shadow / strategy evidence (cadence follows the lane timeframe) --
    Collector("vwap_hold_early shadow", "jsonl", "vwap_hold_early_shadow_evidence.jsonl", 60),
    Collector("mnq_strat_22 continuation", "jsonl", "mnq_strat_22_continuation_evidence.jsonl", 60),
    Collector("mes_trend_consolidation", "jsonl", "mes_trend_consolidation_break_evidence.jsonl", 60),
    Collector("mnq_strat_22 reversal", "jsonl", "mnq_strat_22_reversal_evidence.jsonl", 360),
    Collector("mnq_strat_32", "jsonl", "mnq_strat_32_evidence.jsonl", 720),
    Collector("mnq_strat_322", "jsonl", "mnq_strat_322_evidence.jsonl", 1440),
    # --- forward A/B campaign -------------------------------------------
'''
new_collectors = '''    # --- event-driven futures strategy evidence -------------------------
    # Do not assign wall-clock DEAD thresholds to candidate-driven files.
    # MNQ Strat / MES lane health is owned by ops.evidence_lane_health, which
    # separates feed health from NO_PATTERN_MATCHES. VWAP early health is
    # checked via its 5m feed/runtime prerequisites and campaign/raw evidence;
    # candidate-file silence alone is not a valid death signal.
    # --- forward A/B campaign -------------------------------------------
'''
replace_once("ops/collector_census.py", old_collectors, new_collectors)

replace_once(
    "ops/collector_census.py",
    'from typing import Any, Callable\n\n',
    '''from typing import Any, Callable

_CAMPAIGN_CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "forward_evidence_campaign.json"


def _configured_campaign_populations() -> tuple[tuple[str, str], ...]:
    try:
        config = json.loads(_CAMPAIGN_CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return ()
    return tuple(
        (str(row.get("strategy")), str(row.get("variant")))
        for row in (config.get("populations") or [])
        if isinstance(row, dict)
    )


''',
)

old_arms = '''def campaign_arms(log_dir: Path, now: datetime) -> dict[str, Any]:
    """Per-arm accrual for the forward A/B campaign.

    A campaign whose file is fresh can still have a silently stalled arm --
    which is exactly what happened to the `modified` arm after 2026-08-19 --
    so whole-file freshness is not a sufficient check here.
    """
    path = log_dir / "forward_ab_2026_08_v1.jsonl"
    if not path.exists():
        return {}
    arms: dict[str, dict[str, Any]] = {}
    try:
        with path.open() as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("record_type") != "CANDIDATE":
                    continue
                arm = str(row.get("variant") or "unknown")
                stamp = _parse_ts(row.get("signal_timestamp"))
                entry = arms.setdefault(arm, {"count": 0, "last": None})
                entry["count"] += 1
                if stamp and (entry["last"] is None or stamp > entry["last"]):
                    entry["last"] = stamp
    except OSError:
        return {}
    for entry in arms.values():
        last = entry["last"]
        entry["idle_hours"] = None if last is None else round(_age_minutes(last, now) / 60, 1)
        entry["last"] = last.isoformat() if last else None
    return arms
'''
new_arms = '''def campaign_arms(log_dir: Path, now: datetime) -> dict[str, Any]:
    """Per-population accrual, including configured populations with zero rows."""
    configured = {
        f"{strategy}/{variant}": {
            "strategy": strategy,
            "variant": variant,
            "count": 0,
            "last": None,
        }
        for strategy, variant in _configured_campaign_populations()
    }
    unexpected: dict[str, dict[str, Any]] = {}
    path = log_dir / "forward_ab_2026_08_v1.jsonl"
    if path.exists():
        try:
            with path.open() as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if row.get("record_type") != "CANDIDATE":
                        continue
                    strategy = str(row.get("strategy") or "unknown")
                    variant = str(row.get("variant") or "unknown")
                    key = f"{strategy}/{variant}"
                    target = configured if key in configured else unexpected
                    entry = target.setdefault(
                        key,
                        {"strategy": strategy, "variant": variant, "count": 0, "last": None},
                    )
                    stamp = _parse_ts(row.get("signal_timestamp"))
                    entry["count"] += 1
                    if stamp and (entry["last"] is None or stamp > entry["last"]):
                        entry["last"] = stamp
        except OSError:
            pass

    for population in (configured, unexpected):
        for entry in population.values():
            last = entry["last"]
            entry["idle_hours"] = None if last is None else round(_age_minutes(last, now) / 60, 1)
            entry["last"] = last.isoformat() if last else None
    return {"configured": configured, "unexpected": unexpected}
'''
replace_once("ops/collector_census.py", old_arms, new_arms)

old_format = '''    if census["campaign_arms"]:
        lines += ["", "forward A/B campaign arms:"]
        for arm, entry in sorted(census["campaign_arms"].items()):
            idle = "never" if entry["idle_hours"] is None else f"{entry['idle_hours']:.0f}h idle"
            lines.append(f"  {arm:<12} candidates={entry['count']:<4} {idle}")
'''
new_format = '''    campaign = census["campaign_arms"]
    configured_arms = campaign.get("configured", {})
    unexpected_arms = campaign.get("unexpected", {})
    if configured_arms:
        lines += ["", "forward A/B configured populations:"]
        for arm, entry in sorted(configured_arms.items()):
            idle = "never" if entry["idle_hours"] is None else f"{entry['idle_hours']:.0f}h idle"
            lines.append(f"  {arm:<28} candidates={entry['count']:<4} {idle}")
    if unexpected_arms:
        lines += ["", "forward A/B UNEXPECTED populations:"]
        for arm, entry in sorted(unexpected_arms.items()):
            idle = "never" if entry["idle_hours"] is None else f"{entry['idle_hours']:.0f}h idle"
            lines.append(f"  {arm:<28} candidates={entry['count']:<4} {idle}")
'''
replace_once("ops/collector_census.py", old_format, new_format)


# ---- tests ----
old_variant_assert = '''    assert report["candidate_rows"] == 2
    assert {(p["strategy"], p["variant"], p["candidates"]) for p in report["populations"]} == {
        ("vwap_hold", "control", 1), ("vwap_hold", "modified", 1),
    }
    assert all(p["review_eligible"] is False for p in report["populations"])
'''
new_variant_assert = '''    assert report["candidate_rows"] == 2
    counts = {
        (p["strategy"], p["variant"]): p["candidates"]
        for p in report["populations"]
    }
    assert report["configured_population_count"] == 5
    assert counts == {
        ("vwap_hold", "control"): 1,
        ("vwap_hold", "modified"): 1,
        ("orb_reclaim", "control"): 0,
        ("orb_reclaim", "modified"): 0,
        ("vwap_rejection", "observer"): 0,
    }
    assert report["unexpected_populations"] == []
    assert all(p["review_eligible"] is False for p in report["populations"])
'''
replace_once("tests/test_forward_evidence_campaign.py", old_variant_assert, new_variant_assert)

append_campaign = '''\n\ndef test_report_flags_conflicting_duplicate_candidate_id(tmp_path):
    first = _record()
    conflict = deepcopy(first)
    conflict["original_entry"] = first["original_entry"] + 1.0
    path = tmp_path / "candidate-conflict.jsonl"
    path.write_text("\\n".join(json.dumps(row) for row in (first, conflict)) + "\\n")
    report = build_report(path)
    assert report["duplicate_candidate_rows_ignored"] == 0
    assert report["conflicting_candidate_rows"] == 1
    assert report["evidence_integrity"]["ok"] is False
    assert report["evidence_integrity"]["conflicting_candidate_ids"] == [first["candidate_id"]]
    assert report["review_gate"]["blocked_by_evidence_integrity"] is True
    assert all(p["review_eligible"] is False for p in report["populations"])


def test_report_flags_conflicting_duplicate_outcome_id(tmp_path):
    candidate = _record()
    first = _outcome(candidate, "WIN", 10.0)
    conflict = deepcopy(first)
    conflict["terminal_state"] = "LOSS"
    conflict["gross_pnl_dollars"] = -10.0
    path = tmp_path / "outcome-conflict.jsonl"
    path.write_text("\\n".join(json.dumps(row) for row in (candidate, first, conflict)) + "\\n")
    report = build_report(path)
    assert report["duplicate_outcome_rows_ignored"] == 0
    assert report["conflicting_outcome_rows"] == 1
    assert report["evidence_integrity"]["ok"] is False
    assert report["evidence_integrity"]["conflicting_outcome_ids"] == [candidate["candidate_id"]]


def test_report_separates_unexpected_population(tmp_path):
    unexpected = _record(strategy="not_registered", variant="control")
    path = tmp_path / "unexpected.jsonl"
    path.write_text(json.dumps(unexpected) + "\\n")
    report = build_report(path)
    assert len(report["populations"]) == 5
    assert [(p["strategy"], p["variant"], p["candidates"]) for p in report["unexpected_populations"]] == [
        ("not_registered", "control", 1)
    ]
'''
p = Path("tests/test_forward_evidence_campaign.py")
p.write_text(p.read_text(encoding="utf-8") + append_campaign, encoding="utf-8")

old_census_test = '''def test_campaign_arms_report_per_arm_idle(tmp_path):
    _write_jsonl(
        tmp_path / "forward_ab_2026_08_v1.jsonl",
        [
            {"record_type": "CANDIDATE", "variant": "control",
             "signal_timestamp": "2026-08-25T12:00:00+00:00"},
            {"record_type": "CANDIDATE", "variant": "modified",
             "signal_timestamp": "2026-08-19T04:00:00+00:00"},
            {"record_type": "OUTCOME", "variant": "control",
             "signal_timestamp": "2026-08-25T12:30:00+00:00"},
        ],
    )
    arms = campaign_arms(tmp_path, NOW)
    assert arms["control"]["count"] == 1  # OUTCOME rows are not candidates
    assert arms["modified"]["count"] == 1
    # A whole-file freshness check would call this healthy; per-arm does not.
    assert arms["modified"]["idle_hours"] > arms["control"]["idle_hours"]
'''
new_census_test = '''def test_campaign_arms_report_exact_configured_populations_and_idle(tmp_path):
    _write_jsonl(
        tmp_path / "forward_ab_2026_08_v1.jsonl",
        [
            {"record_type": "CANDIDATE", "strategy": "vwap_hold", "variant": "control",
             "signal_timestamp": "2026-08-25T12:00:00+00:00"},
            {"record_type": "CANDIDATE", "strategy": "vwap_hold", "variant": "modified",
             "signal_timestamp": "2026-08-19T04:00:00+00:00"},
            {"record_type": "OUTCOME", "strategy": "vwap_hold", "variant": "control",
             "signal_timestamp": "2026-08-25T12:30:00+00:00"},
        ],
    )
    campaign = campaign_arms(tmp_path, NOW)
    arms = campaign["configured"]
    assert set(arms) == {
        "vwap_hold/control", "vwap_hold/modified",
        "orb_reclaim/control", "orb_reclaim/modified",
        "vwap_rejection/observer",
    }
    assert arms["vwap_hold/control"]["count"] == 1  # OUTCOME rows are not candidates
    assert arms["vwap_hold/modified"]["count"] == 1
    assert arms["orb_reclaim/control"]["count"] == 0
    assert arms["orb_reclaim/control"]["last"] is None
    assert arms["vwap_hold/modified"]["idle_hours"] > arms["vwap_hold/control"]["idle_hours"]
    assert campaign["unexpected"] == {}
'''
replace_once("tests/test_collector_census.py", old_census_test, new_census_test)

append_census = '''\n\ndef test_campaign_arms_separate_shared_variants_by_strategy(tmp_path):
    _write_jsonl(
        tmp_path / "forward_ab_2026_08_v1.jsonl",
        [
            {"record_type": "CANDIDATE", "strategy": "vwap_hold", "variant": "control",
             "signal_timestamp": "2026-08-25T12:00:00+00:00"},
            {"record_type": "CANDIDATE", "strategy": "orb_reclaim", "variant": "control",
             "signal_timestamp": "2026-08-25T11:00:00+00:00"},
        ],
    )
    arms = campaign_arms(tmp_path, NOW)["configured"]
    assert arms["vwap_hold/control"]["count"] == 1
    assert arms["orb_reclaim/control"]["count"] == 1


def test_campaign_arms_report_unexpected_population_separately(tmp_path):
    _write_jsonl(
        tmp_path / "forward_ab_2026_08_v1.jsonl",
        [{"record_type": "CANDIDATE", "strategy": "unexpected", "variant": "control",
          "signal_timestamp": "2026-08-25T12:00:00+00:00"}],
    )
    campaign = campaign_arms(tmp_path, NOW)
    assert "unexpected/control" not in campaign["configured"]
    assert campaign["unexpected"]["unexpected/control"]["count"] == 1


def test_event_driven_futures_files_are_not_false_dead_cadence_collectors(tmp_path):
    census = build_census(tmp_path, NOW)
    names = {row["name"] for row in census["collectors"]}
    assert names.isdisjoint({
        "vwap_hold_early shadow",
        "mnq_strat_22 continuation",
        "mes_trend_consolidation",
        "mnq_strat_22 reversal",
        "mnq_strat_32",
        "mnq_strat_322",
    })
'''
p = Path("tests/test_collector_census.py")
p.write_text(p.read_text(encoding="utf-8") + append_census, encoding="utf-8")

for raw in (
    "scripts/_chatgpt_apply_campaign_integrity.py",
    ".github/workflows/chatgpt-apply-campaign-integrity.yml",
):
    p = Path(raw)
    if p.exists():
        p.unlink()
