# Contributing

Thanks for helping improve `x-narrative-tracker`. This project is intentionally local-first, so most contributions can be developed and tested without API credentials.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Do not commit `.env`, API keys, bot tokens, SQLite databases, or generated local artifacts.

Python 3.11 and 3.12 are supported. Create branches from the current default branch and keep commits focused. Large changes should begin with an issue describing the use case and compatibility impact.

## Run Tests

```powershell
python -m pytest
```

The CI workflow runs the same test suite on Python 3.11 and 3.12.

Before submitting a behavior change, also run the relevant offline command. Release-facing changes should verify `python -m app.main --version`, the local demo, and dashboard health endpoints. Docker changes should pass the build/smoke job in CI.

## Mock AI Mode

Mock AI mode avoids OpenAI credentials and is the recommended development path for classifier, scoring, alert, and report work:

```powershell
python -m app.main --mode local --mock-ai --reset-db --summary --no-telegram
python -m app.main --mode rss --mock-ai
```

Use `--watch` only when you intentionally want RSS mode to keep polling:

```powershell
python -m app.main --mode rss --mock-ai --watch
```

## Issues

Please open an issue for:

- Bugs or regressions
- Incorrect token or narrative classification
- RSS feed failures
- Telegram formatting problems
- Scoring or momentum behavior that looks misleading

Include logs, the command you ran, expected behavior, and actual behavior.

Search existing issues first. Security vulnerabilities must follow [SECURITY.md](SECURITY.md), not the public bug template. Remove keys, chat IDs, private source URLs, and private content from all reports.

## Pull Requests

Before opening a PR:

- Keep changes focused and small enough to review.
- Add or update tests for behavior changes.
- Update README or docs when commands, setup, reports, or output formats change.
- Run `pytest` locally when possible.

PRs should explain the motivation, the implementation approach, and any remaining limitations.

Keep backward compatibility unless a change is explicitly approved for a major release. Database changes must use additive initialization in `app/db/database.py` and include an upgrade test against an existing schema. New CLI/API/configuration surfaces need matching documentation.

By contributing, you agree to follow [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
