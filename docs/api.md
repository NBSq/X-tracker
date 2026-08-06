# REST API

Start the service with `python -m app.main --dashboard` and open `/docs` for FastAPI's generated OpenAPI UI. JSON errors use FastAPI's standard `{"detail": ...}` shape. Invalid input returns `400` or `422`; unknown records return `404`; unexpected failures return `500`. The API has no built-in authentication, so bind to localhost or put it behind an authenticated reverse proxy.

## System

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/live` | Process liveness |
| GET | `/ready` | Database/config readiness; returns `503` when not ready |
| GET | `/health` | Detailed component health |
| GET | `/metrics` | Prometheus text exposition |
| GET | `/api/status` | Dashboard status summary |
| GET | `/api/system/health` | Structured health report |
| GET | `/api/system/performance` | Operational performance summary |
| GET | `/api/system/metrics-summary` | JSON metrics snapshot |
| GET | `/api/system/version` | Canonical version and runtime information |

## Signals, AI, And Performance

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/signals` | Latest saved signals |
| GET | `/api/signals/{signal_id}/analysis` | Stored reasoning for a signal |
| POST | `/api/signals/{signal_id}/analysis` | Analyze or refresh a saved signal |
| GET | `/api/ai/status` | Provider and fallback state |
| GET | `/api/ai/usage` | AI request audit/usage summary |
| GET | `/api/ai/analyses` | Recent stored analyses |
| GET | `/api/performance` | Signal performance aggregate |
| GET | `/api/outcomes` | Filtered evaluated outcomes |
| GET | `/api/outcomes/summary` | Outcome totals, averages, and narrative rankings |
| GET | `/api/outcomes/{signal_id}` | Outcomes for one signal |
| GET | `/api/narratives` | Ranked narrative metrics |
| GET | `/api/tokens` | Ranked token metrics |

`/api/outcomes` accepts `status`, `evaluation_window_hours`, `token`, `narrative`, and `limit`. Signal and analysis list endpoints accept their documented pagination/limit filters in OpenAPI.

## Historical Analytics

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/history/summary` | Period summary |
| GET | `/api/history/timeline` | Time-bucket series |
| GET | `/api/history/narratives` | Narrative comparison |
| GET | `/api/history/tokens` | Token comparison |
| GET | `/api/history/narratives/{name}` | One narrative history |
| GET | `/api/history/tokens/{symbol}` | One token history |

Historical endpoints use the API's `period` convention (`7d`, `30d`, `90d`, or `all`) where exposed.

## Rules And Watchlists

| Method | Path | Purpose |
| --- | --- | --- |
| GET, POST | `/api/rules` | List or create rules |
| PUT, DELETE | `/api/rules/{rule_id}` | Replace or delete a rule |
| GET, POST | `/api/watchlists` | List or create watchlists |
| GET, PUT, DELETE | `/api/watchlists/{watchlist_id}` | Read, update, or delete one watchlist |
| POST | `/api/watchlists/{watchlist_id}/items` | Add a token or narrative |
| DELETE | `/api/watchlists/{watchlist_id}/items/{item_id}` | Remove an item |
| GET | `/api/watchlists/{watchlist_id}/signals` | Associated signals |
| GET | `/api/watchlists/{watchlist_id}/performance` | Focused performance |
| GET | `/api/watchlists/{watchlist_id}/history` | Watchlist history |

Create/update bodies are JSON and validated by the same rule/watchlist services used by the CLI. Rule conditions and actions retain their structured JSON form.

## Sources And Deduplication

| Method | Path | Purpose |
| --- | --- | --- |
| GET, POST | `/api/sources` | List or create content sources |
| GET, PUT, DELETE | `/api/sources/{source_id}` | Read, update, or delete a source |
| POST | `/api/sources/{source_id}/fetch` | Fetch one source now |
| POST | `/api/sources/{source_id}/enable` | Enable a source |
| POST | `/api/sources/{source_id}/disable` | Disable a source |
| GET | `/api/unified-events` | Filter/list canonical events |
| GET | `/api/unified-events/{event_id}` | Event detail |
| GET | `/api/unified-events/{event_id}/items` | Supporting source records |
| GET | `/api/unified-events/{event_id}/history` | Material-change history |
| GET | `/api/deduplication/stats` | Deduplication statistics |
| POST | `/api/deduplication/rebuild` | Rebuild derived event grouping |

Unified-event filters include source, token, narrative, date range, sort, and limit values exposed by OpenAPI.

## Relationship Graph

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/graph` | Filtered graph payload |
| GET | `/api/graph/nodes` | Graph nodes |
| GET | `/api/graph/nodes/{node_type}/{entity_id}` | Node detail |
| GET | `/api/graph/edges` | Graph edges |
| GET | `/api/graph/summary` | Graph totals and leaders |
| GET | `/api/graph/emerging` | Emerging relationships |
| GET | `/api/graph/bridges` | Bridge entities |
| GET, POST | `/api/graph/snapshots` | List or create snapshots |
| POST | `/api/graph/rebuild` | Rebuild the derived projection |
| GET | `/api/graph/validate` | Integrity validation |

Common graph filters include period, node/relationship type, minimum weight/occurrences, search, watchlist, and limit.

## Signal Quality

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/quality/summary` | Quality distribution and coverage |
| GET | `/api/quality/signals` | Filtered signal scores |
| GET | `/api/quality/signals/{signal_id}` | Explainable score detail |
| GET | `/api/quality/sources` | Source quality aggregates |
| GET | `/api/quality/rules` | Rule quality aggregates |
| GET | `/api/quality/watchlists` | Watchlist quality aggregates |
| GET | `/api/quality/narratives` | Narrative quality aggregates |
| GET | `/api/quality/tokens` | Token quality aggregates |
| GET | `/api/quality/ai` | AI agreement metrics |
| GET | `/api/quality/recommendations` | Quality recommendations |
| PUT | `/api/quality/recommendations/{recommendation_id}` | Update recommendation status |
| POST | `/api/quality/recalculate` | Recompute versioned scores |
| GET | `/api/quality/validate` | Validate quality records |

Quality lists support period, entity, score band, calculation version, and limit filters where applicable.

## HTML Pages

The Jinja2 dashboard serves `/`, `/signals`, `/performance`, `/history`, `/outcomes`, `/narratives`, `/tokens`, `/rules`, `/watchlists`, `/sources`, `/unified-events`, `/deduplication`, `/graph`, `/quality`, `/ai`, and `/system/*`, plus their documented detail pages. HTML pages poll persisted data; they are not a separate API implementation.
