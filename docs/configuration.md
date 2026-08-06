# Configuration

Copy `.env.example` to `.env`. Relative paths are resolved from the repository root. Empty credential values disable the corresponding optional integration. Never commit `.env`.

## Credentials And Core Runtime

| Variable | Purpose | Default | Required | Safe example |
| --- | --- | --- | --- | --- |
| `X_BEARER_TOKEN` | X API v2 bearer token | empty | Live X mode only | empty |
| `OPENAI_API_KEY` | OpenAI API credential | empty | `AI_PROVIDER=openai` only | empty |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token | empty | No | empty |
| `TELEGRAM_CHAT_ID` | Telegram destination | empty | No | empty |
| `DATABASE_PATH` | SQLite file | `x_narrative_tracker.sqlite3` | No | `data/tracker.sqlite3` |
| `FETCH_INTERVAL_SECONDS` | Watch-mode polling interval | `900` | No | `900` |
| `HYPE_ALERT_THRESHOLD` | Raw hype threshold for signal creation | `25` | No | `25` |
| `POSTS_PER_ACCOUNT` | X posts per account | `10` | No | `10` |
| `RSS_ARTICLES_PER_FEED` | Articles per RSS feed | `10` | No | `10` |
| `ACCOUNTS_PATH` | X usernames JSON | `data/accounts.json` | No | `data/accounts.json` |
| `NARRATIVES_PATH` | Narrative keyword JSON | `data/narratives.json` | No | `data/narratives.json` |
| `SAMPLE_POSTS_PATH` | Offline post fixture | `data/sample_posts.json` | No | `data/sample_posts.json` |
| `RSS_FEEDS_PATH` | RSS feed configuration | `data/rss_feeds.json` | No | `data/rss_feeds.json` |

## AI

| Variable | Purpose | Default |
| --- | --- | --- |
| `AI_PROVIDER` | `mock`, `openai`, or `auto` | `mock` |
| `OPENAI_MODEL` | Model identifier | `gpt-4o-mini` |
| `OPENAI_TIMEOUT_SECONDS` | Request timeout | `30` |
| `OPENAI_MAX_RETRIES` | Retry count | `1` |
| `OPENAI_MAX_OUTPUT_TOKENS` | Structured response ceiling | `700` |
| `OPENAI_MAX_POST_LENGTH` | Input truncation length | `600` |
| `OPENAI_MIN_HYPE_SCORE` | Reasoning eligibility, 0-100 | `65` |
| `OPENAI_MIN_MOMENTUM_SCORE` | Reasoning eligibility, 0-100 | `50` |
| `OPENAI_MIN_CONFIDENCE` | Reasoning eligibility, 0-10 | `6` |
| `OPENAI_DAILY_REQUEST_LIMIT` | Local daily request guardrail; `0` disables calls | `100` |
| `OPENAI_CACHE_TTL_HOURS` | Analysis cache lifetime | `24` |
| `OPENAI_FALLBACK_TO_MOCK` | Use deterministic reasoning after failure | `true` |
| `OPENAI_STORE_RESPONSES` | Persist raw provider responses | `false` |

Mock analysis is deterministic but keyword-based. It can miss slang, symbols with ambiguous meanings, sarcasm, multilingual content, and novel narratives.

## Outcomes And History

| Variable | Purpose | Default |
| --- | --- | --- |
| `OUTCOME_EVALUATION_WINDOWS` | Unique evaluation windows in hours | `24,72,168` |
| `OUTCOME_EVALUATION_HOURS` | Legacy single-window compatibility setting | `24` |
| `OUTCOME_SUCCESS_THRESHOLD` | Positive metric-change boundary | `10` |
| `OUTCOME_FAILURE_THRESHOLD` | Negative metric-change boundary | `-10` |
| `HISTORY_GROWTH_THRESHOLD` | Growth threshold used by reports | `20` |
| `HISTORY_MINIMUM_ACTIVITY` | Minimum history activity | `2` |

Outcome classification measures continuation in mentions, momentum, or hype. It does not measure token price profitability.

## Multi-source Ingestion

| Variable | Purpose | Default |
| --- | --- | --- |
| `CONTENT_SOURCES_PATH` | Source registry JSON | `config/sources.json` |
| `CONTENT_SOURCES_FILE` | Legacy alias for the source registry | `config/sources.json` |
| `SOURCE_ENABLED` | Enable configured source orchestration | `true` |
| `SOURCE_FETCH_TIMEOUT_SECONDS` | Fetch timeout | `20` |
| `SOURCE_MAX_RETRIES` | Fetch retries | `2` |
| `SOURCE_DEFAULT_INTERVAL_SECONDS` | Default source schedule | `300` |
| `SOURCE_FAILURE_BACKOFF_SECONDS` | Retry backoff after failure | `600` |
| `SOURCE_MAX_ITEMS_PER_FETCH` | Per-source item cap | `100` |
| `SOURCE_ALERT_AFTER_FAILURES` | Failure count before health alert | `3` |
| `SOURCE_FAILURE_ALERT_COOLDOWN_MINUTES` | Failure alert cooldown | `60` |
| `SOURCE_RECOVERY_NOTIFICATIONS` | Notify after recovery | `true` |

## Deduplication And Event Updates

| Variable | Purpose | Default |
| --- | --- | --- |
| `DEDUPLICATION_ENABLED` | Group duplicate source coverage | `true` |
| `DEDUPLICATION_TIME_WINDOW_HOURS` | Candidate time window | `24` |
| `DEDUPLICATION_TITLE_SIMILARITY_THRESHOLD` | Normalized title threshold | `0.82` |
| `DEDUPLICATION_BODY_SIMILARITY_THRESHOLD` | Normalized body threshold | `0.88` |
| `DEDUPLICATION_MIN_SHARED_ENTITIES` | Shared token/narrative minimum | `1` |
| `DEDUPLICATION_CROSS_SOURCE_ONLY` | Restrict fuzzy merges across sources | `false` |
| `EVENT_UPDATE_NOTIFICATIONS` | Enable material-change notices | `true` |
| `EVENT_UPDATE_MIN_NEW_SOURCES` | New-source threshold | `2` |
| `EVENT_UPDATE_MIN_HYPE_CHANGE` | Hype-change threshold | `10` |
| `EVENT_UPDATE_MIN_MOMENTUM_CHANGE` | Momentum-change threshold | `10` |
| `EVENT_UPDATE_COOLDOWN_MINUTES` | Update notification cooldown | `30` |

## Relationship Graph

| Variable | Purpose | Default |
| --- | --- | --- |
| `GRAPH_RECENCY_HALF_LIFE_DAYS` | Edge recency decay | `14` |
| `GRAPH_MIN_EDGE_WEIGHT` | Stored/displayed edge floor | `0.05` |
| `GRAPH_MIN_NODE_WEIGHT` | Node floor | `0.05` |
| `GRAPH_AI_RELATIONSHIP_MIN_CONFIDENCE` | AI-suggested edge threshold | `0.75` |
| `GRAPH_DEFAULT_PERIOD_DAYS` | Default graph window | `30` |
| `GRAPH_MAX_NODES` | Query/render cap | `250` |

## Quality Analytics

`QUALITY_CALCULATION_VERSION` defaults to `1`; `QUALITY_MINIMUM_EVIDENCE` to `2`; and `QUALITY_MINIMUM_SAMPLE_SIZE` to `5`. Component weights are configured with `QUALITY_WEIGHT_OUTCOME` (`0.25`), `QUALITY_WEIGHT_CALIBRATION` (`0.15`), `QUALITY_WEIGHT_SOURCE_RELIABILITY` (`0.15`), `QUALITY_WEIGHT_EVIDENCE` (`0.10`), `QUALITY_WEIGHT_SOURCE_DIVERSITY` (`0.10`), `QUALITY_WEIGHT_TIMELINESS` (`0.10`), `QUALITY_WEIGHT_RULE_PRECISION` (`0.05`), `QUALITY_WEIGHT_WATCHLIST_RELEVANCE` (`0.05`), and `QUALITY_WEIGHT_AI_AGREEMENT` (`0.05`).

Bands default to `85`, `70`, `55`, and `40` through `QUALITY_EXCELLENT_THRESHOLD`, `QUALITY_STRONG_THRESHOLD`, `QUALITY_MODERATE_THRESHOLD`, and `QUALITY_WEAK_THRESHOLD`. Timeliness defaults are 5/15/60 minutes through `QUALITY_TIMELINESS_EXCELLENT_MINUTES`, `QUALITY_TIMELINESS_GOOD_MINUTES`, and `QUALITY_TIMELINESS_WEAK_MINUTES`. `QUALITY_CHANGE_SIGNIFICANCE` defaults to `5`.

## Observability And Deployment

| Variable | Purpose | Default |
| --- | --- | --- |
| `LOG_FORMAT` | `text` or structured `json` | `text` |
| `LOG_LEVEL` | Python log level | `INFO` |
| `LOG_INCLUDE_TIMESTAMP` | Include timestamps in text logs | `true` |
| `SLOW_SOURCE_FETCH_MS` | Slow source warning | `5000` |
| `SLOW_AI_REQUEST_MS` | Slow AI warning | `15000` |
| `SLOW_DATABASE_QUERY_MS` | Slow query warning | `500` |
| `SLOW_EVENT_HANDLER_MS` | Slow subscriber warning | `2000` |
| `SLOW_TELEGRAM_SEND_MS` | Slow send warning | `3000` |
| `OBSERVABILITY_SNAPSHOT_ENABLED` | Persist operational snapshots | `true` |
| `OBSERVABILITY_SNAPSHOT_INTERVAL_MINUTES` | Snapshot cadence | `15` |
| `OBSERVABILITY_SNAPSHOT_RETENTION_DAYS` | Snapshot retention | `30` |
| `DASHBOARD_HOST` | CLI dashboard bind address | `127.0.0.1` |
| `DASHBOARD_PORT` | Container/dashboard port | `8000` |
| `HOST_PORT` | Compose host port mapping | `8000` |
| `BIND_ADDRESS` | Compose host bind address | `127.0.0.1` |
| `TRACKER_ENABLED` | Run background tracker in production entrypoint | `true` |
| `TRACKER_SHUTDOWN_TIMEOUT_SECONDS` | Graceful tracker shutdown timeout | `30` |

All booleans accept conventional true/false values. Invalid numeric ranges and unsupported providers fail at startup with the variable name in the error.
