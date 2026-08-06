# Troubleshooting

## Configuration Fails At Startup

The loader names invalid environment variables in its exception. Start from a fresh `.env.example`, keep numeric values unquoted, use comma-separated positive integers for `OUTCOME_EVALUATION_WINDOWS`, and use `mock`, `openai`, or `auto` for `AI_PROVIDER`.

## Database Is Locked

Only run one ingestion writer against a SQLite file. Stop duplicate `--watch`, Task Scheduler, or container processes. Copy the database and its `-wal`/`-shm` files before maintenance. SQLite is intended for a single-node deployment, not multiple replicas writing a shared volume.

If startup reports an `OperationalError` for a new `DATABASE_PATH`, create its parent directory first. SQLite creates the database file, but not missing directories.

## Telegram Is Not Configured

This is a supported state. Configure both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`, or use `--no-telegram`. The application logs `Telegram not configured` and keeps console alerts as the fallback.

## RSS Fetches Fail

Check the feed URL, network/DNS access, source status, timeout, and publisher rate limits. Use `--fetch-source ID` for one source and `--source-status` for stored failures. Public feeds may move or change without notice.

## OpenAI Is Unavailable

Use `AI_PROVIDER=mock` or `--mock-ai` for deterministic offline analysis. With `OPENAI_FALLBACK_TO_MOCK=true`, qualified reasoning falls back after bounded failures. Never paste a key into logs or issues.

## Dashboard Does Not Start

Confirm dependencies are installed and the port is free:

```powershell
python -m uvicorn app.dashboard.app:create_app --factory --host 127.0.0.1 --port 8000
```

Use another port when needed. A browser request for `/favicon.ico` may return `404`; it does not affect application health.

## Docker Container Is Not Ready

Run `docker compose ps`, inspect `docker compose logs tracker`, then check `/live` and `/ready`. Verify the data/config mounts are writable/readable and that `DATABASE_PATH` points inside the writable data volume. See [deployment.md](deployment.md).

## Scores Look Unexpected

Hype is based on mentions and average importance, while the displayed score is normalized to 0-100. Momentum, quality, relationships, and outcomes are heuristics over available content. Rebuild derived projections after a large import and review [configuration.md](configuration.md) thresholds.

## Tests Leave Local Files

Generated databases, logs, exports, `.env`, caches, and bytecode are ignored. If a test fails, keep fixtures under `tests/` and do not commit a local SQLite database or credential file.
