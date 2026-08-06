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

| Version | Supported |
| --- | --- |
| 1.0.x | Yes |
| 0.x | No |

Security fixes are applied to the latest stable release and the current default branch.

## Deployment Guidance

- The dashboard/API has no built-in authentication. Bind to `127.0.0.1` or use an authenticated TLS reverse proxy.
- Do not expose SQLite files, `.env`, logs, exports, or Prometheus metrics publicly.
- Run the Docker image as its configured non-root user and keep writable data in the mounted data volume.
- Rotate a credential immediately if it is exposed, then remove it from Git history and logs.
- Treat RSS/X/OpenAI response content as untrusted input; keep HTML escaping and request timeouts enabled.

Please allow maintainers reasonable time to reproduce and remediate a report before public disclosure. Receipt and remediation timelines depend on maintainer availability; no paid bounty program is currently offered.
