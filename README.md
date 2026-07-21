# x-narrative-tracker

Local-first crypto narrative intelligence from X posts and RSS news.

`x-narrative-tracker` collects recent crypto content, extracts tokens and narratives, measures hype and momentum, stores historical signals in SQLite, and delivers actionable Telegram alerts and reports.

The project supports real APIs, public RSS feeds, and a fully local mock-AI workflow for development and evaluation.

## Highlights

- Monitor configured X accounts through an X API v2-compatible client
- Ingest crypto news from configurable RSS and Atom feeds
- Analyze content with OpenAI structured outputs or deterministic mock AI
- Extract tokens, narratives, sentiment, importance, and summaries
- Calculate hype scores and 0-100 Narrative Momentum scores
- Track narrative history, growth, recency, and importance in SQLite
- Send HTML-formatted Telegram spike alerts, summaries, trends, and digests
- Explore signals and performance in the built-in FastAPI analytics dashboard
- Run a complete local MVP without X or OpenAI credentials

## Screenshots

### Analytics Dashboard

![Analytics dashboard overview](docs/screenshots/dashboard-overview.png)

The dashboard presents system health, latest signals, evaluated accuracy, momentum, top narratives, and top tokens from the existing SQLite database.

## Architecture

```mermaid
flowchart LR
    X["X API v2"] --> Sources["Source Clients"]
    RSS["RSS / Atom Feeds"] --> Sources
    Local["Local Sample Posts"] --> Sources

    Sources --> Posts["Shared Post Model"]
    Sources -->|RSSFetched| Bus["Internal Event Bus"]
    Posts --> Analyzer{"Analyzer"}
    Analyzer --> OpenAI["OpenAI Structured Output"]
    Analyzer --> MockAI["Mock AI Keyword Rules"]

    OpenAI --> DB[("SQLite")]
    MockAI --> DB
    Analyzer -->|NarrativeDetected| Bus

    DB --> Hype["Hype Scoring"]
    DB --> Momentum["Narrative Momentum"]
    DB --> History["Narrative History"]
    DB --> Outcomes["Signal Outcomes Engine"]
    DB --> Dashboard["FastAPI Dashboard"]

    Hype -->|SignalCreated| Bus
    Outcomes -->|SignalEvaluated| Bus
    Bus --> Storage["Database Subscriber"]
    Bus --> Performance["Performance Subscriber"]
    Bus --> Telegram["Telegram Subscriber"]
    Performance -->|PerformanceUpdated| Bus
    Bus -. Optional live events .-> Dashboard

    Bus --> Alerts["Spike Alerts"]
    Momentum --> Reports["Trend Reports / Daily Digests"]
    History --> Reports
    Outcomes --> Reports

    Alerts --> Console["Console Logging"]
    Telegram --> TelegramAPI["Telegram Bot API"]
    Reports --> Console
    Reports --> TelegramAPI
    Dashboard --> Browser["Jinja2 + Bootstrap UI"]
```

### Internal Event Flow

The application uses a synchronous, typed, in-process event bus with no external dependencies. Publishers call `publish(event)`, while components register handlers with `subscribe(event_type, handler)`. Handlers run in registration order during the same process, keeping the MVP deterministic and easy to test.

| Event | Published when | Current consumers |
| --- | --- | --- |
| `RSSFetched` | An RSS cycle returns shared posts | Extension point for source telemetry |
| `NarrativeDetected` | Analysis detects a narrative in a post | Extension point for dashboards and APIs |
| `SignalCreated` | Hype evaluation creates an alert | SQLite alert storage, performance tracking, Telegram |
| `SignalEvaluationRequested` | A configured evaluation window becomes eligible for processing | Outcome evaluator observability and future workers |
| `SignalEvaluated` | The outcomes engine evaluates a mature signal | SQLite outcome storage, performance updates |
| `PerformanceUpdated` | Signal or outcome performance changes | Extension point for dashboards and REST APIs |

The bus is intentionally transport-neutral. Future Dashboard, REST API, websocket, or audit-log adapters can subscribe without changing scoring and analysis code. Existing public helpers remain callable without supplying a bus; they create and register the default subscribers automatically.

## Scoring

### Hype Score

```text
hype score = mentions count * average importance
```

The raw hype score remains internal and is only emitted in debug logs. User-facing reports show a normalized display score from `0-100`.

Example:

```text
raw score = 116
display score = 92/100
```

Display score interpretation:

| Score | Meaning |
| --- | --- |
| 0-20 | Low |
| 21-40 | Moderate |
| 41-60 | Strong |
| 61-80 | High |
| 81-100 | Extreme |

### Narrative Momentum

Narrative Momentum is a bounded `0-100` heuristic combining:

- Mentions during the last 24 hours
- Growth versus the preceding 24-hour period
- Average importance
- Recency of the latest mention

Momentum rankings appear in spike alerts, trend reports, and daily digests.

## Project Structure

```text
app/
  main.py
  config.py
  ai/analyzer.py
  alerts/telegram.py
  db/database.py
  dashboard/app.py
  dashboard/service.py
  dashboard/static/
  dashboard/templates/
  events/bus.py
  events/models.py
  events/subscribers.py
  export/csv_exporter.py
  scoring/hype_score.py
  scoring/momentum_score.py
  scoring/signal_outcomes.py
  sources/local_client.py
  sources/rss_client.py
  sources/x_client.py
data/
  accounts.json
  narratives.json
  rss_feeds.json
  sample_posts.json
scripts/
  run_daily_digest.bat
  run_rss_mock.bat
tests/
```

## Requirements

- Python 3.11+
- SQLite with JSON functions
- Internet access for RSS, X, OpenAI, or Telegram integrations

## Setup

Create a virtual environment and install dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Configure `.env`:

```dotenv
# Required for live X mode
X_BEARER_TOKEN=

# Required for OpenAI analysis; not required with --mock-ai
OPENAI_API_KEY=

# Optional: both values are required to enable Telegram delivery
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Optional application settings
DATABASE_PATH=x_narrative_tracker.sqlite3
OPENAI_MODEL=gpt-4o-mini
FETCH_INTERVAL_SECONDS=900
HYPE_ALERT_THRESHOLD=25
POSTS_PER_ACCOUNT=10
RSS_ARTICLES_PER_FEED=10
OUTCOME_EVALUATION_HOURS=24
OUTCOME_EVALUATION_WINDOWS=24,72,168
OUTCOME_SUCCESS_THRESHOLD=10
OUTCOME_FAILURE_THRESHOLD=-10
```

When Telegram credentials are missing, the application continues normally and logs reports to the console.

## Quick Start

Run the complete offline local MVP:

```powershell
python -m app.main --mode local --reset-db --summary
```

This command reads 30 sample posts, performs deterministic analysis, stores results in SQLite, calculates scores, prints alerts, and produces a summary.

## Analytics Dashboard

Start the built-in dashboard against the SQLite database configured by `DATABASE_PATH`:

```powershell
python -m app.main --dashboard
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Use another host or port when needed:

```powershell
python -m app.main --dashboard --dashboard-host 0.0.0.0 --dashboard-port 8080
```

The dashboard is read-only and provides these pages:

- `/` overview
- `/signals` latest signals and outcomes
- `/performance` accuracy, confidence, momentum, and narrative results
- `/outcomes` filterable signal outcome history and metric changes
- `/narratives` 24-hour narrative rankings
- `/tokens` 24-hour token rankings

JSON endpoints are available at `/api/signals`, `/api/performance`, `/api/outcomes`, `/api/outcomes/summary`, `/api/outcomes/{signal_id}`, `/api/narratives`, `/api/tokens`, and `/api/status`. Pages poll these endpoints every 30 seconds, so collector and dashboard processes can share the same SQLite file without restarting the web server. The Outcomes page filters by status, evaluation window, token, and narrative.

The app factory accepts an optional `EventBus`, allowing an embedded dashboard to subscribe to `PerformanceUpdated` and `NarrativeDetected`. The standard CLI deployment remains database-driven so it also works as a separate process.

The dashboard has no authentication. Keep the default localhost binding unless access is protected by a trusted reverse proxy or private network.

## Source Modes

### Local Mode

Local mode requires no X or OpenAI credentials:

```powershell
python -m app.main --mode local
```

Useful options:

```powershell
python -m app.main --mode local --reset-db
python -m app.main --mode local --summary
python -m app.main --mode local --no-telegram
```

### RSS Mode

RSS mode reads public feeds from `data/rss_feeds.json`. By default it performs one fetch-and-analysis run, then exits:

```powershell
python -m app.main --mode rss --mock-ai
```

Run continuously using the configured 15-minute polling interval:

```powershell
python -m app.main --mode rss --mock-ai --watch
```

RSS mode requires `OPENAI_API_KEY` unless mock AI is enabled.

Example feed configuration:

```json
{
  "RSS_FEEDS": [
    {
      "name": "CoinDesk",
      "url": "https://www.coindesk.com/arc/outboundfeeds/rss/"
    }
  ]
}
```

Each feed is isolated: an unavailable or malformed feed is logged without stopping the remaining sources.

### X Mode

Configure usernames in `data/accounts.json`, then run:

```powershell
python -m app.main --mode live
```

Live mode requires `X_BEARER_TOKEN` and, unless mock AI is enabled, `OPENAI_API_KEY`.

## Mock AI Mode

Mock AI uses local keyword rules instead of OpenAI:

```powershell
python -m app.main --mode rss --mock-ai
python -m app.main --mode live --mock-ai
```

It detects common tokens and configured narrative aliases, then generates sentiment, importance, spike explanations, confidence, and suggested actions. The database, alerts, momentum, and report pipeline remains identical.

Mock AI removes OpenAI dependency, but RSS and X modes still require network access to their respective sources.

### Mock AI Limitations

Mock AI is designed for fast, deterministic classification rather than language-level understanding:

- It relies on keyword and ticker matches, so implied narratives without recognizable terms may be missed.
- Ambiguous tickers such as `OP`, `TON`, `NEAR`, and `LINK` are detected when uppercase, prefixed with `$`, or referenced by project name to reduce false positives.
- Sentiment and importance are lexical heuristics and do not understand sarcasm, source credibility, or market context.
- Narrative mappings are intentionally broad. For example, macroeconomic terms map to `Bitcoin / macro` even when an article is not exclusively about Bitcoin.
- OpenAI mode remains the better option for nuanced classification, summaries, and explanations.

The canonical mock-AI taxonomy includes Bitcoin/macro, Ethereum/L2, Solana, AI agents, DePIN, RWA, memecoins, gaming, stablecoins, ETFs, regulation, privacy, DeFi, and infrastructure.

## Reports

Generate a narrative trend report from stored history:

```powershell
python -m app.main --trend-report
```

The report includes:

- Top narratives over the last 24 hours
- Top narratives over the last 7 days
- Fastest-growing narratives
- Narrative Momentum rankings

Generate a daily digest:

```powershell
python -m app.main --daily-digest
```

The digest includes:

- Top five tokens from the last 24 hours
- Top five narratives from the last 24 hours
- Fastest-growing narrative
- Top three most important posts or articles
- Narrative Momentum rankings
- Short closing summary

Reports are sent to Telegram automatically when credentials are configured. Add `--no-telegram` for console-only output.

Generate a seven-day Narrative Momentum comparison:

```powershell
python -m app.main --history-report
```

Every processing run upserts one momentum snapshot per narrative for the current UTC date in the `daily_momentum` SQLite table. The history report compares today's score with the latest available snapshot on or before seven days ago.

Rank the strongest narrative opportunities from stored momentum history:

```powershell
python -m app.main --top-opportunities
```

Opportunity rankings combine latest momentum, seven-day growth, and snapshot recency. Results are classified as `Emerging`, `Growing`, or `Watchlist` and are sent to Telegram when configured.

Generate signal performance metrics from saved alerts:

```powershell
python -m app.main --performance-report
```

Every generated alert is saved in the `signal_history` SQLite table with signal type, token, narrative, display hype score, mention count, momentum score, confidence, and action. The existing performance report retains its confidence/action proxy definition for backward compatibility.

### Signal Outcomes

Generate an outcome report from mature saved signals:

```powershell
python -m app.main --outcome-report
```

Evaluate due signals without producing or sending a report:

```powershell
python -m app.main --evaluate-signals
```

Reports can optionally be limited to recently evaluated outcomes:

```powershell
python -m app.main --outcome-report --outcome-period-hours 168
```

The outcomes engine also runs automatically after each source-processing cycle. At each window in `OUTCOME_EVALUATION_WINDOWS` (default `24,72,168` hours), it compares the saved signal baseline with current stored feed statistics:

- Mention count
- Normalized hype score
- Momentum score

Classification uses absolute metric changes and shared configurable boundaries:

- `SUCCESS`: at least one metric change is greater than or equal to `OUTCOME_SUCCESS_THRESHOLD`.
- `FAILED`: no metric reaches the success threshold and at least one metric change is less than or equal to `OUTCOME_FAILURE_THRESHOLD`.
- `NEUTRAL`: all changes remain strictly between the two thresholds.

Success has deterministic precedence when different metrics cross opposing boundaries. Missing current activity is treated as zero and recorded in the outcome notes. `OUTCOME_EVALUATION_HOURS` remains supported as a legacy single-window setting when `OUTCOME_EVALUATION_WINDOWS` is absent.

Each `(signal_id, evaluation_window_hours)` pair is unique, so repeated runs cannot create duplicate evaluations. Existing `signal_outcomes` rows are migrated in place: the old horizon and change fields are retained, new baseline/current fields are added, and compatible values are backfilled from `signal_history` plus the legacy deltas.

The outcome report includes totals, success rate, average hype, mention, and momentum changes, plus best/worst narratives ranked by success rate. It is sent to Telegram automatically when configured. While the tracker is running, sending `/performance` to the configured bot returns the same concise outcome summary; commands from other chat IDs are ignored.

Signal Outcomes measure whether narrative or token attention continued in the tracker data. They do not use market prices and must not be interpreted as token price profitability or investment performance.

## CSV Export

Export stored data without starting X, RSS, OpenAI, or Telegram integrations:

```powershell
python -m app.main --export-signals-csv
python -m app.main --export-outcomes-csv
python -m app.main --export-performance-csv
python -m app.main --export-csv all
```

Files are written to `exports/` by default. Use `--output-dir` to select another directory; missing directories are created automatically:

```powershell
python -m app.main --export-csv all --output-dir reports/csv
```

Calendar-date filters are inclusive. Signal exports filter by signal creation time, outcome exports filter by evaluation time, and performance exports apply the corresponding date range to each dataset:

```powershell
python -m app.main --export-csv all --from-date 2026-07-01 --to-date 2026-07-21
```

Generated files use timestamped names and never overwrite an existing export:

```text
signals_2026-07-21_180000.csv
outcomes_2026-07-21_180000.csv
performance_2026-07-21_180000.csv
narrative_performance_2026-07-21_180000.csv
```

Signal columns contain the stored identity, creation time, token/narrative, hype, momentum, mentions, confidence, action, and latest outcome status. Outcome columns contain the signal and evaluation timestamps, evaluation window, baseline/current metrics, changes, status, and notes. No synthetic explanation is added because signal explanations are not persisted in SQLite.

Performance export creates one overall summary row plus a separate narrative-level file with signal/evaluation counts, status counts, success rate, and average metric changes. Empty result sets still produce files with headers.

CSV files use deterministic column ordering, standard CSV quoting, ISO 8601 timestamps, and UTF-8 with a byte-order mark for Microsoft Excel compatibility. Numeric values do not include display symbols such as `%`, `+`, or `/100`.

## Telegram Examples

### Hype Spike

```text
Crypto Hype Spike

Token/Narrative: SOL
Hype Score: 92/100
Confidence: 8/10
Action: research

Why it matters:
SOL is appearing across several high-importance posts.

Top posts:
1. @account: SOL activity continues to grow...

Narrative Momentum:
Solana ecosystem 92
```

### Daily Digest

```text
Crypto Daily Digest

Top 5 tokens last 24h
1. SOL - hype score 92/100

Fastest growing narrative
AI Agents +42%

Narrative Momentum
AI Agents 92
RWA 61
Memecoins 47
```

Telegram messages use HTML formatting and escape dynamic content before delivery.

## Windows Task Scheduler

The included batch scripts change to the project directory before running.

Test them manually:

```powershell
scripts\run_rss_mock.bat
scripts\run_daily_digest.bat
```

### RSS Mock Tracker

Create a Task Scheduler task with:

- Program: `<PROJECT_DIR>\scripts\run_rss_mock.bat`
- Trigger: daily, repeating every 15 minutes indefinitely
- Existing instance rule: **Do not start a new instance**
- Setting: **Run task as soon as possible after a scheduled start is missed**

The RSS batch script includes `--watch`, so it remains active and performs its own 15-minute polling loop.

### Daily Digest

Create a second task with:

- Program: `<PROJECT_DIR>\scripts\run_daily_digest.bat`
- Trigger: daily at the preferred morning time
- Setting: **Run task as soon as possible after a scheduled start is missed**

## Testing

Run the test suite:

```powershell
pytest
```

Tests cover:

- RSS and Atom parsing
- Mock AI token, narrative, and sentiment detection
- Narrative history and growth calculations
- Narrative Momentum scoring
- Telegram formatting, HTML escaping, and payloads
- Signal Outcomes evaluation, migration, API, and dashboard behavior
- CSV headers, encoding, escaping, date filters, aggregate exports, and CLI behavior

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md) for setup, test, issue, and pull request guidance.

Good first areas include:

- Improving mock AI keyword coverage
- Adding RSS feed fixtures
- Refining scoring and report output
- Expanding Telegram formatting tests
- Improving docs and screenshots

Please run tests before opening a PR:

```powershell
pytest
```

## Data and Operations

- SQLite is created automatically at `DATABASE_PATH`.
- Existing posts are skipped using their stable source IDs.
- Spike alerts are de-duplicated for the same signal within 60 minutes.
- Token and narrative spikes sharing at least two-thirds of their top-post set are merged into one alert.
- Merged spike hype is recalculated from all unique posts causing the paired signals; only the top three are displayed. This prevents token and narrative components from double-counting the same evidence.
- Narrative score snapshots are stored after each processing run.
- Generated alerts are stored in `signal_history` for performance reporting.
- Mature signals are evaluated automatically and stored in `signal_outcomes`.
- Existing databases are migrated in place with signal mention baselines and the expanded multi-window outcome schema.
- `--reset-db` clears analyses, alerts, narrative history, momentum snapshots, signal history, and signal outcomes.
- Individual post-analysis, feed, OpenAI, and Telegram errors are logged without silently failing.

## Roadmap

- [x] Add a web dashboard for narratives, tokens, and source activity
- [x] Add configurable multi-window signal outcome evaluation
- [ ] Add source-level reliability and influence weighting
- [ ] Add semantic clustering for emerging narratives
- [ ] Add historical charts and momentum sparklines
- [ ] Add PostgreSQL support for larger deployments
- [ ] Add Docker and cross-platform service definitions
- [ ] Add scheduled report configuration and multiple Telegram destinations
- [ ] Add integration tests against recorded API fixtures
- [ ] Add packaging, release automation, and a project license

## Disclaimer

This project is an experimental monitoring and research tool. Scores, summaries, and suggested actions are heuristic outputs and are not financial advice.

## License

Released under the [MIT License](LICENSE).
