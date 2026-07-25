"""Regression for the replay-side half of the paper_order_id identity chain.

#327 fixed the LIVE journal path (adaptive/journal_reader.py) to join
TRADE<->OUTCOME by exact paper_order_id instead of FIFO. It never touched
replay/replay_engine.py: PaperBroker already minted and threaded a
paper_order_id onto every Fill it returned, but replay_engine.py never read
it and never forwarded it into journal.log_decision()/log_outcome(). Every
replay-generated journal (verified directly against the Corpus v1 run) had
paper_order_id: null on every single TRADE and OUTCOME row -- a fail-closed
identity join against that data resolves zero trades, not because the join
is wrong, but because the identity was never written in the first place.

This fixes that by:
  - execution/paper_broker.py: execute_bracket() accepts an optional
    paper_order_id override (mirrors restore_position's existing pattern);
    omitted, behavior for every existing caller (live webhook/runner.py) is
    unchanged. _entry_not_filled() echoes back whatever id it's given so a
    same-day IOC self-cancel still carries the id symmetrically.
  - replay/replay_engine.py: mints one id per TRADE decision, before risk/
    fill outcome is known (matching the live contract: TRADE row and its
    eventual OUTCOME row share one id), and passes it into every
    restore_position()/execute_bracket() call and every log_outcome() call
    for that position. No FIFO fallback, no new id scheme.

Note on scope: PaperBroker allows only one open position at a time
(execute_bracket raises if one is already open), so "out-of-order outcome
resolution" in the #327 sense (two DIFFERENT instruments' trades resolving
out of journal order) cannot occur within a single ReplayEngine.run() call --
that class of bug is already exhaustively covered across days/instruments by
tests/test_journal_reader_identity_pairing.py and
tests/test_corpus_v1_report_identity_pairing.py, which exercise the same
JournalReader this fix now correctly feeds. What CAN happen within one
day -- sequential same-day trades getting distinct ids and each outcome
matching its own entry -- is proven at the PaperBroker level below, since
that's the exact mechanism responsible for minting and tracking the id.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from adaptive.journal_reader import JournalReader
from execution.broker_interface import BracketOrder
from execution.paper_broker import NextBarOHLC, PaperBroker
from replay import ReplayEngine

SAMPLE = "data/replay/sample_day_mnq.jsonl"


def _order(**overrides) -> BracketOrder:
    base = dict(
        instrument="MES", direction="LONG", entry=5000.0, stop=4990.0,
        target=5020.0, rr_ratio=2.0, strategy="orb_reclaim", contracts=1,
    )
    base.update(overrides)
    return BracketOrder(**base)


class TestPaperBrokerIdentityOverride:
    def test_execute_bracket_self_generates_when_not_provided(self):
        """Every existing caller (live webhook/runner.py) omits the new
        kwarg -- behavior must be exactly what it was before this change."""
        fill = PaperBroker().execute_bracket(_order())
        assert fill.paper_order_id is not None
        assert fill.paper_order_id.startswith("PAPER-")

    def test_execute_bracket_honors_a_provided_id_end_to_end(self):
        broker = PaperBroker()
        fill = broker.execute_bracket(_order(), paper_order_id="PAPER-explicit")
        assert fill.paper_order_id == "PAPER-explicit"
        outcome = broker.resolve_position(NextBarOHLC(high=5025.0, low=4995.0))
        assert outcome is not None
        assert outcome.paper_order_id == "PAPER-explicit"

    def test_sequential_same_day_trades_get_distinct_ids_and_correct_outcomes(self):
        """The core mechanic behind 'multiple same-day trades: each position
        has a different ID; each outcome matches its own entry.' PaperBroker
        only ever holds one position at a time, so this is inherently
        sequential -- exactly how replay_engine.py uses it."""
        broker = PaperBroker()

        fill1 = broker.execute_bracket(_order(direction="LONG", entry=5000.0, stop=4990.0, target=5020.0))
        outcome1 = broker.resolve_position(NextBarOHLC(high=5025.0, low=4995.0))  # target hit
        assert outcome1.result == "WIN"
        assert outcome1.paper_order_id == fill1.paper_order_id

        fill2 = broker.execute_bracket(_order(direction="SHORT", entry=5000.0, stop=5010.0, target=4980.0))
        outcome2 = broker.resolve_position(NextBarOHLC(high=5015.0, low=4975.0))  # target hit
        assert outcome2.result == "WIN"
        assert outcome2.paper_order_id == fill2.paper_order_id

        assert fill1.paper_order_id != fill2.paper_order_id
        assert outcome1.paper_order_id != outcome2.paper_order_id

    def test_ioc_self_cancel_echoes_the_provided_id(self):
        """An entry that never opens a position is still 'the same lifecycle,'
        per the operator's own contract -- not an unidentified one. Matches
        _entry_not_filled's docstring."""
        broker = PaperBroker(entry_fill_model="ioc_limit", entry_tolerance_ticks_by_root={"MES": 0.0})
        fill = broker.execute_bracket(
            _order(direction="LONG", entry=5000.0),
            market_price=5100.0,  # far beyond a zero-tolerance IOC limit -> self-cancel
            paper_order_id="PAPER-ioc-cancel",
        )
        assert fill.result == "CANCELLED"
        assert fill.paper_order_id == "PAPER-ioc-cancel"


def _journal_entries(journal_path: str) -> list[dict]:
    return [json.loads(line) for line in Path(journal_path).read_text().splitlines() if line.strip()]


class TestReplayEngineIdentityPropagation:
    def test_trade_and_outcome_rows_share_the_same_nonnull_id(self, config, tmp_path):
        """Test 1 from the operator's list: one replay trade -> TRADE row id
        is non-null, OUTCOME row has the exact same id."""
        config.log_dir = str(tmp_path)
        report = ReplayEngine(config=config, log_dir=str(tmp_path)).run(
            SAMPLE, review_date="2026-05-23"
        )
        assert report.approved_trades == 1
        entries = _journal_entries(report.journal_path)
        trade_row = next(e for e in entries if e.get("decision") == "TRADE")
        outcome_row = next(e for e in entries if e.get("type") == "OUTCOME")
        assert trade_row["paper_order_id"] is not None
        assert trade_row["paper_order_id"] == outcome_row["outcome"]["paper_order_id"]

    def test_replay_output_resolves_cleanly_through_journal_reader(self, config, tmp_path):
        """The whole point of this fix: JournalReader (the #327 identity join)
        must now resolve this trade instead of marking it unjoinable_legacy."""
        config.log_dir = str(tmp_path)
        ReplayEngine(config=config, log_dir=str(tmp_path)).run(
            SAMPLE, review_date="2026-05-23"
        )
        trades = JournalReader(tmp_path)._trades_for_day(date(2026, 5, 23))
        assert len(trades) == 1
        assert trades[0].unjoinable_legacy is False
        assert trades[0].result == "WIN"

    def test_default_market_model_pnl_and_result_unchanged(self, config, tmp_path):
        """Existing fill P&L/result behavior must be byte-identical to before
        this fix -- same assertions as
        tests/test_replay_ioc_baseline.py::test_default_market_model_unchanged."""
        config.log_dir = str(tmp_path)
        report = ReplayEngine(config=config, log_dir=str(tmp_path)).run(
            SAMPLE, review_date="2026-05-23"
        )
        assert report.approved_trades == 1
        assert report.wins == 1
        assert report.realized_pnl_dollars > 0
