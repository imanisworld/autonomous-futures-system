"""Options research/authority evidence layer.

Pure models and evaluators for prospective options evidence. This package
currently contains ``strategy_epochs`` (the preregistered, hash-pinned strategy
epoch registry).

Nothing in this package fetches market data, sends alerts, touches a broker,
or grants execution authority. The epoch registry is observation-only and
fail-closed: it can never enable, promote, or restore trading.
"""
