# Saved Searches

Saved searches persist validated analytics filters without storing SQL. `SearchService` normalizes each definition, rejects unknown fields, delegates to the existing SQLite repositories or graph service, applies an allowlisted sort, enforces the configured result limit, and records execution metadata only for real runs. Preview does not increment `run_count`.

## Targets

| Target | Data source |
| --- | --- |
| `signals` | Saved signals with latest outcome, quality, AI, event, rule, and watchlist data |
| `quality_signals` | The same signal query, requiring a persisted quality score |
| `unified_events` | Canonical multi-source events and supporting source records |
| `narratives` | Aggregated saved-signal narrative metrics |
| `tokens` | Aggregated saved-signal token metrics |
| `graph_relationships` | Existing emerging-relationship and bridge calculations from `GraphService` |

## Filters

| Area | Allowlisted filters |
| --- | --- |
| Identity | `token`, `narrative` |
| Associations | `watchlist`, `rule`, `source` (one name or a list of up to 20 names) |
| Scores | `hype_min`, `hype_max`, `momentum_min`, `momentum_max`, `confidence_min`, `confidence_max` |
| Evidence | `source_count_min`, `item_count_min`, `minimum_evidence_count` |
| Quality | `quality_min`, `quality_max`, `quality_classification` |
| AI | `ai_provider`, `ai_action`, `ai_risk_level` |
| Evaluation | `outcome` |
| Time | `date_from`, `date_to` using `YYYY-MM-DD` |
| Events | `conflict_status`: `has_conflicts`, `requires_review`, or `clear` |
| Graph | `graph_emerging_status`, `emerging_score_min`, `bridge_score_min` |

Filters are target-specific. Unsupported combinations return validation errors. Score ranges are bounded, minimum values cannot exceed maximums, date ranges are ordered and bounded, and result limits cannot exceed `REPORT_MAX_RESULTS`.

```json
{
  "name": "High Quality AI Signals",
  "target_type": "quality_signals",
  "filters": {
    "narrative": "AI agents",
    "watchlist": "AI Narratives",
    "quality_min": 80,
    "source_count_min": 3
  },
  "sort_by": "quality_score",
  "sort_direction": "desc",
  "result_limit": 25
}
```

## Sorting And Limits

Sort fields depend on the target. Signal targets support creation time, hype, momentum, confidence, mentions, quality, source count, and evidence count. Events support creation time, scores, source count, and item count. Entity aggregates support name, creation time, mentions, and scores. Graph relationships support emerging score, bridge score, weight, occurrences, and recency.

## Dashboard, API, And CLI

Dashboard pages are `/saved-searches`, `/saved-searches/new`, `/saved-searches/{id}`, and `/saved-searches/{id}/edit`.

REST endpoints:

- `GET/POST /api/saved-searches`
- `GET/PUT/DELETE /api/saved-searches/{id}`
- `POST /api/saved-searches/{id}/preview`
- `POST /api/saved-searches/{id}/run`

CLI commands:

```powershell
python -m app.main --list-saved-searches
python -m app.main --saved-search-details 1
python -m app.main --run-saved-search 1
python -m app.main --delete-saved-search 1
```

Creation and editing are dashboard/API-first to keep complex filter definitions out of command-line JSON.
