"""Options research/authority evidence layer.

Pure models and evaluators for prospective options evidence: strategy epochs,
canonical prospective signals, outcome evidence, strategy fitness, and the
alert/research projections built on them.

Nothing in this package fetches market data, sends alerts, touches a broker,
or grants execution authority. Its only authority-bearing output is a
*revocation* (see ``fitness``); it can never enable, promote, or restore
trading.
"""
