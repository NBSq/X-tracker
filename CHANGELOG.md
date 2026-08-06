# Changelog

All notable changes to `x-narrative-tracker` are documented here. The project follows semantic versioning from the first stable release onward.

## [1.0.0] - 2026-08-06

### Added

- RSS, Atom, local fixture, and X API v2-compatible ingestion using a shared post model.
- Multi-source normalization, exact and near-duplicate detection, and unified event timelines.
- Deterministic mock AI plus optional OpenAI analysis with structured results, caching, limits, and fallback.
- Hype, Narrative Momentum, historical analytics, signal outcomes, and performance reporting.
- Typed internal Event Bus, smart alert rules, token and narrative watchlists, and focused alert actions.
- Narrative relationship graph with snapshots, emerging relationships, bridge analysis, and CSV exports.
- Versioned signal quality analytics, entity aggregates, recommendations, and quality-aware rules.
- HTML-safe Telegram alerts, summaries, digests, performance commands, and source health notifications.
- FastAPI/Jinja2 dashboard and REST API for signals, analytics, sources, events, rules, watchlists, graph, quality, and system health.
- Structured logging, correlation IDs, Prometheus metrics, health/readiness endpoints, runtime diagnostics, and coarse observability snapshots.
- Non-root Docker deployment with persistent SQLite storage, graceful tracker lifecycle, health checks, and CI smoke validation.

### Changed

- Declared the implemented and tested feature set stable for v1.0.0.
- Centralized the application version in `app/version.py`.
- Enabled SQLite WAL mode, foreign keys, and a bounded busy timeout for dashboard/tracker overlap.
- Expanded public documentation for architecture, CLI, API, configuration, deployment, demonstration, troubleshooting, and releases.

### Fixed

- Prevented duplicate post importance from inflating merged token/narrative hype alerts.
- Preserved RSS sentence punctuation and escaped dynamic Telegram HTML safely.
- Normalized user-facing hype scores to a bounded 0-100 scale while retaining raw internal values.
- Prevented repeated signal outcome evaluations for the same evaluation window.

### Security

- Redacted credentials and authorization values from structured logs.
- Kept secrets and local state out of Git and Docker build contexts.
- Hardened the Docker service with a non-root user, dropped capabilities, read-only root filesystem, and localhost binding by default.

## v0.3.0 - Narrative Momentum and Normalized Scores

- Added Narrative Momentum scoring.
- Added daily momentum snapshots and history reports.
- Added top opportunities report.
- Added normalized display hype scores from 0 to 100.
- Improved duplicate-alert merging for token and narrative spikes.
- Expanded mock AI token and narrative classification.

## v0.2.0 - RSS and Telegram Alerts

- Added RSS and Atom source ingestion.
- Added Telegram spike alerts, summaries, trend reports, and daily digests.
- Added mock AI mode for OpenAI-free development.
- Added Windows Task Scheduler scripts.

## v0.1.0 - Initial MVP

- Added local sample-post MVP.
- Added X API v2-compatible source client.
- Added OpenAI structured analysis.
- Added SQLite persistence.
- Added hype scoring and threshold alerts.
