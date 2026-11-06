# Autonomous Futures Paper-Trading System

Educational futures-system code focused on deterministic risk controls, replay, journaling, monitoring, and paper/demo validation.

## Safety posture

- Paper simulation is the default.
- Live trading is not supported by the public configuration.
- Broker-connected demo testing, where present in the codebase, must remain explicitly gated and independently verified.
- Missing, stale, contradictory, or unverified state should fail closed.
- No repository content is financial advice or a promise of profitability.

## Scope

The public repository is intended to contain reusable source code, tests, schemas, safety contracts, and sanitized documentation.

It must not contain:

- credentials, API keys, passwords, tokens, SSH material, or webhook secrets;
- real broker/account identifiers;
- private runtime journals or logs;
- VPS/production topology or operator-only deployment instructions;
- private handoffs or box-state evidence;
- proprietary research datasets or private strategy evidence unless explicitly approved for publication.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
pytest
```

Review every configuration value before running anything.

## Trading mode

The repository is designed around paper-first operation. A public checkout should use local paper simulation unless a separately reviewed demo configuration is intentionally enabled.

Never place real credentials in version control.

## Contributing

Bug reports and documentation feedback are welcome. Security issues should be reported privately through GitHub's vulnerability-reporting flow.

See `CONTRIBUTING.md` and `SECURITY.md`.

## License

See `LICENSE`.
