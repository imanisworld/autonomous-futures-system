# Security Policy

This public repository contains a reusable, paper-trading-focused engine.

## Never Commit

- API keys, passwords, tokens, webhook URLs, or account identifiers
- Real `.env` files
- Broker credentials or deployment SSH details
- Personal journals, scanner databases, reports, or replay output
- Proprietary strategy notes or production risk configuration

Use `.env.example` only for blank placeholders. Store real values in `.env`,
which is ignored by Git.

## Reporting

If you find a credential or sensitive operational detail in the repository,
revoke or rotate it first, then remove it from the repository and its history.

This project is educational and paper-first. Live trading is not enabled by
default and should not be enabled without independent security and risk review.
