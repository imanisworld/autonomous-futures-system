# Security Policy

## Supported version

Only the latest public `main` branch is supported.

## Never publish

Do not commit or post:

- API keys, passwords, tokens, cookies, SSH keys, or webhook secrets;
- real broker, account, or customer identifiers;
- real `.env` files;
- private runtime logs, journals, scanner databases, or reports;
- production/VPS addresses, credentials, access details, or private operator instructions;
- secret-derived values or screenshots containing sensitive state.

Use sanitized examples and synthetic fixtures only.

## Reporting a vulnerability

Use GitHub's private vulnerability-reporting flow on the repository Security page.

Do not disclose an active credential or sensitive operational detail in a public issue, discussion, pull request, or screenshot.

If you control a credential that may have been exposed, revoke or rotate it immediately.

## Trading-system safety

A security fix does not authorize live trading, broker execution, deployment, or runtime mutation. Those actions require their own explicit safety and operator gates.
