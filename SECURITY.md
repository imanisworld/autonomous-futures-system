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

Do not open a public issue for a suspected vulnerability, exposed credential,
or sensitive operational detail.

Use GitHub's **Report a vulnerability** action on the repository Security page
to submit a private report. Include:

- A concise description of the issue
- The affected file, endpoint, or workflow
- Reproduction steps that do not expose real credentials
- The potential impact
- Any suggested remediation

If you own an exposed credential, revoke or rotate it immediately before
waiting for a repository change.

## Supported Versions

Only the latest commit on `main` is supported. Historical versions and external
forks are not monitored.

This project is educational and paper-first. Live trading is not enabled by
default and should not be enabled without independent security and risk review.
