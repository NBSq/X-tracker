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

## Run Tests

```powershell
pytest
```

The CI workflow runs the same test suite on Python 3.11 and 3.12.

## Mock AI Mode

Mock AI mode avoids OpenAI credentials and is the recommended development path for classifier, scoring, alert, and report work:

```powershell
python -m app.main --mode local --reset-db --summary
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

## Pull Requests

Before opening a PR:

- Keep changes focused and small enough to review.
- Add or update tests for behavior changes.
- Update README or docs when commands, setup, reports, or output formats change.
- Run `pytest` locally when possible.

PRs should explain the motivation, the implementation approach, and any remaining limitations.
