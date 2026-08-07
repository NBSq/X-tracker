# CLI Reference

Run `python -m app.main --help` for the authoritative option list. Commands below use the repository root as the working directory. Report and maintenance flags normally initialize the configured SQLite database; mutating commands state their additional side effects.

## Application And Ingestion

| Command | Purpose | Side effects |
| --- | --- | --- |
| `python -m app.main --version` | Print the canonical application version | None |
| `python -m app.main --mode local --mock-ai --no-telegram` | Process bundled sample posts offline | Writes analyses, history, and qualifying signals |
| `python -m app.main --mode rss --mock-ai` | Fetch configured RSS feeds once | Network fetch; writes content and analysis records |
| `python -m app.main --mode rss --mock-ai --watch` | Poll RSS continuously | Repeats fetches at `FETCH_INTERVAL_SECONDS` |
| `python -m app.main --mode live` | Fetch configured X accounts once | Requires `X_BEARER_TOKEN`; writes records |
| `python -m app.main --reset-db --mode local` | Clear analyses and alert history, then rerun | Destructive to application data in `DATABASE_PATH` |
| `python -m app.main --dashboard --dashboard-host 127.0.0.1 --dashboard-port 8000` | Serve dashboard and API | Opens a local HTTP listener |

`--summary` adds the narrative summary after an ingestion run. `--no-telegram` suppresses Telegram while preserving console output.

## Sources And Unified Events

| Command | Purpose | Side effects |
| --- | --- | --- |
| `--list-sources` / `--source-status` | List source configuration or current health | Read only |
| `--fetch-source ID` | Fetch one configured source | Network or file read; persists source items/events |
| `--enable-source ID` / `--disable-source ID` | Change source state | Updates SQLite source state |
| `--list-unified-events` | List deduplicated events | Read only |
| `--show-unified-event ID` | Show one event and its evidence | Read only |
| `--dedup-report` | Print deduplication statistics | Read only |
| `--rebuild-deduplication` | Recompute event grouping | Rebuilds derived deduplication records |

## Watchlists

| Command | Purpose | Side effects |
| --- | --- | --- |
| `--list-watchlists` | List watchlists | Read only |
| `--create-watchlist NAME` | Create a watchlist | Writes configuration; combine with threshold/options flags |
| `--delete-watchlist NAME` | Delete a watchlist | Removes its items and associations |
| `--enable-watchlist NAME` / `--disable-watchlist NAME` | Toggle matching | Updates configuration |
| `--watchlist-add-token NAME SYMBOL` | Add a token | Writes an item |
| `--watchlist-add-narrative NAME NARRATIVE` | Add a narrative | Writes an item |
| `--watchlist-remove-item NAME ITEM` | Remove a token or narrative | Deletes an item |
| `--watchlist-report NAME` | Show focused signal history and performance | Read only |

Creation options include `--watchlist-description`, `--watchlist-priority`, minimum hype/momentum/confidence, Telegram, digest, highlight, and case-sensitivity flags.

## Smart Rules

| Command | Purpose | Side effects |
| --- | --- | --- |
| `--list-rules` | List alert rules | Read only |
| `--create-rule NAME --rule-condition JSON --rule-actions JSON` | Create and validate a rule | Writes a rule |
| `--test-rule ID --rule-signal JSON` | Evaluate a rule against supplied signal facts | Read only; does not run actions |
| `--enable-rule ID` / `--disable-rule ID` | Toggle evaluation | Updates a rule |
| `--delete-rule ID` | Delete a rule | Deletes the rule and future behavior |

Conditions support token, narrative, hype, momentum, confidence, mentions, outcome success rate, and nested `AND`/`OR`/`NOT`. Actions support Telegram, priority, dashboard highlight, digest inclusion, and CSV markers.

## AI Reasoning

| Command | Purpose | Side effects |
| --- | --- | --- |
| `--ai-status` | Show provider, quota, cache, and fallback state | Read only |
| `--analyze-signal ID` | Request reasoning for one saved signal | May call OpenAI; writes analysis/cache/usage audit |
| `--clear-ai-cache` | Remove cached reasoning | Deletes cache records |

Use `AI_PROVIDER=mock` for deterministic offline operation.

## Reports And Outcomes

| Command | Purpose | Side effects |
| --- | --- | --- |
| `--trend-report` | Show 24-hour/7-day narratives and growth | May send Telegram |
| `--daily-digest` | Show the last 24 hours | May send Telegram |
| `--history-report --period 30d` | Historical signal analytics | Read only |
| `--top-opportunities` | Rank narratives by momentum, growth, and recency | May send Telegram |
| `--performance-report` | Aggregate generated-signal performance | May send Telegram |
| `--evaluate-signals` | Evaluate every due signal window | Writes unique outcome rows and events |
| `--outcome-report --outcome-period-hours 168` | Evaluate due signals and report outcomes | May write due outcomes and send Telegram |

## Relationship Graph

Use `--graph-summary`, `--graph-node TYPE:ID`, `--graph-top-narratives`, `--graph-top-tokens`, `--graph-emerging`, or `--graph-bridges` for read-only views. `--graph-snapshot daily`, `--graph-rebuild`, and `--graph-validate` create a snapshot, rebuild derived graph rows, or validate projection integrity.

## Signal Quality

Read reports with `--quality-summary`, `--quality-signal ID`, `--quality-sources`, `--quality-rules`, `--quality-watchlists`, `--quality-narratives`, `--quality-tokens`, `--quality-ai`, and `--quality-recommendations`. `--quality-recalculate` refreshes versioned derived scores; `--quality-validate` checks stored aggregates. Filters include `--quality-entity`, `--quality-period`, and `--quality-calculation-version`.

## Observability And Maintenance

| Command | Purpose | Side effects |
| --- | --- | --- |
| `--health-report` | Print liveness/readiness/component status | Performs local health checks |
| `--metrics-summary` | Print persisted operational metrics | Read only |
| `--component-health COMPONENT` | Show one component | Read only |

Use `--reset-db` only with an intentional backup. Graph rebuild, quality recalculation, deduplication rebuild, and outcome evaluation update derived data but preserve source content and saved signals.

## Saved Searches And Scheduled Reports

| Command | Purpose | Side effects |
| --- | --- | --- |
| `--list-saved-searches` | List definitions and execution metadata | Read only |
| `--saved-search-details ID` | Show definition and preview | Executes a non-recording preview |
| `--run-saved-search ID` | Execute an enabled search | Updates `last_run_at` and `run_count`; publishes an event |
| `--delete-saved-search ID` | Delete a search | Also removes linked reports through SQLite cascade |
| `--list-scheduled-reports` | List schedule, delivery, and health metadata | Read only |
| `--scheduled-report-details ID` | Show definition, preview, and run history | Executes a non-delivering preview |
| `--run-scheduled-report ID` | Claim and run a report immediately | May send one Telegram message and generate one CSV |

Saved-search and report creation remain dashboard/API-first so CLI users do not need to maintain complex JSON expressions.

## CSV Export

Core exports are `--export-signals-csv`, `--export-outcomes-csv`, `--export-performance-csv`, `--export-history-csv`, `--export-watchlists-csv`, and `--export-csv all`. Source, event, graph, and quality sections have corresponding `--export-*-csv` flags. Combine exports with `--output-dir PATH`, `--from-date YYYY-MM-DD`, and `--to-date YYYY-MM-DD`. Files use UTF-8 with BOM and unique timestamps; export commands create the output directory and never mutate analytics records.
