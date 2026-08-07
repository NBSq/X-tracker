# Scheduled Reports

Scheduled reports connect one enabled saved search to a delivery schedule. A lightweight background poller starts with the FastAPI application when `REPORT_SCHEDULER_ENABLED=true`. It finds due rows, claims each report atomically in SQLite, executes the search, produces a deterministic summary, delivers configured output, records run history, and calculates the next UTC run.

## Scheduling And Timezones

- `daily`: `schedule_value` uses `HH:MM` in the report timezone.
- `weekly`: `schedule_value` uses `MON@HH:MM` through `SUN@HH:MM`.
- `interval_hours`: an optional integer from 1 through 168.

The IANA timezone is stored on every report. Local wall-clock schedules are converted to UTC using Python `zoneinfo` and the cross-platform `tzdata` package. `next_run_at` is deterministic and persisted.

SQLite state prevents two workers from claiming the same report concurrently. A stale `running` claim can be recovered after two hours following an application crash. This is designed for one application instance; multi-process or multi-host scheduling requires an external coordinator and is not supported by the SQLite deployment.

## Delivery And Preview

`delivery_type=telegram` reuses `TelegramAlerter`. One HTML-escaped message contains the search, match count, up to ten top results, and summary. Messages are truncated below Telegram's 4096-character limit; the scheduler never sends one message per row. A per-report destination can override `TELEGRAM_CHAT_ID` without exposing it in logs.

Preview shows matching count, top results, deterministic summary, message rendering, and estimated CSV rows. Preview never sends Telegram and does not increment saved-search run metadata.

## CSV And Retention

When `include_csv=true`, the scheduler passes filtered rows to the existing `CSVExportService`. Files use UTF-8 with BOM, deterministic columns from the result set, sanitized names such as `scheduled_report_Daily_AI_2026-08-07_090000.csv`, and unique suffixes. Cleanup only scans `REPORT_OUTPUT_DIR`; manually created exports elsewhere are never deleted. Retention defaults to 30 days.

## Summaries And AI

Local summaries use already filtered metrics: match count, average quality, hype, momentum, source diversity, outcome coverage/success, noise, and strongest entities where available. They need no API key.

`REPORT_AI_SUMMARY_ENABLED=false` by default. When enabled with an OpenAI key, the report summarizer sends only bounded filtered metrics under prompt-injection-resistant instructions, uses the existing model, timeout, retry, request-limit, usage-audit, and cache settings, and falls back to the local summary on any failure.

## Run History And Failures

Every run records start/completion time, status, result count, delivery status, CSV path, sanitized error type, and duration. Report totals track successful and failed runs. Telegram misconfiguration or delivery failure marks the run failed while preserving its history and next schedule.

Dashboard pages are `/scheduled-reports`, `/scheduled-reports/new`, `/scheduled-reports/{id}`, and `/scheduled-reports/{id}/edit`. The detail page includes preview and run history.

```powershell
python -m app.main --list-scheduled-reports
python -m app.main --scheduled-report-details 1
python -m app.main --run-scheduled-report 1
```

## Docker

Keep one scheduler-enabled container per SQLite volume. Mount `/app/exports` when CSV files must survive replacement, and retain `/app/data` for SQLite. Disable the scheduler on read-only replicas with `REPORT_SCHEDULER_ENABLED=false`. Health details appear under the `report_scheduler` component and metrics are exposed at `/metrics`.
