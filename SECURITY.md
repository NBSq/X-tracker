# Security Policy

## Reporting Vulnerabilities

Please report suspected vulnerabilities privately. Do not open a public issue for security-sensitive reports.

Send a report to the repository maintainer with:

- A clear description of the issue
- Steps to reproduce
- Potential impact
- Suggested remediation, if known

If this repository is mirrored to GitHub, use GitHub's private vulnerability reporting feature when available.

## Secrets and API Keys

Never commit:

- `.env`
- `OPENAI_API_KEY`
- `X_BEARER_TOKEN`
- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- SQLite databases containing private source history
- Logs containing API responses or credentials

Use `.env.example` as the committed template and keep real credentials local.

## Supported Versions

This project is pre-1.0. Security fixes are applied to the latest main branch.
