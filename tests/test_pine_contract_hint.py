"""Pine `contract_hint` emission (#960 requirement; design #966 amended by #968).

Static checks on the production alert script: the hint comes only from
`syminfo.current_contract` proven by the named contract's own close equal to
the continuous chart's close, it is null otherwise, and it adds no roll-date or
candidate logic. Plus: the reference alert template carries the field in the
form the backend (#969) accepts and normalizes.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from execution import contract_identity as ci
from webhook.payload import AlertPayload

ROOT = Path(__file__).resolve().parents[1]
PINE = (ROOT / "tradingview" / "risksentinel_context.pine").read_text(encoding="utf-8")
TEMPLATE = (ROOT / "tradingview" / "full_context_alert_message.json.tpl").read_text(encoding="utf-8")


def _block() -> str:
    start = PINE.index("// ─── CONTRACT IDENTITY")
    return PINE[start:PINE.index("// ─── BUILD JSON", start)]


def test_hint_is_emitted_in_the_alert_json_as_nullable_string():
    assert PINE.count('"\\"contract_hint\\":"      + s2j(contract_hint) + ","') == 1
    # s2j renders na as JSON null — the "unproven" value.
    assert 's2j(v)            => na(v) or v == "" ? "null"' in PINE


def test_hint_requires_current_contract_and_exact_close_proof():
    block = _block()
    assert "syminfo.current_contract" in block
    assert re.search(r"request\.security\(cc_symbol, timeframe\.period, close, ignore_invalid_symbol=true\)", block)
    assert "cc_close == close" in block
    assert "string contract_hint = cc_proven ? cc_name : na" in block
    assert 'str.startswith(cc_name, "MNQ") or str.startswith(cc_name, "MES")' in block


def test_hint_block_has_no_calendar_candidate_or_conversion_logic():
    code = "\n".join(line for line in _block().splitlines() if not line.lstrip().startswith("//"))
    for banned in ("timenow", "year(", "month(", "dayofweek", "timestamp(", "ROLL", "H2", "M2", "U2", "Z2", "+ (", "- (", "*"):
        assert banned not in code, banned


def test_template_hint_parses_and_matches_the_routed_form():
    body = json.loads(re.sub(r"\{\{[^}]+\}\}", "0", TEMPLATE).replace('"0"', '"MNQ1!"'))
    assert body["contract_hint"] == "MNQZ2026"
    payload = AlertPayload(**{**body, "ticker": "MNQ1!", "timestamp": "2026-09-24T14:30:00+00:00",
                              "open": 20000, "high": 20010, "low": 19990, "close": 20005})
    assert payload.contract_hint == "MNQZ2026"
    verdict = ci.compare(payload.contract_hint, "MNQZ6", context_date=date(2026, 9, 24))
    assert verdict.status == ci.MATCH
