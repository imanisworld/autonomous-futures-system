"""Test helper: a ``_find_contract_id`` stub that resolves like production.

Production ``TradovateBroker._find_contract_id`` always records the exact
dated front-month symbol it resolved. Stubs must do the same, otherwise the
broker (U8) correctly refuses to route an unresolved contract.
"""
from __future__ import annotations

import types

from execution.tradovate_broker import _broker_root, _front_month_symbol


def exact_contract_stub(contract_id: int):
    def _find(self, instrument: str) -> int:
        root = _broker_root(instrument)
        desired = _front_month_symbol(root, self._trading_date())
        if desired is None:
            raise ValueError(f"no exact dated-contract policy for {root}")
        self._contract_cache[root] = contract_id
        self._contract_symbol_cache[root] = desired
        self._contract_roll_key[root] = desired
        return contract_id

    return _find


def bind_exact_contract(broker, contract_id: int):
    """Bound-method form for ``monkeypatch.setattr(broker, ...)``."""
    return types.MethodType(exact_contract_stub(contract_id), broker)
