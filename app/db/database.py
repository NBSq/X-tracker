from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.ai.analyzer import AnalysisResult
from app.sources.x_client import XPost


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout = 30000")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA foreign_keys = ON")

    def initialize(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS analyzed_posts (
                post_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                text TEXT NOT NULL,
                url TEXT NOT NULL,
                posted_at TEXT,
                tokens_json TEXT NOT NULL,
                narratives_json TEXT NOT NULL,
                sentiment TEXT NOT NULL,
                importance INTEGER NOT NULL,
                summary TEXT NOT NULL,
                analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                hype_score REAL NOT NULL,
                mentions_count INTEGER NOT NULL,
                average_importance REAL NOT NULL,
                sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS narrative_score_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                narrative TEXT NOT NULL,
                hype_score REAL NOT NULL,
                mentions_count INTEGER NOT NULL,
                average_importance REAL NOT NULL,
                recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_narrative_history_recorded_at
            ON narrative_score_history(recorded_at);

            CREATE TABLE IF NOT EXISTS daily_momentum (
                date TEXT NOT NULL,
                narrative TEXT NOT NULL,
                momentum_score INTEGER NOT NULL,
                PRIMARY KEY (date, narrative)
            );

            CREATE TABLE IF NOT EXISTS signal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                signal_type TEXT NOT NULL,
                token TEXT,
                narrative TEXT,
                hype_score REAL NOT NULL,
                momentum_score REAL NOT NULL,
                confidence INTEGER NOT NULL,
                action TEXT NOT NULL,
                mentions_count INTEGER
            );

            CREATE TABLE IF NOT EXISTS signal_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                evaluated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                hours_after INTEGER NOT NULL,
                evaluation_window_hours INTEGER NOT NULL,
                status TEXT NOT NULL CHECK(status IN ('SUCCESS', 'NEUTRAL', 'FAILED')),
                score_change REAL NOT NULL,
                original_hype_score REAL NOT NULL,
                current_hype_score REAL NOT NULL,
                hype_change REAL NOT NULL,
                original_momentum_score REAL NOT NULL,
                current_momentum_score REAL NOT NULL,
                mentions_change INTEGER NOT NULL,
                momentum_change REAL NOT NULL,
                original_mentions INTEGER NOT NULL,
                current_mentions INTEGER NOT NULL,
                notes TEXT NOT NULL,
                FOREIGN KEY (signal_id) REFERENCES signal_history(id),
                UNIQUE (signal_id, hours_after),
                UNIQUE (signal_id, evaluation_window_hours)
            );

            CREATE TABLE IF NOT EXISTS alert_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                priority INTEGER NOT NULL DEFAULT 0,
                condition TEXT NOT NULL,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_triggered TEXT,
                trigger_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS signal_rule_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                rule_id INTEGER NOT NULL,
                triggered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                actions_json TEXT NOT NULL,
                high_priority INTEGER NOT NULL DEFAULT 0,
                dashboard_highlight INTEGER NOT NULL DEFAULT 0,
                include_in_digest INTEGER NOT NULL DEFAULT 0,
                csv_export_marker INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (signal_id) REFERENCES signal_history(id),
                FOREIGN KEY (rule_id) REFERENCES alert_rules(id),
                UNIQUE (signal_id, rule_id)
            );

            CREATE TABLE IF NOT EXISTS watchlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                priority INTEGER NOT NULL DEFAULT 0 CHECK(priority BETWEEN 0 AND 100),
                minimum_hype_score REAL NOT NULL DEFAULT 0 CHECK(minimum_hype_score BETWEEN 0 AND 100),
                minimum_momentum_score REAL NOT NULL DEFAULT 0 CHECK(minimum_momentum_score BETWEEN 0 AND 100),
                minimum_confidence INTEGER NOT NULL DEFAULT 0 CHECK(minimum_confidence BETWEEN 0 AND 10),
                telegram_enabled INTEGER NOT NULL DEFAULT 1 CHECK(telegram_enabled IN (0, 1)),
                include_in_digest INTEGER NOT NULL DEFAULT 0 CHECK(include_in_digest IN (0, 1)),
                dashboard_highlight INTEGER NOT NULL DEFAULT 1 CHECK(dashboard_highlight IN (0, 1)),
                case_insensitive INTEGER NOT NULL DEFAULT 1 CHECK(case_insensitive IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS watchlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                watchlist_id INTEGER NOT NULL,
                item_type TEXT NOT NULL CHECK(item_type IN ('token', 'narrative')),
                item_value TEXT NOT NULL,
                normalized_value TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (watchlist_id) REFERENCES watchlists(id),
                UNIQUE (watchlist_id, item_type, normalized_value)
            );

            CREATE TABLE IF NOT EXISTS signal_watchlists (
                signal_id INTEGER NOT NULL,
                watchlist_id INTEGER NOT NULL,
                matched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                matched_item_type TEXT NOT NULL CHECK(matched_item_type IN ('token', 'narrative')),
                matched_item_value TEXT NOT NULL,
                FOREIGN KEY (signal_id) REFERENCES signal_history(id),
                FOREIGN KEY (watchlist_id) REFERENCES watchlists(id),
                UNIQUE (signal_id, watchlist_id, matched_item_type, matched_item_value)
            );

            CREATE TABLE IF NOT EXISTS ai_analysis_cache (
                cache_key TEXT PRIMARY KEY,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS ai_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                requested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                signal_id INTEGER,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                success INTEGER NOT NULL CHECK(success IN (0, 1)),
                fallback_used INTEGER NOT NULL DEFAULT 0 CHECK(fallback_used IN (0, 1)),
                cached INTEGER NOT NULL DEFAULT 0 CHECK(cached IN (0, 1)),
                input_size_estimate INTEGER NOT NULL DEFAULT 0,
                output_size_estimate INTEGER NOT NULL DEFAULT 0,
                latency_ms INTEGER NOT NULL DEFAULT 0,
                error_type TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                FOREIGN KEY (signal_id) REFERENCES signal_history(id)
            );

            CREATE TABLE IF NOT EXISTS signal_ai_analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                summary TEXT NOT NULL,
                why_it_matters TEXT NOT NULL,
                action TEXT NOT NULL,
                confidence INTEGER NOT NULL,
                risk_level TEXT NOT NULL,
                supporting_factors_json TEXT NOT NULL,
                risk_factors_json TEXT NOT NULL,
                related_tokens_json TEXT NOT NULL,
                related_narratives_json TEXT NOT NULL,
                market_context TEXT NOT NULL,
                invalidation_conditions_json TEXT NOT NULL,
                cached INTEGER NOT NULL DEFAULT 0 CHECK(cached IN (0, 1)),
                fallback_used INTEGER NOT NULL DEFAULT 0 CHECK(fallback_used IN (0, 1)),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (signal_id) REFERENCES signal_history(id),
                UNIQUE (signal_id, provider, model, prompt_version)
            );

            CREATE TABLE IF NOT EXISTS content_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_key TEXT NOT NULL COLLATE NOCASE UNIQUE,
                name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                url TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
                priority INTEGER NOT NULL DEFAULT 5,
                categories_json TEXT NOT NULL DEFAULT '[]',
                fetch_interval_seconds INTEGER NOT NULL DEFAULT 300,
                last_fetch_at TEXT,
                last_success_at TEXT,
                last_error_at TEXT,
                last_error_type TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                successful_fetches INTEGER NOT NULL DEFAULT 0,
                failed_fetches INTEGER NOT NULL DEFAULT 0,
                empty_fetches INTEGER NOT NULL DEFAULT 0,
                total_fetch_latency_ms INTEGER NOT NULL DEFAULT 0,
                total_items_fetched INTEGER NOT NULL DEFAULT 0,
                total_items_accepted INTEGER NOT NULL DEFAULT 0,
                total_items_deduplicated INTEGER NOT NULL DEFAULT 0,
                last_failure_alert_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS content_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                external_id TEXT NOT NULL,
                canonical_url TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL,
                body TEXT NOT NULL,
                author TEXT NOT NULL DEFAULT '',
                published_at TEXT,
                fetched_at TEXT NOT NULL,
                content_hash TEXT NOT NULL,
                normalized_title TEXT NOT NULL,
                language TEXT NOT NULL DEFAULT 'unknown',
                tokens_json TEXT NOT NULL DEFAULT '[]',
                narratives_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'accepted',
                duplicate_reason TEXT,
                duplicate_of_content_item_id INTEGER,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_id) REFERENCES content_sources(id),
                FOREIGN KEY (duplicate_of_content_item_id) REFERENCES content_items(id)
            );

            CREATE TABLE IF NOT EXISTS unified_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                primary_content_item_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                token TEXT,
                narrative TEXT,
                tokens_json TEXT NOT NULL DEFAULT '[]',
                narratives_json TEXT NOT NULL DEFAULT '[]',
                detected_conflicts_json TEXT NOT NULL DEFAULT '[]',
                conflict_count INTEGER NOT NULL DEFAULT 0,
                requires_review INTEGER NOT NULL DEFAULT 0,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                source_count INTEGER NOT NULL DEFAULT 1,
                item_count INTEGER NOT NULL DEFAULT 1,
                duplicate_count INTEGER NOT NULL DEFAULT 0,
                highest_source_priority INTEGER NOT NULL DEFAULT 0,
                hype_score REAL NOT NULL DEFAULT 0,
                momentum_score REAL NOT NULL DEFAULT 0,
                confidence REAL NOT NULL DEFAULT 0,
                material_version INTEGER NOT NULL DEFAULT 1,
                last_material_update_at TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (primary_content_item_id) REFERENCES content_items(id)
            );

            CREATE TABLE IF NOT EXISTS unified_event_items (
                unified_event_id INTEGER NOT NULL,
                content_item_id INTEGER NOT NULL,
                similarity_score REAL NOT NULL DEFAULT 1.0,
                match_reason TEXT NOT NULL,
                added_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (unified_event_id) REFERENCES unified_events(id),
                FOREIGN KEY (content_item_id) REFERENCES content_items(id),
                UNIQUE (unified_event_id, content_item_id)
            );

            CREATE TABLE IF NOT EXISTS unified_event_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                unified_event_id INTEGER NOT NULL,
                changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                change_type TEXT NOT NULL,
                source_count INTEGER NOT NULL,
                item_count INTEGER NOT NULL,
                hype_score REAL NOT NULL DEFAULT 0,
                momentum_score REAL NOT NULL DEFAULT 0,
                details_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY (unified_event_id) REFERENCES unified_events(id)
            );

            CREATE TABLE IF NOT EXISTS source_fetch_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                success INTEGER NOT NULL CHECK(success IN (0, 1)),
                empty INTEGER NOT NULL DEFAULT 0 CHECK(empty IN (0, 1)),
                item_count INTEGER NOT NULL DEFAULT 0,
                accepted_count INTEGER NOT NULL DEFAULT 0,
                duplicate_count INTEGER NOT NULL DEFAULT 0,
                duration_ms INTEGER NOT NULL DEFAULT 0,
                error_type TEXT,
                FOREIGN KEY (source_id) REFERENCES content_sources(id)
            );

            CREATE TABLE IF NOT EXISTS graph_nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                node_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                label TEXT NOT NULL,
                normalized_label TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 0 CHECK(weight BETWEEN 0 AND 1),
                activity_score REAL NOT NULL DEFAULT 0 CHECK(activity_score BETWEEN 0 AND 100),
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (node_type, entity_id)
            );

            CREATE TABLE IF NOT EXISTS graph_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_node_id INTEGER NOT NULL,
                target_node_id INTEGER NOT NULL,
                edge_type TEXT NOT NULL,
                derivation TEXT NOT NULL DEFAULT 'observed',
                weight REAL NOT NULL DEFAULT 0 CHECK(weight BETWEEN 0 AND 1),
                occurrence_count INTEGER NOT NULL DEFAULT 1 CHECK(occurrence_count > 0),
                confidence REAL NOT NULL DEFAULT 1 CHECK(confidence BETWEEN 0 AND 1),
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (source_node_id) REFERENCES graph_nodes(id),
                FOREIGN KEY (target_node_id) REFERENCES graph_nodes(id),
                UNIQUE (source_node_id, target_node_id, edge_type, derivation)
            );

            CREATE TABLE IF NOT EXISTS graph_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                node_count INTEGER NOT NULL,
                edge_count INTEGER NOT NULL,
                metrics_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE (period_start, period_end)
            );

            CREATE TABLE IF NOT EXISTS signal_quality_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                quality_score REAL NOT NULL CHECK(quality_score BETWEEN 0 AND 100),
                classification TEXT NOT NULL CHECK(classification IN (
                    'excellent', 'strong', 'moderate', 'weak', 'unreliable',
                    'insufficient_data'
                )),
                outcome_quality REAL,
                confidence_calibration REAL,
                source_reliability REAL,
                evidence_strength REAL,
                source_diversity REAL,
                timeliness REAL,
                rule_precision REAL,
                watchlist_relevance REAL,
                ai_agreement REAL,
                noise_risk REAL NOT NULL CHECK(noise_risk BETWEEN 0 AND 100),
                evaluation_coverage REAL CHECK(
                    evaluation_coverage IS NULL OR evaluation_coverage BETWEEN 0 AND 100
                ),
                evidence_count INTEGER NOT NULL DEFAULT 0,
                calculation_version INTEGER NOT NULL,
                breakdown_json TEXT NOT NULL,
                calculated_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (signal_id) REFERENCES signal_history(id),
                UNIQUE (signal_id, calculation_version)
            );

            CREATE TABLE IF NOT EXISTS quality_aggregates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                signal_count INTEGER NOT NULL DEFAULT 0,
                evaluated_count INTEGER NOT NULL DEFAULT 0,
                successful_count INTEGER NOT NULL DEFAULT 0,
                neutral_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                average_quality_score REAL,
                median_quality_score REAL,
                precision REAL CHECK(precision IS NULL OR precision BETWEEN 0 AND 100),
                noise_rate REAL CHECK(noise_rate IS NULL OR noise_rate BETWEEN 0 AND 100),
                evaluation_coverage REAL CHECK(
                    evaluation_coverage IS NULL OR evaluation_coverage BETWEEN 0 AND 100
                ),
                average_confidence REAL,
                calibration_error REAL,
                reliability_score REAL,
                calculation_version INTEGER NOT NULL,
                metrics_json TEXT NOT NULL,
                calculated_at TEXT NOT NULL,
                UNIQUE (
                    entity_type, entity_id, period_start, period_end,
                    calculation_version
                )
            );

            CREATE TABLE IF NOT EXISTS quality_recommendations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                recommendation_type TEXT NOT NULL,
                severity TEXT NOT NULL CHECK(severity IN ('low', 'medium', 'high')),
                title TEXT NOT NULL,
                description TEXT NOT NULL,
                suggested_action TEXT NOT NULL,
                confidence REAL NOT NULL CHECK(confidence BETWEEN 0 AND 100),
                minimum_sample_requirement INTEGER NOT NULL DEFAULT 0,
                evidence_json TEXT NOT NULL,
                period_key TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open' CHECK(
                    status IN ('open', 'acknowledged', 'resolved', 'dismissed')
                ),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT,
                UNIQUE (entity_type, entity_id, recommendation_type, period_key)
            );

            CREATE TABLE IF NOT EXISTS observability_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL UNIQUE,
                metrics_json TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_signal_outcomes_signal_id
            ON signal_outcomes(signal_id);

            CREATE INDEX IF NOT EXISTS idx_signal_history_timestamp
            ON signal_history(timestamp);

            CREATE INDEX IF NOT EXISTS idx_signal_history_narrative_timestamp
            ON signal_history(narrative, timestamp);

            CREATE INDEX IF NOT EXISTS idx_signal_history_token_timestamp
            ON signal_history(token, timestamp);

            CREATE INDEX IF NOT EXISTS idx_signal_outcomes_evaluated_at
            ON signal_outcomes(evaluated_at);

            CREATE INDEX IF NOT EXISTS idx_alert_rules_enabled_priority
            ON alert_rules(enabled, priority DESC);

            CREATE INDEX IF NOT EXISTS idx_signal_rule_matches_signal
            ON signal_rule_matches(signal_id);

            CREATE INDEX IF NOT EXISTS idx_signal_rule_matches_rule
            ON signal_rule_matches(rule_id);

            CREATE INDEX IF NOT EXISTS idx_watchlist_items_watchlist
            ON watchlist_items(watchlist_id, item_type, normalized_value);

            CREATE INDEX IF NOT EXISTS idx_signal_watchlists_watchlist
            ON signal_watchlists(watchlist_id, matched_at DESC);

            CREATE INDEX IF NOT EXISTS idx_signal_watchlists_signal
            ON signal_watchlists(signal_id);

            CREATE INDEX IF NOT EXISTS idx_ai_usage_requested_at
            ON ai_usage(requested_at);

            CREATE INDEX IF NOT EXISTS idx_signal_ai_analyses_signal
            ON signal_ai_analyses(signal_id, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_content_items_external_id
            ON content_items(source_id, external_id);

            CREATE INDEX IF NOT EXISTS idx_content_items_canonical_url
            ON content_items(canonical_url);

            CREATE INDEX IF NOT EXISTS idx_content_items_hash
            ON content_items(content_hash);

            CREATE INDEX IF NOT EXISTS idx_content_items_title_time
            ON content_items(normalized_title, published_at);

            CREATE INDEX IF NOT EXISTS idx_unified_event_items_content
            ON unified_event_items(content_item_id);

            CREATE INDEX IF NOT EXISTS idx_unified_events_last_seen
            ON unified_events(last_seen_at DESC);

            CREATE INDEX IF NOT EXISTS idx_source_fetch_history_source
            ON source_fetch_history(source_id, fetched_at DESC);

            CREATE INDEX IF NOT EXISTS idx_graph_nodes_type_weight
            ON graph_nodes(node_type, weight DESC);

            CREATE INDEX IF NOT EXISTS idx_graph_nodes_normalized_label
            ON graph_nodes(normalized_label);

            CREATE INDEX IF NOT EXISTS idx_graph_edges_type_weight
            ON graph_edges(edge_type, weight DESC);

            CREATE INDEX IF NOT EXISTS idx_graph_edges_source
            ON graph_edges(source_node_id);

            CREATE INDEX IF NOT EXISTS idx_graph_edges_target
            ON graph_edges(target_node_id);

            CREATE INDEX IF NOT EXISTS idx_graph_snapshots_period
            ON graph_snapshots(period_end DESC);

            CREATE INDEX IF NOT EXISTS idx_signal_quality_score
            ON signal_quality_scores(quality_score DESC, calculated_at DESC);

            CREATE INDEX IF NOT EXISTS idx_signal_quality_classification
            ON signal_quality_scores(classification, calculation_version);

            CREATE INDEX IF NOT EXISTS idx_quality_aggregates_entity_period
            ON quality_aggregates(entity_type, entity_id, period_end DESC);

            CREATE INDEX IF NOT EXISTS idx_quality_recommendations_status
            ON quality_recommendations(status, severity, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_observability_snapshots_timestamp
            ON observability_snapshots(timestamp DESC);
            """
        )
        self._add_column_if_missing("signal_history", "mentions_count", "INTEGER")
        self._add_column_if_missing("signal_history", "unified_event_id", "INTEGER")
        self._add_column_if_missing("analyzed_posts", "content_item_id", "INTEGER")
        self._add_column_if_missing("analyzed_posts", "unified_event_id", "INTEGER")
        self._migrate_signal_outcomes()
        self.connection.commit()

    def _add_column_if_missing(self, table: str, column: str, definition: str) -> None:
        columns = {
            str(row["name"])
            for row in self.connection.execute(f"PRAGMA table_info({table})")
        }
        if column not in columns:
            self.connection.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
            )

    def _migrate_signal_outcomes(self) -> None:
        columns = {
            "evaluation_window_hours": "INTEGER",
            "original_hype_score": "REAL",
            "current_hype_score": "REAL",
            "hype_change": "REAL",
            "original_momentum_score": "REAL",
            "current_momentum_score": "REAL",
            "original_mentions": "INTEGER",
            "current_mentions": "INTEGER",
        }
        for name, definition in columns.items():
            self._add_column_if_missing("signal_outcomes", name, definition)
        self.connection.executescript(
            """
            UPDATE signal_outcomes
            SET evaluation_window_hours = COALESCE(evaluation_window_hours, hours_after);

            UPDATE signal_outcomes
            SET original_hype_score = COALESCE(
                    original_hype_score,
                    (SELECT hype_score FROM signal_history WHERE id = signal_id),
                    0.0
                ),
                original_momentum_score = COALESCE(
                    original_momentum_score,
                    (SELECT momentum_score FROM signal_history WHERE id = signal_id),
                    0.0
                ),
                original_mentions = COALESCE(
                    original_mentions,
                    (SELECT mentions_count FROM signal_history WHERE id = signal_id),
                    0
                );

            UPDATE signal_outcomes
            SET hype_change = COALESCE(hype_change, score_change, 0.0),
                current_hype_score = COALESCE(
                    current_hype_score,
                    original_hype_score + COALESCE(hype_change, score_change, 0.0)
                ),
                current_momentum_score = COALESCE(
                    current_momentum_score,
                    original_momentum_score + COALESCE(momentum_change, 0.0)
                ),
                current_mentions = COALESCE(
                    current_mentions,
                    original_mentions + COALESCE(mentions_change, 0)
                );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_outcomes_window
            ON signal_outcomes(signal_id, evaluation_window_hours);
            """
        )

    def reset(self) -> None:
        self.connection.execute("DELETE FROM quality_recommendations")
        self.connection.execute("DELETE FROM quality_aggregates")
        self.connection.execute("DELETE FROM signal_quality_scores")
        self.connection.execute("DELETE FROM observability_snapshots")
        self.connection.execute("DELETE FROM graph_snapshots")
        self.connection.execute("DELETE FROM graph_edges")
        self.connection.execute("DELETE FROM graph_nodes")
        self.connection.execute("DELETE FROM unified_event_history")
        self.connection.execute("DELETE FROM unified_event_items")
        self.connection.execute("DELETE FROM unified_events")
        self.connection.execute("DELETE FROM source_fetch_history")
        self.connection.execute("DELETE FROM content_items")
        self.connection.execute(
            """
            UPDATE content_sources SET
                last_fetch_at = NULL, last_success_at = NULL,
                last_error_at = NULL, last_error_type = NULL,
                consecutive_failures = 0, successful_fetches = 0,
                failed_fetches = 0, empty_fetches = 0,
                total_fetch_latency_ms = 0, total_items_fetched = 0,
                total_items_accepted = 0, total_items_deduplicated = 0,
                last_failure_alert_at = NULL
            """
        )
        self.connection.execute("DELETE FROM signal_ai_analyses")
        self.connection.execute("DELETE FROM ai_usage")
        self.connection.execute("DELETE FROM ai_analysis_cache")
        self.connection.execute("DELETE FROM signal_watchlists")
        self.connection.execute("DELETE FROM signal_rule_matches")
        self.connection.execute("DELETE FROM signal_outcomes")
        self.connection.execute("DELETE FROM alerts")
        self.connection.execute("DELETE FROM analyzed_posts")
        self.connection.execute("DELETE FROM narrative_score_history")
        self.connection.execute("DELETE FROM daily_momentum")
        self.connection.execute("DELETE FROM signal_history")
        self.connection.execute(
            "UPDATE alert_rules SET last_triggered = NULL, trigger_count = 0"
        )
        self.connection.commit()

    def get_signal(self, signal_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM signal_history WHERE id = ?",
            (signal_id,),
        ).fetchone()

    def get_ai_cache(self, cache_key: str) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT * FROM ai_analysis_cache
            WHERE cache_key = ? AND datetime(expires_at) > CURRENT_TIMESTAMP
            """,
            (cache_key,),
        ).fetchone()

    def save_ai_cache(
        self,
        cache_key: str,
        provider: str,
        model: str,
        prompt_version: str,
        result: dict[str, Any],
        expires_at: str,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO ai_analysis_cache (
                cache_key, provider, model, prompt_version, result_json, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                provider = excluded.provider,
                model = excluded.model,
                prompt_version = excluded.prompt_version,
                result_json = excluded.result_json,
                created_at = CURRENT_TIMESTAMP,
                expires_at = excluded.expires_at
            """,
            (
                cache_key,
                provider,
                model,
                prompt_version,
                json.dumps(result, separators=(",", ":"), ensure_ascii=False),
                expires_at,
            ),
        )
        self.connection.commit()

    def clear_ai_cache(self) -> int:
        cursor = self.connection.execute("DELETE FROM ai_analysis_cache")
        self.connection.commit()
        return int(cursor.rowcount)

    def get_active_ai_cache_count(self) -> int:
        row = self.connection.execute(
            """
            SELECT COUNT(*) FROM ai_analysis_cache
            WHERE datetime(expires_at) > CURRENT_TIMESTAMP
            """
        ).fetchone()
        return int(row[0])

    def save_ai_usage(
        self,
        *,
        signal_id: int | None,
        provider: str,
        model: str,
        success: bool,
        fallback_used: bool = False,
        cached: bool = False,
        input_size_estimate: int = 0,
        output_size_estimate: int = 0,
        latency_ms: int = 0,
        error_type: str | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO ai_usage (
                signal_id, provider, model, success, fallback_used, cached,
                input_size_estimate, output_size_estimate, latency_ms, error_type,
                input_tokens, output_tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                provider,
                model,
                int(success),
                int(fallback_used),
                int(cached),
                input_size_estimate,
                output_size_estimate,
                latency_ms,
                error_type,
                input_tokens,
                output_tokens,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def count_openai_requests_today(self) -> int:
        row = self.connection.execute(
            """
            SELECT COUNT(*) FROM ai_usage
            WHERE provider = 'openai'
              AND cached = 0
              AND requested_at >= datetime('now', 'start of day')
              AND COALESCE(error_type, '') != 'local_daily_limit'
            """
        ).fetchone()
        return int(row[0])

    def get_ai_usage(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM ai_usage ORDER BY requested_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()

    def get_ai_usage_summary(self) -> sqlite3.Row:
        return self.connection.execute(
            """
            SELECT
                COUNT(*) AS requests,
                SUM(success) AS successful,
                SUM(fallback_used) AS fallbacks,
                SUM(cached) AS cache_hits,
                AVG(latency_ms) AS average_latency_ms,
                SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                SUM(COALESCE(output_tokens, 0)) AS output_tokens
            FROM ai_usage
            """
        ).fetchone()

    def save_signal_ai_analysis(
        self,
        signal_id: int,
        result: dict[str, Any],
    ) -> int | None:
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO signal_ai_analyses (
                signal_id, provider, model, prompt_version, summary,
                why_it_matters, action, confidence, risk_level,
                supporting_factors_json, risk_factors_json, related_tokens_json,
                related_narratives_json, market_context,
                invalidation_conditions_json, cached, fallback_used, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                result["provider"],
                result["model"],
                result["prompt_version"],
                result["summary"],
                result["why_it_matters"],
                result["action"],
                result["confidence"],
                result["risk_level"],
                json.dumps(result["supporting_factors"], ensure_ascii=False),
                json.dumps(result["risk_factors"], ensure_ascii=False),
                json.dumps(result["related_tokens"], ensure_ascii=False),
                json.dumps(result["related_narratives"], ensure_ascii=False),
                result["market_context"],
                json.dumps(result["invalidation_conditions"], ensure_ascii=False),
                int(bool(result.get("cached"))),
                int(bool(result.get("fallback_used"))),
                result.get("generated_at")
                or datetime.now(timezone.utc).isoformat(),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid) if cursor.rowcount else None

    def get_signal_ai_analysis(
        self,
        signal_id: int,
        provider: str | None = None,
    ) -> sqlite3.Row | None:
        provider_filter = "AND provider = ?" if provider else ""
        parameters: tuple[object, ...] = (signal_id, provider) if provider else (signal_id,)
        return self.connection.execute(
            f"""
            SELECT * FROM signal_ai_analyses
            WHERE signal_id = ? {provider_filter}
            ORDER BY created_at DESC, id DESC LIMIT 1
            """,
            parameters,
        ).fetchone()

    def get_signal_ai_analyses(
        self,
        limit: int = 100,
        provider: str | None = None,
    ) -> list[sqlite3.Row]:
        provider_filter = "WHERE analysis.provider = ?" if provider else ""
        parameters: list[object] = [provider] if provider else []
        parameters.append(limit)
        return self.connection.execute(
            f"""
            SELECT analysis.*, signal.token, signal.narrative,
                   signal.hype_score, signal.momentum_score,
                   usage.latency_ms
            FROM signal_ai_analyses AS analysis
            JOIN signal_history AS signal ON signal.id = analysis.signal_id
            LEFT JOIN ai_usage AS usage ON usage.id = (
                SELECT candidate.id FROM ai_usage AS candidate
                WHERE candidate.signal_id = analysis.signal_id
                  AND candidate.provider = analysis.provider
                  AND candidate.model = analysis.model
                ORDER BY candidate.id DESC LIMIT 1
            )
            {provider_filter}
            ORDER BY analysis.created_at DESC, analysis.id DESC LIMIT ?
            """,
            parameters,
        ).fetchall()

    def upsert_content_source(self, definition) -> int:
        self.connection.execute(
            """
            INSERT INTO content_sources (
                source_key, name, source_type, url, enabled, priority,
                categories_json, fetch_interval_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_key) DO UPDATE SET
                name = excluded.name,
                source_type = excluded.source_type,
                url = excluded.url,
                enabled = excluded.enabled,
                priority = excluded.priority,
                categories_json = excluded.categories_json,
                fetch_interval_seconds = excluded.fetch_interval_seconds,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                definition.source_key,
                definition.name,
                definition.source_type,
                definition.url,
                int(definition.enabled),
                definition.priority,
                json.dumps(definition.categories, ensure_ascii=False),
                definition.fetch_interval_seconds,
            ),
        )
        self.connection.commit()
        row = self.get_content_source(definition.source_key)
        if row is None:
            raise RuntimeError("Content source could not be persisted")
        return int(row["id"])

    def get_content_sources(self, enabled: bool | None = None) -> list[sqlite3.Row]:
        where = "WHERE enabled = ?" if enabled is not None else ""
        parameters = (int(enabled),) if enabled is not None else ()
        return self.connection.execute(
            f"""
            SELECT *, CASE WHEN successful_fetches + failed_fetches > 0
                THEN 100.0 * successful_fetches / (successful_fetches + failed_fetches)
                ELSE 0.0 END AS success_rate,
                CASE WHEN successful_fetches + failed_fetches > 0
                THEN 1.0 * total_fetch_latency_ms / (successful_fetches + failed_fetches)
                ELSE 0.0 END AS average_latency_ms
            FROM content_sources {where}
            ORDER BY priority DESC, name COLLATE NOCASE
            """,
            parameters,
        ).fetchall()

    def get_content_source(self, identifier: int | str) -> sqlite3.Row | None:
        if isinstance(identifier, int):
            return self.connection.execute(
                "SELECT * FROM content_sources WHERE id = ?", (identifier,)
            ).fetchone()
        return self.connection.execute(
            "SELECT * FROM content_sources WHERE source_key = ? COLLATE NOCASE",
            (str(identifier),),
        ).fetchone()

    def update_content_source(self, identifier: int | str, **changes) -> bool:
        source = self.get_content_source(identifier)
        if source is None:
            return False
        allowed = {
            "name", "source_type", "url", "enabled", "priority",
            "categories_json", "fetch_interval_seconds",
        }
        assignments = []
        parameters: list[object] = []
        for key, value in changes.items():
            if key not in allowed:
                raise ValueError(f"Unsupported source field: {key}")
            if key == "enabled":
                value = int(bool(value))
            assignments.append(f"{key} = ?")
            parameters.append(value)
        if not assignments:
            return True
        assignments.append("updated_at = CURRENT_TIMESTAMP")
        parameters.append(int(source["id"]))
        cursor = self.connection.execute(
            f"UPDATE content_sources SET {', '.join(assignments)} WHERE id = ?",
            parameters,
        )
        self.connection.commit()
        return bool(cursor.rowcount)

    def delete_content_source(self, identifier: int | str) -> bool:
        source = self.get_content_source(identifier)
        if source is None:
            return False
        count = self.connection.execute(
            "SELECT COUNT(*) FROM content_items WHERE source_id = ?",
            (source["id"],),
        ).fetchone()[0]
        if count:
            return False
        cursor = self.connection.execute(
            "DELETE FROM content_sources WHERE id = ?", (source["id"],)
        )
        self.connection.commit()
        return bool(cursor.rowcount)

    def record_source_fetch(
        self,
        source_id: int,
        *,
        success: bool,
        item_count: int,
        accepted_count: int,
        duplicate_count: int,
        duration_ms: int,
        error_type: str | None = None,
    ) -> bool:
        source = self.get_content_source(source_id)
        was_failing = bool(source and int(source["consecutive_failures"] or 0) > 0)
        empty = success and item_count == 0
        self.connection.execute(
            """
            INSERT INTO source_fetch_history (
                source_id, success, empty, item_count, accepted_count,
                duplicate_count, duration_ms, error_type
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id, int(success), int(empty), item_count, accepted_count,
                duplicate_count, duration_ms, error_type,
            ),
        )
        if success:
            self.connection.execute(
                """
                UPDATE content_sources SET
                    last_fetch_at = CURRENT_TIMESTAMP,
                    last_success_at = CURRENT_TIMESTAMP,
                    last_error_type = NULL,
                    last_failure_alert_at = NULL,
                    consecutive_failures = 0,
                    successful_fetches = successful_fetches + 1,
                    empty_fetches = empty_fetches + ?,
                    total_fetch_latency_ms = total_fetch_latency_ms + ?,
                    total_items_fetched = total_items_fetched + ?,
                    total_items_accepted = total_items_accepted + ?,
                    total_items_deduplicated = total_items_deduplicated + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    int(empty), duration_ms, item_count, accepted_count,
                    duplicate_count, source_id,
                ),
            )
        else:
            self.connection.execute(
                """
                UPDATE content_sources SET
                    last_fetch_at = CURRENT_TIMESTAMP,
                    last_error_at = CURRENT_TIMESTAMP,
                    last_error_type = ?,
                    consecutive_failures = consecutive_failures + 1,
                    failed_fetches = failed_fetches + 1,
                    total_fetch_latency_ms = total_fetch_latency_ms + ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (error_type, duration_ms, source_id),
            )
        self.connection.commit()
        return was_failing and success

    def mark_source_failure_alert(self, source_id: int) -> None:
        self.connection.execute(
            """
            UPDATE content_sources
            SET last_failure_alert_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (source_id,),
        )
        self.connection.commit()

    def save_content_item(
        self,
        source_id: int,
        item,
        *,
        status: str,
        duplicate_reason: str | None = None,
        duplicate_of_content_item_id: int | None = None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO content_items (
                source_id, external_id, canonical_url, title, body, author,
                published_at, fetched_at, content_hash, normalized_title,
                language, tokens_json, narratives_json, metadata_json, status,
                duplicate_reason, duplicate_of_content_item_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id, item.external_id, item.canonical_url, item.title,
                item.body, item.author, item.published_at, item.fetched_at,
                item.content_hash, item.normalized_title, item.language,
                json.dumps(item.tokens, ensure_ascii=False),
                json.dumps(item.narratives, ensure_ascii=False),
                json.dumps(item.metadata, ensure_ascii=False), status,
                duplicate_reason, duplicate_of_content_item_id,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def get_content_item(self, item_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT item.*, source.source_key, source.name AS source_name,
                   source.source_type, source.priority AS source_priority
            FROM content_items AS item
            JOIN content_sources AS source ON source.id = item.source_id
            WHERE item.id = ?
            """,
            (item_id,),
        ).fetchone()

    def get_content_items(
        self,
        limit: int | None = 100,
        source_id: int | None = None,
        status: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[sqlite3.Row]:
        conditions = []
        parameters: list[object] = []
        if source_id is not None:
            conditions.append("item.source_id = ?")
            parameters.append(source_id)
        if status is not None:
            conditions.append("item.status = ?")
            parameters.append(status)
        self._append_date_filters(conditions, parameters, "item.fetched_at", from_date, to_date)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            parameters.append(limit)
        return self.connection.execute(
            f"""
            SELECT item.*, source.source_key, source.name AS source_name,
                   source.source_type, source.priority AS source_priority,
                   association.unified_event_id
            FROM content_items AS item
            JOIN content_sources AS source ON source.id = item.source_id
            LEFT JOIN unified_event_items AS association
              ON association.content_item_id = item.id
            {where}
            ORDER BY item.fetched_at DESC, item.id DESC {limit_clause}
            """,
            parameters,
        ).fetchall()

    def find_exact_content_duplicate(self, source_id: int, item) -> tuple[sqlite3.Row | None, str | None]:
        checks = [
            ("source_id = ? AND external_id = ?", (source_id, item.external_id), "same_external_id"),
            ("canonical_url = ? AND canonical_url != ''", (item.canonical_url,), "same_canonical_url"),
            ("content_hash = ?", (item.content_hash,), "same_content_hash"),
        ]
        if item.normalized_title:
            checks.append(
                (
                    "normalized_title = ? AND fetched_at >= datetime(?, '-24 hours')",
                    (item.normalized_title, item.fetched_at),
                    "same_normalized_title",
                )
            )
        for where, parameters, reason in checks:
            row = self.connection.execute(
                f"SELECT * FROM content_items WHERE {where} ORDER BY id LIMIT 1",
                parameters,
            ).fetchone()
            if row is not None:
                return row, reason
        return None, None

    def get_near_duplicate_candidates(self, fetched_at: str, hours: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT item.*, source.source_key, source.priority AS source_priority,
                   association.unified_event_id
            FROM content_items AS item
            JOIN content_sources AS source ON source.id = item.source_id
            LEFT JOIN unified_event_items AS association
              ON association.content_item_id = item.id
            WHERE item.fetched_at >= datetime(?, ?)
            ORDER BY item.id DESC
            LIMIT 500
            """,
            (fetched_at, f"-{hours} hours"),
        ).fetchall()

    def create_unified_event(self, content_item_id: int, event_key: str) -> int:
        item = self.get_content_item(content_item_id)
        if item is None:
            raise ValueError("Content item does not exist")
        seen_at = item["published_at"] or item["fetched_at"]
        cursor = self.connection.execute(
            """
            INSERT INTO unified_events (
                event_key, primary_content_item_id, title, summary,
                tokens_json, narratives_json, first_seen_at, last_seen_at,
                highest_source_priority
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_key, content_item_id, item["title"], item["body"][:500],
                item["tokens_json"], item["narratives_json"], seen_at, seen_at,
                item["source_priority"],
            ),
        )
        event_id = int(cursor.lastrowid)
        self.connection.execute(
            """
            INSERT INTO unified_event_items (
                unified_event_id, content_item_id, similarity_score, match_reason
            ) VALUES (?, ?, 1.0, 'primary')
            """,
            (event_id, content_item_id),
        )
        self.connection.execute(
            """
            INSERT INTO unified_event_history (
                unified_event_id, change_type, source_count, item_count, details_json
            ) VALUES (?, 'created', 1, 1, '{}')
            """,
            (event_id,),
        )
        self.connection.commit()
        return event_id

    def add_unified_event_item(
        self,
        event_id: int,
        content_item_id: int,
        similarity_score: float,
        match_reason: str,
    ) -> bool:
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO unified_event_items (
                unified_event_id, content_item_id, similarity_score, match_reason
            ) VALUES (?, ?, ?, ?)
            """,
            (event_id, content_item_id, similarity_score, match_reason),
        )
        self.connection.commit()
        return bool(cursor.rowcount)

    def get_unified_event(self, event_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM unified_events WHERE id = ?", (event_id,)
        ).fetchone()

    def get_unified_events(
        self,
        limit: int | None = 100,
        status: str | None = None,
    ) -> list[sqlite3.Row]:
        where = "WHERE status = ?" if status else ""
        parameters: list[object] = [status] if status else []
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            parameters.append(limit)
        return self.connection.execute(
            f"""
            SELECT event.*, source.name AS primary_source_name,
                   item.canonical_url AS primary_url,
                   item.author AS primary_author
            FROM unified_events AS event
            JOIN content_items AS item ON item.id = event.primary_content_item_id
            JOIN content_sources AS source ON source.id = item.source_id
            {where}
            ORDER BY event.last_seen_at DESC, event.id DESC {limit_clause}
            """,
            parameters,
        ).fetchall()

    def get_unified_event_items(self, event_id: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT item.*, source.source_key, source.name AS source_name,
                   source.source_type, source.priority AS source_priority,
                   association.similarity_score, association.match_reason,
                   association.added_at
            FROM unified_event_items AS association
            JOIN content_items AS item ON item.id = association.content_item_id
            JOIN content_sources AS source ON source.id = item.source_id
            WHERE association.unified_event_id = ?
            ORDER BY source.priority DESC, item.published_at, item.id
            """,
            (event_id,),
        ).fetchall()

    def get_unified_event_for_content(self, content_item_id: int) -> int | None:
        row = self.connection.execute(
            "SELECT unified_event_id FROM unified_event_items WHERE content_item_id = ?",
            (content_item_id,),
        ).fetchone()
        return int(row[0]) if row else None

    def find_unified_event_for_signal(
        self,
        token: str | None,
        narrative: str | None,
    ) -> sqlite3.Row | None:
        clauses = []
        parameters: list[object] = []
        if token:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(ue.tokens_json) "
                "WHERE value = ? COLLATE NOCASE)"
            )
            parameters.append(token)
        if narrative:
            clauses.append(
                "EXISTS (SELECT 1 FROM json_each(ue.narratives_json) "
                "WHERE value = ? COLLATE NOCASE)"
            )
            parameters.append(narrative)
        if not clauses:
            return None
        return self.connection.execute(
            f"""
            SELECT ue.* FROM unified_events AS ue
            WHERE ue.status = 'active' AND ({' OR '.join(clauses)})
            ORDER BY ue.last_seen_at DESC, ue.id DESC LIMIT 1
            """,
            parameters,
        ).fetchone()

    def get_signal_unified_event(self, signal_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT ue.* FROM unified_events AS ue
            JOIN signal_history AS signal ON signal.unified_event_id = ue.id
            WHERE signal.id = ?
            """,
            (signal_id,),
        ).fetchone()

    def get_unified_event_history(self, event_id: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT * FROM unified_event_history
            WHERE unified_event_id = ? ORDER BY changed_at DESC, id DESC
            """,
            (event_id,),
        ).fetchall()

    def archive_stale_unified_events(self, hours: int = 168) -> int:
        cursor = self.connection.execute(
            """
            UPDATE unified_events SET status = 'archived', updated_at = CURRENT_TIMESTAMP
            WHERE status = 'active' AND last_seen_at < datetime('now', ?)
            """,
            (f"-{hours} hours",),
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def update_unified_event(self, event_id: int, **changes) -> None:
        allowed = {
            "primary_content_item_id", "title", "summary", "token", "narrative",
            "tokens_json", "narratives_json", "detected_conflicts_json",
            "conflict_count", "requires_review", "first_seen_at", "last_seen_at",
            "source_count", "item_count", "duplicate_count",
            "highest_source_priority", "hype_score", "momentum_score", "confidence",
            "material_version", "last_material_update_at", "status",
        }
        assignments = []
        parameters: list[object] = []
        for key, value in changes.items():
            if key not in allowed:
                raise ValueError(f"Unsupported unified event field: {key}")
            assignments.append(f"{key} = ?")
            parameters.append(value)
        if assignments:
            assignments.append("updated_at = CURRENT_TIMESTAMP")
            parameters.append(event_id)
            self.connection.execute(
                f"UPDATE unified_events SET {', '.join(assignments)} WHERE id = ?",
                parameters,
            )
            self.connection.commit()

    def save_unified_event_history(
        self,
        event_id: int,
        change_type: str,
        details: dict,
    ) -> None:
        event = self.get_unified_event(event_id)
        if event is None:
            return
        self.connection.execute(
            """
            INSERT INTO unified_event_history (
                unified_event_id, change_type, source_count, item_count,
                hype_score, momentum_score, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, change_type, event["source_count"], event["item_count"],
                event["hype_score"], event["momentum_score"],
                json.dumps(details, ensure_ascii=False),
            ),
        )
        self.connection.commit()

    def sync_content_analysis(self, external_id: str, analysis) -> None:
        rows = self.connection.execute(
            "SELECT id FROM content_items WHERE external_id = ? ORDER BY id DESC",
            (external_id,),
        ).fetchall()
        for row in rows:
            self.connection.execute(
                "UPDATE content_items SET tokens_json = ?, narratives_json = ? WHERE id = ?",
                (
                    json.dumps(analysis.tokens, ensure_ascii=False),
                    json.dumps(analysis.narratives, ensure_ascii=False),
                    row["id"],
                ),
            )
            event_id = self.get_unified_event_for_content(int(row["id"]))
            if event_id is not None:
                self._refresh_unified_event_entities(event_id)
                self.connection.execute(
                    """
                    UPDATE analyzed_posts SET content_item_id = ?, unified_event_id = ?
                    WHERE post_id = ?
                    """,
                    (row["id"], event_id, external_id),
                )
        self.connection.commit()

    def _refresh_unified_event_entities(self, event_id: int) -> None:
        rows = self.get_unified_event_items(event_id)
        tokens = list(dict.fromkeys(
            str(item) for row in rows for item in json.loads(row["tokens_json"] or "[]")
        ))
        narratives = list(dict.fromkeys(
            str(item) for row in rows for item in json.loads(row["narratives_json"] or "[]")
        ))
        self.connection.execute(
            """
            UPDATE unified_events SET token = ?, narrative = ?, tokens_json = ?,
                narratives_json = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?
            """,
            (
                tokens[0] if tokens else None,
                narratives[0] if narratives else None,
                json.dumps(tokens, ensure_ascii=False),
                json.dumps(narratives, ensure_ascii=False),
                event_id,
            ),
        )

    def get_deduplication_stats(self, days: int = 30) -> sqlite3.Row:
        return self.connection.execute(
            """
            SELECT
                COUNT(*) AS raw_items,
                SUM(CASE WHEN status = 'accepted' THEN 1 ELSE 0 END) AS accepted_items,
                SUM(CASE WHEN status = 'exact_duplicate' THEN 1 ELSE 0 END) AS exact_duplicates,
                SUM(CASE WHEN status = 'near_duplicate' THEN 1 ELSE 0 END) AS near_duplicates,
                (SELECT COUNT(*) FROM unified_events
                 WHERE created_at >= datetime('now', ?)) AS unified_events,
                (SELECT AVG(source_count) FROM unified_events
                 WHERE created_at >= datetime('now', ?)) AS average_sources,
                (SELECT MAX(source_count) FROM unified_events
                 WHERE created_at >= datetime('now', ?)) AS maximum_sources
            FROM content_items
            WHERE fetched_at >= datetime('now', ?)
            """,
            (f"-{days} days",) * 4,
        ).fetchone()

    def get_top_duplicate_sources(self, days: int = 30, limit: int = 5) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT source.name, COUNT(*) AS duplicate_count
            FROM content_items AS item
            JOIN content_sources AS source ON source.id = item.source_id
            WHERE item.status IN ('exact_duplicate', 'near_duplicate')
              AND item.fetched_at >= datetime('now', ?)
            GROUP BY source.id ORDER BY duplicate_count DESC, source.name LIMIT ?
            """,
            (f"-{days} days", limit),
        ).fetchall()

    def get_ai_analysis_distribution(self, field: str) -> list[sqlite3.Row]:
        if field not in {"provider", "model", "action", "risk_level"}:
            raise ValueError(f"Unsupported AI distribution field: {field}")
        return self.connection.execute(
            f"""
            SELECT {field} AS name, COUNT(*) AS count
            FROM signal_ai_analyses
            GROUP BY {field}
            ORDER BY count DESC, {field}
            """
        ).fetchall()

    def has_post(self, post_id: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM analyzed_posts WHERE post_id = ?",
            (post_id,),
        ).fetchone()
        return row is not None

    def save_analysis(self, post: XPost, analysis: AnalysisResult) -> None:
        self.connection.execute(
            """
            INSERT OR IGNORE INTO analyzed_posts (
                post_id, username, text, url, posted_at, tokens_json, narratives_json,
                sentiment, importance, summary
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                post.id,
                post.username,
                post.text,
                post.url,
                post.created_at,
                json.dumps(analysis.tokens),
                json.dumps(analysis.narratives),
                analysis.sentiment,
                analysis.importance,
                analysis.summary,
            ),
        )
        self.connection.commit()

    def get_recent_signal_stats(self, lookback_minutes: int = 60) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            WITH signals AS (
                SELECT 'token' AS kind, value AS name, importance
                FROM analyzed_posts, json_each(tokens_json)
                WHERE analyzed_at >= datetime('now', ?)
                UNION ALL
                SELECT 'narrative' AS kind, value AS name, importance
                FROM analyzed_posts, json_each(narratives_json)
                WHERE analyzed_at >= datetime('now', ?)
            )
            SELECT
                kind,
                name,
                COUNT(*) AS mentions_count,
                AVG(importance) AS average_importance
            FROM signals
            WHERE name IS NOT NULL AND TRIM(name) != ''
            GROUP BY kind, name
            """,
            (f"-{lookback_minutes} minutes", f"-{lookback_minutes} minutes"),
        ).fetchall()

    def get_signal_stats_for_hours(self, lookback_hours: int = 24) -> list[sqlite3.Row]:
        return self.get_recent_signal_stats(lookback_hours * 60)

    def get_signal_posts(
        self,
        kind: str,
        name: str,
        lookback_minutes: int = 60,
        limit: int = 3,
    ) -> list[sqlite3.Row]:
        if kind not in {"token", "narrative"}:
            raise ValueError(f"Unsupported signal kind: {kind}")
        json_column = "tokens_json" if kind == "token" else "narratives_json"
        return self.connection.execute(
            f"""
            SELECT post_id, username, text, tokens_json, narratives_json, importance
            FROM analyzed_posts
            WHERE analyzed_at >= datetime('now', ?)
              AND EXISTS (
                  SELECT 1
                  FROM json_each({json_column})
                  WHERE value = ? COLLATE NOCASE
              )
            ORDER BY importance DESC, analyzed_at DESC
            LIMIT ?
            """,
            (f"-{lookback_minutes} minutes", name, limit),
        ).fetchall()

    def get_most_important_posts(
        self,
        lookback_minutes: int = 60,
        limit: int = 3,
    ) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT username, text, importance
            FROM analyzed_posts
            WHERE analyzed_at >= datetime('now', ?)
            ORDER BY importance DESC, analyzed_at DESC
            LIMIT ?
            """,
            (f"-{lookback_minutes} minutes", limit),
        ).fetchall()

    def save_narrative_score_history(self, rows: list[sqlite3.Row]) -> None:
        values = []
        for row in rows:
            if str(row["kind"]) != "narrative":
                continue
            mentions_count = int(row["mentions_count"])
            average_importance = float(row["average_importance"])
            values.append(
                (
                    str(row["name"]),
                    mentions_count * average_importance,
                    mentions_count,
                    average_importance,
                )
            )
        if values:
            self.connection.executemany(
                """
                INSERT INTO narrative_score_history (
                    narrative, hype_score, mentions_count, average_importance
                )
                VALUES (?, ?, ?, ?)
                """,
                values,
            )
            self.connection.commit()

    def get_top_narrative_history(
        self,
        lookback_hours: int,
        limit: int = 5,
    ) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT narrative, AVG(hype_score) AS score
            FROM narrative_score_history
            WHERE recorded_at >= datetime('now', ?)
            GROUP BY narrative
            ORDER BY score DESC
            LIMIT ?
            """,
            (f"-{lookback_hours} hours", limit),
        ).fetchall()

    def get_fastest_growing_narratives(self, limit: int = 5) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            WITH raw_scores AS (
                SELECT
                    narrative,
                    AVG(CASE
                        WHEN recorded_at >= datetime('now', '-24 hours')
                        THEN hype_score
                    END) AS current_score,
                    AVG(CASE
                        WHEN recorded_at >= datetime('now', '-48 hours')
                         AND recorded_at < datetime('now', '-24 hours')
                        THEN hype_score
                    END) AS previous_score
                FROM narrative_score_history
                WHERE recorded_at >= datetime('now', '-48 hours')
                GROUP BY narrative
            ),
            scores AS (
                SELECT
                    narrative,
                    COALESCE(current_score, 0.0) AS current_score,
                    COALESCE(previous_score, 0.0) AS previous_score
                FROM raw_scores
            )
            SELECT
                narrative,
                current_score,
                previous_score,
                CASE
                    WHEN previous_score > 0
                    THEN ((current_score - previous_score) / previous_score) * 100.0
                    WHEN current_score > 0
                    THEN 100.0
                    ELSE 0.0
                END AS growth_percent
            FROM scores
            WHERE current_score > 0 OR previous_score > 0
            ORDER BY growth_percent DESC, current_score DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def get_narrative_momentum_inputs(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT
                value AS narrative,
                COUNT(*) AS mentions_count,
                AVG(importance) AS average_importance,
                (julianday('now') - julianday(MAX(analyzed_at))) * 24.0 AS recency_hours
            FROM analyzed_posts, json_each(narratives_json)
            WHERE analyzed_at >= datetime('now', '-24 hours')
              AND value IS NOT NULL
              AND TRIM(value) != ''
            GROUP BY value
            """
        ).fetchall()

    def save_daily_momentum(self, scores) -> None:
        values = [(item.name, item.score) for item in scores]
        if not values:
            return
        self.connection.executemany(
            """
            INSERT INTO daily_momentum (date, narrative, momentum_score)
            VALUES (date('now'), ?, ?)
            ON CONFLICT(date, narrative)
            DO UPDATE SET momentum_score = excluded.momentum_score
            """,
            values,
        )
        self.connection.commit()

    def get_momentum_history_report(self, limit: int = 20) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT
                today.narrative,
                today.momentum_score AS today_score,
                (
                    SELECT previous.momentum_score
                    FROM daily_momentum AS previous
                    WHERE previous.narrative = today.narrative
                      AND previous.date <= date('now', '-7 days')
                    ORDER BY previous.date DESC
                    LIMIT 1
                ) AS seven_days_ago_score
            FROM daily_momentum AS today
            WHERE today.date = date('now')
            ORDER BY today.momentum_score DESC, today.narrative
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def get_opportunity_inputs(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            WITH latest_dates AS (
                SELECT narrative, MAX(date) AS latest_date
                FROM daily_momentum
                GROUP BY narrative
            )
            SELECT
                latest.narrative,
                latest.momentum_score,
                latest.date AS latest_date,
                julianday('now') - julianday(latest.date) AS recency_days,
                (
                    SELECT previous.momentum_score
                    FROM daily_momentum AS previous
                    WHERE previous.narrative = latest.narrative
                      AND previous.date <= date(latest.date, '-7 days')
                    ORDER BY previous.date DESC
                    LIMIT 1
                ) AS seven_days_ago_score
            FROM daily_momentum AS latest
            JOIN latest_dates
              ON latest.narrative = latest_dates.narrative
             AND latest.date = latest_dates.latest_date
            """
        ).fetchall()

    def alert_recently_sent(self, kind: str, name: str, lookback_minutes: int = 60) -> bool:
        row = self.connection.execute(
            """
            SELECT 1
            FROM alerts
            WHERE kind = ?
              AND name = ?
              AND sent_at >= datetime('now', ?)
            LIMIT 1
            """,
            (kind, name, f"-{lookback_minutes} minutes"),
        ).fetchone()
        return row is not None

    def save_alert(
        self,
        kind: str,
        name: str,
        hype_score: float,
        mentions_count: int,
        average_importance: float,
    ) -> None:
        self.connection.execute(
            """
            INSERT INTO alerts (
                kind, name, hype_score, mentions_count, average_importance
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (kind, name, hype_score, mentions_count, average_importance),
        )
        self.connection.commit()

    def save_signal_history(
        self,
        signal_type: str,
        token: str | None,
        narrative: str | None,
        hype_score: float,
        momentum_score: float,
        confidence: int,
        action: str,
        mentions_count: int | None = None,
        unified_event_id: int | None = None,
    ) -> int:
        self.connection.execute(
            """
            INSERT INTO signal_history (
                signal_type, token, narrative, hype_score, momentum_score,
                confidence, action, mentions_count, unified_event_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_type,
                token,
                narrative,
                hype_score,
                momentum_score,
                confidence,
                action,
                mentions_count,
                unified_event_id,
            ),
        )
        self.connection.commit()
        return int(self.connection.execute("SELECT last_insert_rowid()").fetchone()[0])

    def create_alert_rule(
        self,
        name: str,
        enabled: bool,
        priority: int,
        condition: dict,
        actions: tuple[str, ...],
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO alert_rules (name, enabled, priority, condition, action)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                name,
                int(enabled),
                int(priority),
                json.dumps(condition, separators=(",", ":"), sort_keys=True),
                json.dumps(actions, separators=(",", ":")),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def get_alert_rules(self, enabled: bool | None = None) -> list[sqlite3.Row]:
        condition = "WHERE enabled = ?" if enabled is not None else ""
        parameters = (int(enabled),) if enabled is not None else ()
        return self.connection.execute(
            f"""
            SELECT *
            FROM alert_rules
            {condition}
            ORDER BY priority DESC, name COLLATE NOCASE, id
            """,
            parameters,
        ).fetchall()

    def get_alert_rule(self, rule_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM alert_rules WHERE id = ?",
            (rule_id,),
        ).fetchone()

    def update_alert_rule(self, rule_id: int, **changes) -> bool:
        columns = {
            "name": "name",
            "enabled": "enabled",
            "priority": "priority",
            "condition": "condition",
            "action": "action",
        }
        assignments = []
        parameters: list[object] = []
        for key, value in changes.items():
            if key not in columns:
                raise ValueError(f"Unsupported alert rule column: {key}")
            if key == "enabled":
                value = int(bool(value))
            elif key == "priority":
                value = int(value)
            elif key == "condition":
                value = json.dumps(value, separators=(",", ":"), sort_keys=True)
            elif key == "action":
                value = json.dumps(value, separators=(",", ":"))
            assignments.append(f"{columns[key]} = ?")
            parameters.append(value)
        if not assignments:
            return self.get_alert_rule(rule_id) is not None
        assignments.append("updated_at = CURRENT_TIMESTAMP")
        parameters.append(rule_id)
        cursor = self.connection.execute(
            f"UPDATE alert_rules SET {', '.join(assignments)} WHERE id = ?",
            parameters,
        )
        self.connection.commit()
        return bool(cursor.rowcount)

    def delete_alert_rule(self, rule_id: int) -> bool:
        self.connection.execute(
            "DELETE FROM signal_rule_matches WHERE rule_id = ?",
            (rule_id,),
        )
        cursor = self.connection.execute(
            "DELETE FROM alert_rules WHERE id = ?",
            (rule_id,),
        )
        self.connection.commit()
        return bool(cursor.rowcount)

    def save_rule_match(
        self,
        signal_id: int,
        rule_id: int,
        actions: tuple[str, ...],
    ) -> bool:
        action_set = set(actions)
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO signal_rule_matches (
                signal_id, rule_id, actions_json, high_priority,
                dashboard_highlight, include_in_digest, csv_export_marker
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                rule_id,
                json.dumps(actions, separators=(",", ":")),
                int("high_priority" in action_set),
                int("dashboard_highlight" in action_set),
                int("include_in_digest" in action_set),
                int("csv_export_marker" in action_set),
            ),
        )
        if cursor.rowcount:
            self.connection.execute(
                """
                UPDATE alert_rules
                SET last_triggered = CURRENT_TIMESTAMP,
                    trigger_count = trigger_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (rule_id,),
            )
        self.connection.commit()
        return bool(cursor.rowcount)

    def get_rule_matches(self, signal_id: int | None = None) -> list[sqlite3.Row]:
        condition = "WHERE match.signal_id = ?" if signal_id is not None else ""
        parameters = (signal_id,) if signal_id is not None else ()
        return self.connection.execute(
            f"""
            SELECT match.*, rule.name AS rule_name, rule.priority
            FROM signal_rule_matches AS match
            JOIN alert_rules AS rule ON rule.id = match.rule_id
            {condition}
            ORDER BY match.triggered_at DESC, rule.priority DESC, match.id DESC
            """,
            parameters,
        ).fetchall()

    def find_signal_history_id(self, values: dict) -> int | None:
        row = self.connection.execute(
            """
            SELECT id
            FROM signal_history
            WHERE signal_type = ?
              AND token IS ?
              AND narrative IS ?
              AND hype_score = ?
              AND momentum_score = ?
              AND confidence = ?
              AND action = ?
              AND COALESCE(mentions_count, 0) = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (
                values["signal_type"],
                values["token"],
                values["narrative"],
                values["hype_score"],
                values["momentum_score"],
                values["confidence"],
                values["action"],
                values["mentions_count"],
            ),
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def get_entity_outcome_success_rate(
        self,
        token: str | None,
        narrative: str | None,
    ) -> float:
        row = self.connection.execute(
            """
            SELECT
                COUNT(outcome.id) AS evaluated_count,
                SUM(CASE WHEN outcome.status = 'SUCCESS' THEN 1 ELSE 0 END)
                    AS successful_count
            FROM signal_history AS signal
            JOIN signal_outcomes AS outcome ON outcome.signal_id = signal.id
            WHERE
                (? IS NOT NULL AND signal.token = ? COLLATE NOCASE)
                OR
                (? IS NOT NULL AND signal.narrative = ? COLLATE NOCASE)
            """,
            (token, token, narrative, narrative),
        ).fetchone()
        evaluated = int(row["evaluated_count"] or 0)
        successful = int(row["successful_count"] or 0)
        return successful / evaluated * 100.0 if evaluated else 0.0

    def get_entity_recent_outcomes(
        self,
        token: str | None,
        narrative: str | None,
        limit: int = 5,
    ) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT outcome.*
            FROM signal_outcomes AS outcome
            JOIN signal_history AS signal ON signal.id = outcome.signal_id
            WHERE
                (? IS NOT NULL AND signal.token = ? COLLATE NOCASE)
                OR
                (? IS NOT NULL AND signal.narrative = ? COLLATE NOCASE)
            ORDER BY outcome.evaluated_at DESC, outcome.id DESC
            LIMIT ?
            """,
            (token, token, narrative, narrative, limit),
        ).fetchall()

    def get_rule_flagged_signals(
        self,
        action: str,
        lookback_hours: int = 24,
        limit: int = 10,
    ) -> list[sqlite3.Row]:
        columns = {
            "high_priority",
            "dashboard_highlight",
            "include_in_digest",
            "csv_export_marker",
        }
        if action not in columns:
            raise ValueError(f"Unsupported rule action marker: {action}")
        return self.connection.execute(
            f"""
            SELECT DISTINCT signal.*
            FROM signal_history AS signal
            JOIN signal_rule_matches AS match ON match.signal_id = signal.id
            WHERE match.{action} = 1
              AND signal.timestamp >= datetime('now', ?)
            ORDER BY signal.timestamp DESC, signal.id DESC
            LIMIT ?
            """,
            (f"-{lookback_hours} hours", limit),
        ).fetchall()

    def get_watchlist_digest_signals(
        self,
        lookback_hours: int = 24,
        limit: int = 10,
    ) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT DISTINCT signal.*
            FROM signal_history AS signal
            JOIN signal_watchlists AS association
              ON association.signal_id = signal.id
            JOIN watchlists AS watchlist ON watchlist.id = association.watchlist_id
            WHERE watchlist.enabled = 1
              AND watchlist.include_in_digest = 1
              AND signal.timestamp >= datetime('now', ?)
            ORDER BY signal.timestamp DESC, signal.id DESC
            LIMIT ?
            """,
            (f"-{lookback_hours} hours", limit),
        ).fetchall()

    def create_watchlist(
        self,
        name: str,
        description: str,
        enabled: bool,
        priority: int,
        minimum_hype_score: float,
        minimum_momentum_score: float,
        minimum_confidence: int,
        telegram_enabled: bool,
        include_in_digest: bool,
        dashboard_highlight: bool,
        case_insensitive: bool,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO watchlists (
                name, description, enabled, priority, minimum_hype_score,
                minimum_momentum_score, minimum_confidence, telegram_enabled,
                include_in_digest, dashboard_highlight, case_insensitive
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                description,
                int(enabled),
                priority,
                minimum_hype_score,
                minimum_momentum_score,
                minimum_confidence,
                int(telegram_enabled),
                int(include_in_digest),
                int(dashboard_highlight),
                int(case_insensitive),
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def get_watchlists(self, enabled: bool | None = None) -> list[sqlite3.Row]:
        where = "WHERE enabled = ?" if enabled is not None else ""
        parameters = (int(enabled),) if enabled is not None else ()
        return self.connection.execute(
            f"""
            SELECT *
            FROM watchlists
            {where}
            ORDER BY priority DESC, name COLLATE NOCASE, id
            """,
            parameters,
        ).fetchall()

    def get_watchlist(self, identifier: int | str) -> sqlite3.Row | None:
        if isinstance(identifier, int):
            return self.connection.execute(
                "SELECT * FROM watchlists WHERE id = ?",
                (identifier,),
            ).fetchone()
        return self.connection.execute(
            "SELECT * FROM watchlists WHERE name = ? COLLATE NOCASE",
            (str(identifier).strip(),),
        ).fetchone()

    def update_watchlist(self, watchlist_id: int, **changes) -> bool:
        allowed = {
            "name",
            "description",
            "enabled",
            "priority",
            "minimum_hype_score",
            "minimum_momentum_score",
            "minimum_confidence",
            "telegram_enabled",
            "include_in_digest",
            "dashboard_highlight",
            "case_insensitive",
        }
        assignments = []
        parameters: list[object] = []
        for key, value in changes.items():
            if key not in allowed:
                raise ValueError(f"Unsupported watchlist column: {key}")
            if key in {
                "enabled",
                "telegram_enabled",
                "include_in_digest",
                "dashboard_highlight",
                "case_insensitive",
            }:
                value = int(bool(value))
            assignments.append(f"{key} = ?")
            parameters.append(value)
        if not assignments:
            return self.get_watchlist(watchlist_id) is not None
        assignments.append("updated_at = CURRENT_TIMESTAMP")
        parameters.append(watchlist_id)
        cursor = self.connection.execute(
            f"UPDATE watchlists SET {', '.join(assignments)} WHERE id = ?",
            parameters,
        )
        self.connection.commit()
        return bool(cursor.rowcount)

    def delete_watchlist(self, watchlist_id: int) -> bool:
        self.connection.execute(
            "DELETE FROM signal_watchlists WHERE watchlist_id = ?",
            (watchlist_id,),
        )
        self.connection.execute(
            "DELETE FROM watchlist_items WHERE watchlist_id = ?",
            (watchlist_id,),
        )
        cursor = self.connection.execute(
            "DELETE FROM watchlists WHERE id = ?",
            (watchlist_id,),
        )
        self.connection.commit()
        return bool(cursor.rowcount)

    def add_watchlist_item(
        self,
        watchlist_id: int,
        item_type: str,
        item_value: str,
        normalized_value: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO watchlist_items (
                watchlist_id, item_type, item_value, normalized_value
            )
            VALUES (?, ?, ?, ?)
            """,
            (watchlist_id, item_type, item_value, normalized_value),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def get_watchlist_item(self, item_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM watchlist_items WHERE id = ?",
            (item_id,),
        ).fetchone()

    def get_watchlist_items(self, watchlist_id: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT *
            FROM watchlist_items
            WHERE watchlist_id = ?
            ORDER BY item_type, item_value COLLATE NOCASE, id
            """,
            (watchlist_id,),
        ).fetchall()

    def get_watchlist_items_for_enabled_watchlists(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT item.*
            FROM watchlist_items AS item
            JOIN watchlists AS watchlist ON watchlist.id = item.watchlist_id
            WHERE watchlist.enabled = 1
            ORDER BY watchlist.priority DESC, item.id
            """
        ).fetchall()

    def remove_watchlist_item(self, watchlist_id: int, item: int | str) -> bool:
        if isinstance(item, int):
            cursor = self.connection.execute(
                "DELETE FROM watchlist_items WHERE watchlist_id = ? AND id = ?",
                (watchlist_id, item),
            )
        else:
            normalized = " ".join(str(item).strip().split()).lstrip("$").casefold()
            cursor = self.connection.execute(
                """
                DELETE FROM watchlist_items
                WHERE watchlist_id = ? AND normalized_value = ?
                """,
                (watchlist_id, normalized),
            )
        self.connection.commit()
        return bool(cursor.rowcount)

    def save_signal_watchlist(
        self,
        signal_id: int,
        watchlist_id: int,
        matched_item_type: str,
        matched_item_value: str,
    ) -> bool:
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO signal_watchlists (
                signal_id, watchlist_id, matched_item_type, matched_item_value
            )
            VALUES (?, ?, ?, ?)
            """,
            (signal_id, watchlist_id, matched_item_type, matched_item_value),
        )
        self.connection.commit()
        return bool(cursor.rowcount)

    def get_signal_watchlists(
        self,
        signal_id: int,
        telegram_only: bool = False,
    ) -> list[sqlite3.Row]:
        telegram_filter = "AND watchlist.telegram_enabled = 1" if telegram_only else ""
        return self.connection.execute(
            f"""
            SELECT
                watchlist.*,
                GROUP_CONCAT(DISTINCT association.matched_item_type)
                    AS matched_item_types,
                GROUP_CONCAT(DISTINCT association.matched_item_value)
                    AS matched_item_values,
                MAX(association.matched_at) AS matched_at
            FROM signal_watchlists AS association
            JOIN watchlists AS watchlist ON watchlist.id = association.watchlist_id
            WHERE association.signal_id = ?
            {telegram_filter}
            GROUP BY watchlist.id
            ORDER BY watchlist.priority DESC, watchlist.name COLLATE NOCASE
            """,
            (signal_id,),
        ).fetchall()

    def get_signal_watchlist_context(self, signal_id: int) -> dict[str, object]:
        rows = self.get_signal_watchlists(signal_id)
        return {
            "ids": tuple(int(row["id"]) for row in rows),
            "names": tuple(str(row["name"]) for row in rows),
            "highest_priority": max((int(row["priority"]) for row in rows), default=0),
            "matched_any": bool(rows),
        }

    def get_watchlist_signals(
        self,
        watchlist_id: int,
        limit: int | None = 50,
        days: int | None = None,
    ) -> list[sqlite3.Row]:
        conditions = ["association.watchlist_id = ?"]
        parameters: list[object] = [watchlist_id]
        if days is not None:
            conditions.append("signal.timestamp >= datetime('now', ?)")
            parameters.append(f"-{days} days")
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            parameters.append(limit)
        return self.connection.execute(
            f"""
            WITH matched AS (
                SELECT
                    signal_id,
                    MAX(matched_at) AS matched_at,
                    GROUP_CONCAT(DISTINCT matched_item_type) AS matched_item_types,
                    GROUP_CONCAT(DISTINCT matched_item_value) AS matched_item_values
                FROM signal_watchlists AS association
                WHERE association.watchlist_id = ?
                GROUP BY signal_id
            )
            SELECT
                signal.*,
                matched.matched_at,
                matched.matched_item_types,
                matched.matched_item_values,
                outcome.status AS outcome_status,
                outcome.evaluated_at
            FROM matched
            JOIN signal_history AS signal ON signal.id = matched.signal_id
            LEFT JOIN signal_outcomes AS outcome
              ON outcome.id = (
                  SELECT latest.id FROM signal_outcomes AS latest
                  WHERE latest.signal_id = signal.id
                  ORDER BY latest.evaluated_at DESC, latest.id DESC LIMIT 1
              )
            WHERE {' AND '.join(conditions[1:]) if len(conditions) > 1 else '1 = 1'}
            ORDER BY signal.timestamp DESC, signal.id DESC
            {limit_clause}
            """,
            [watchlist_id, *parameters[1:]],
        ).fetchall()

    def get_watchlist_performance(self, watchlist_id: int, days: int = 30) -> sqlite3.Row:
        return self.connection.execute(
            """
            WITH matched AS (
                SELECT DISTINCT association.signal_id,
                    MAX(association.matched_at) AS matched_at
                FROM signal_watchlists AS association
                JOIN signal_history AS signal ON signal.id = association.signal_id
                WHERE association.watchlist_id = ?
                  AND signal.timestamp >= datetime('now', ?)
                GROUP BY association.signal_id
            ), latest_outcomes AS (
                SELECT outcome.*
                FROM signal_outcomes AS outcome
                WHERE outcome.id = (
                    SELECT latest.id FROM signal_outcomes AS latest
                    WHERE latest.signal_id = outcome.signal_id
                    ORDER BY latest.evaluated_at DESC, latest.id DESC LIMIT 1
                )
            )
            SELECT
                COUNT(matched.signal_id) AS signals_count,
                COUNT(DISTINCT signal.unified_event_id) AS unified_event_count,
                (SELECT COUNT(*)
                 FROM matched AS raw_matched
                 JOIN signal_history AS raw_signal
                   ON raw_signal.id = raw_matched.signal_id
                 JOIN unified_event_items AS event_item
                   ON event_item.unified_event_id = raw_signal.unified_event_id)
                    AS raw_article_count,
                COUNT(outcome.id) AS evaluated_count,
                SUM(CASE WHEN outcome.status = 'SUCCESS' THEN 1 ELSE 0 END)
                    AS successful_count,
                SUM(CASE WHEN outcome.status = 'NEUTRAL' THEN 1 ELSE 0 END)
                    AS neutral_count,
                SUM(CASE WHEN outcome.status = 'FAILED' THEN 1 ELSE 0 END)
                    AS failed_count,
                AVG(signal.hype_score) AS average_hype_score,
                AVG(signal.momentum_score) AS average_momentum_score,
                MAX(matched.matched_at) AS last_matched_at
            FROM matched
            JOIN signal_history AS signal ON signal.id = matched.signal_id
            LEFT JOIN latest_outcomes AS outcome ON outcome.signal_id = signal.id
            """,
            (watchlist_id, f"-{days} days"),
        ).fetchone()

    def get_rules_referencing_watchlist(
        self,
        watchlist_id: int,
        watchlist_name: str,
    ) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT * FROM alert_rules
            WHERE condition LIKE ? COLLATE NOCASE
               OR condition LIKE ?
            ORDER BY priority DESC, name COLLATE NOCASE
            """,
            (f"%{watchlist_name}%", f"%{watchlist_id}%"),
        ).fetchall()

    def get_pending_signal_outcomes(
        self,
        evaluation_window_hours: int,
    ) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT signal.*
            FROM signal_history AS signal
            WHERE signal.timestamp <= datetime('now', ?)
              AND NOT EXISTS (
                  SELECT 1
                  FROM signal_outcomes AS outcome
                  WHERE outcome.signal_id = signal.id
                    AND COALESCE(
                        outcome.evaluation_window_hours,
                        outcome.hours_after
                    ) = ?
              )
            ORDER BY signal.timestamp, signal.id
            """,
            (
                f"-{evaluation_window_hours} hours",
                evaluation_window_hours,
            ),
        ).fetchall()

    def get_current_signal_metrics(
        self,
        token: str | None,
        narrative: str | None,
        lookback_hours: int,
    ) -> sqlite3.Row:
        return self.connection.execute(
            """
            SELECT
                COUNT(*) AS mentions_count,
                COALESCE(AVG(importance), 0.0) AS average_importance,
                COALESCE(
                    (julianday('now') - julianday(MAX(analyzed_at))) * 24.0,
                    ?
                ) AS recency_hours
            FROM analyzed_posts
            WHERE analyzed_at >= datetime('now', ?)
              AND (
                  (? IS NOT NULL AND EXISTS (
                      SELECT 1 FROM json_each(tokens_json)
                      WHERE value = ? COLLATE NOCASE
                  ))
                  OR
                  (? IS NOT NULL AND EXISTS (
                      SELECT 1 FROM json_each(narratives_json)
                      WHERE value = ? COLLATE NOCASE
                  ))
              )
            """,
            (
                lookback_hours,
                f"-{lookback_hours} hours",
                token,
                token,
                narrative,
                narrative,
            ),
        ).fetchone()

    def save_signal_outcome(
        self,
        signal_id: int,
        hours_after: int,
        status: str,
        score_change: float,
        mentions_change: int,
        momentum_change: float,
        notes: str,
        *,
        evaluation_window_hours: int | None = None,
        original_hype_score: float | None = None,
        current_hype_score: float | None = None,
        original_momentum_score: float | None = None,
        current_momentum_score: float | None = None,
        original_mentions: int | None = None,
        current_mentions: int | None = None,
    ) -> int | None:
        signal = self.connection.execute(
            """
            SELECT hype_score, momentum_score, mentions_count
            FROM signal_history
            WHERE id = ?
            """,
            (signal_id,),
        ).fetchone()
        if signal is None:
            raise ValueError(f"Signal {signal_id} does not exist")
        window = evaluation_window_hours or hours_after
        original_hype = (
            float(original_hype_score)
            if original_hype_score is not None
            else float(signal["hype_score"])
        )
        original_momentum = (
            float(original_momentum_score)
            if original_momentum_score is not None
            else float(signal["momentum_score"])
        )
        original_mention_count = (
            int(original_mentions)
            if original_mentions is not None
            else int(signal["mentions_count"] or 0)
        )
        current_hype = (
            float(current_hype_score)
            if current_hype_score is not None
            else original_hype + score_change
        )
        current_momentum = (
            float(current_momentum_score)
            if current_momentum_score is not None
            else original_momentum + momentum_change
        )
        current_mention_count = (
            int(current_mentions)
            if current_mentions is not None
            else original_mention_count + mentions_change
        )
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO signal_outcomes (
                signal_id, hours_after, evaluation_window_hours, status,
                score_change, original_hype_score, current_hype_score,
                hype_change, original_momentum_score, current_momentum_score,
                momentum_change, original_mentions, current_mentions,
                mentions_change, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                window,
                window,
                status,
                score_change,
                original_hype,
                current_hype,
                score_change,
                original_momentum,
                current_momentum,
                momentum_change,
                original_mention_count,
                current_mention_count,
                mentions_change,
                notes,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid) if cursor.rowcount else None

    def get_signal_outcome_summary(
        self,
        period_hours: int | None = None,
        evaluation_window_hours: int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> sqlite3.Row:
        conditions = []
        parameters: list[object] = []
        if period_hours is not None:
            conditions.append("evaluated_at >= datetime('now', ?)")
            parameters.append(f"-{period_hours} hours")
        if evaluation_window_hours is not None:
            conditions.append("evaluation_window_hours = ?")
            parameters.append(evaluation_window_hours)
        self._append_date_filters(
            conditions,
            parameters,
            "evaluated_at",
            from_date,
            to_date,
        )
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return self.connection.execute(
            f"""
            SELECT
                COUNT(*) AS signals_evaluated,
                SUM(CASE WHEN status = 'SUCCESS' THEN 1 ELSE 0 END) AS success,
                SUM(CASE WHEN status = 'NEUTRAL' THEN 1 ELSE 0 END) AS neutral,
                SUM(CASE WHEN status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                AVG(hype_change) AS average_hype_change,
                AVG(mentions_change) AS average_mention_change,
                AVG(momentum_change) AS average_momentum_change
            FROM signal_outcomes
            {where_clause}
            """,
            parameters,
        ).fetchone()

    def get_signal_outcome_narratives(
        self,
        order: str = "DESC",
        limit: int = 5,
        period_hours: int | None = None,
    ) -> list[sqlite3.Row]:
        if order not in {"ASC", "DESC"}:
            raise ValueError("order must be ASC or DESC")
        period_condition = (
            "AND outcome.evaluated_at >= datetime('now', ?)"
            if period_hours is not None
            else ""
        )
        parameters: list[object] = []
        if period_hours is not None:
            parameters.append(f"-{period_hours} hours")
        parameters.append(limit)
        return self.connection.execute(
            f"""
            SELECT
                signal.narrative AS name,
                COUNT(*) AS evaluated_count,
                100.0 * SUM(CASE WHEN outcome.status = 'SUCCESS' THEN 1 ELSE 0 END)
                    / COUNT(*) AS success_rate,
                100.0 * SUM(CASE WHEN outcome.status = 'SUCCESS' THEN 1 ELSE 0 END)
                    / COUNT(*) AS outcome_score,
                AVG(outcome.momentum_change) AS average_momentum_change
            FROM signal_outcomes AS outcome
            JOIN signal_history AS signal ON signal.id = outcome.signal_id
            WHERE signal.narrative IS NOT NULL
            {period_condition}
            GROUP BY signal.narrative
            ORDER BY success_rate {order}, average_momentum_change {order}, evaluated_count DESC
            LIMIT ?
            """,
            parameters,
        ).fetchall()

    def get_signal_outcomes(
        self,
        limit: int | None = 100,
        status: str | None = None,
        evaluation_window_hours: int | None = None,
        token: str | None = None,
        narrative: str | None = None,
        period_hours: int | None = None,
        signal_id: int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        watchlist_id: int | None = None,
    ) -> list[sqlite3.Row]:
        if not self.has_table("signal_outcomes") or not self.has_table("signal_history"):
            return []
        if status is not None and status not in {"SUCCESS", "NEUTRAL", "FAILED"}:
            raise ValueError(f"Unsupported outcome status: {status}")
        conditions = []
        parameters: list[object] = []
        for condition, value in (
            ("outcome.status = ?", status),
            ("outcome.evaluation_window_hours = ?", evaluation_window_hours),
            ("signal.token = ? COLLATE NOCASE", token),
            ("signal.narrative = ? COLLATE NOCASE", narrative),
            ("outcome.signal_id = ?", signal_id),
        ):
            if value is not None:
                conditions.append(condition)
                parameters.append(value)
        if period_hours is not None:
            conditions.append("outcome.evaluated_at >= datetime('now', ?)")
            parameters.append(f"-{period_hours} hours")
        if watchlist_id is not None:
            conditions.append(
                "EXISTS (SELECT 1 FROM signal_watchlists AS association "
                "WHERE association.signal_id = signal.id "
                "AND association.watchlist_id = ?)"
            )
            parameters.append(watchlist_id)
        self._append_date_filters(
            conditions,
            parameters,
            "outcome.evaluated_at",
            from_date,
            to_date,
        )
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            parameters.append(max(1, min(limit, 500)))
        return self.connection.execute(
            f"""
            SELECT
                outcome.id,
                outcome.signal_id,
                outcome.evaluated_at,
                outcome.evaluation_window_hours,
                outcome.status,
                outcome.original_hype_score,
                outcome.current_hype_score,
                outcome.hype_change,
                outcome.original_momentum_score,
                outcome.current_momentum_score,
                outcome.momentum_change,
                outcome.original_mentions,
                outcome.current_mentions,
                outcome.mentions_change,
                outcome.notes,
                signal.signal_type,
                signal.token,
                signal.narrative,
                signal.timestamp AS signal_timestamp
            FROM signal_outcomes AS outcome
            JOIN signal_history AS signal ON signal.id = outcome.signal_id
            {where_clause}
            ORDER BY outcome.evaluated_at DESC, outcome.id DESC
            {limit_clause}
            """,
            parameters,
        ).fetchall()

    def get_signal_performance_summary(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> sqlite3.Row:
        conditions: list[str] = []
        parameters: list[object] = []
        self._append_date_filters(
            conditions,
            parameters,
            "timestamp",
            from_date,
            to_date,
        )
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        return self.connection.execute(
            f"""
            SELECT
                COUNT(*) AS signals_generated,
                SUM(CASE
                    WHEN action IN ('watch', 'research') AND confidence >= 7
                    THEN 1 ELSE 0
                END) AS successful,
                AVG(confidence) AS average_confidence,
                AVG(momentum_score) AS average_momentum
            FROM signal_history
            {where_clause}
            """,
            parameters,
        ).fetchone()

    def get_narrative_performance_summary(
        self,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> list[sqlite3.Row]:
        signal_conditions = ["signal.narrative IS NOT NULL"]
        signal_parameters: list[object] = []
        self._append_date_filters(
            signal_conditions,
            signal_parameters,
            "signal.timestamp",
            from_date,
            to_date,
        )
        outcome_conditions: list[str] = []
        outcome_parameters: list[object] = []
        self._append_date_filters(
            outcome_conditions,
            outcome_parameters,
            "evaluated_at",
            from_date,
            to_date,
        )
        outcome_where = (
            f"WHERE {' AND '.join(outcome_conditions)}" if outcome_conditions else ""
        )
        return self.connection.execute(
            f"""
            WITH filtered_outcomes AS (
                SELECT *
                FROM signal_outcomes
                {outcome_where}
            )
            SELECT
                signal.narrative,
                COUNT(DISTINCT signal.id) AS signal_count,
                COUNT(outcome.id) AS evaluated_count,
                SUM(CASE WHEN outcome.status = 'SUCCESS' THEN 1 ELSE 0 END)
                    AS successful_count,
                SUM(CASE WHEN outcome.status = 'NEUTRAL' THEN 1 ELSE 0 END)
                    AS neutral_count,
                SUM(CASE WHEN outcome.status = 'FAILED' THEN 1 ELSE 0 END)
                    AS failed_count,
                CASE WHEN COUNT(outcome.id) > 0 THEN
                    100.0 * SUM(CASE WHEN outcome.status = 'SUCCESS' THEN 1 ELSE 0 END)
                        / COUNT(outcome.id)
                END AS success_rate,
                AVG(outcome.hype_change) AS average_hype_change,
                AVG(outcome.momentum_change) AS average_momentum_change,
                AVG(outcome.mentions_change) AS average_mentions_change
            FROM signal_history AS signal
            LEFT JOIN filtered_outcomes AS outcome ON outcome.signal_id = signal.id
            WHERE {' AND '.join(signal_conditions)}
            GROUP BY signal.narrative
            ORDER BY success_rate DESC, evaluated_count DESC, signal.narrative
            """,
            [*outcome_parameters, *signal_parameters],
        ).fetchall()

    def get_signal_performance_narratives(
        self,
        order: str = "DESC",
        limit: int = 5,
    ) -> list[sqlite3.Row]:
        if order not in {"ASC", "DESC"}:
            raise ValueError("order must be ASC or DESC")
        return self.connection.execute(
            f"""
            SELECT
                COALESCE(narrative, token, 'Unknown') AS name,
                COUNT(*) AS signals_count,
                AVG(momentum_score) AS average_momentum,
                AVG(confidence) AS average_confidence,
                AVG(hype_score) AS average_hype
            FROM signal_history
            GROUP BY COALESCE(narrative, token, 'Unknown')
            ORDER BY average_momentum {order}, average_confidence {order}, signals_count DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def get_signals(
        self,
        limit: int | None = 50,
        from_date: str | None = None,
        to_date: str | None = None,
        watchlist_id: int | None = None,
    ) -> list[sqlite3.Row]:
        if not self.has_table("signal_history"):
            return []
        rule_columns = (
            "EXISTS (SELECT 1 FROM signal_rule_matches AS match "
            "WHERE match.signal_id = signal.id AND match.high_priority = 1) "
            "AS high_priority, "
            "EXISTS (SELECT 1 FROM signal_rule_matches AS match "
            "WHERE match.signal_id = signal.id AND match.dashboard_highlight = 1) "
            "AS dashboard_highlight, "
            "EXISTS (SELECT 1 FROM signal_rule_matches AS match "
            "WHERE match.signal_id = signal.id AND match.include_in_digest = 1) "
            "AS include_in_digest, "
            "EXISTS (SELECT 1 FROM signal_rule_matches AS match "
            "WHERE match.signal_id = signal.id AND match.csv_export_marker = 1) "
            "AS csv_export_marker, "
            "(SELECT GROUP_CONCAT(rule.name, ', ') "
            "FROM signal_rule_matches AS match "
            "JOIN alert_rules AS rule ON rule.id = match.rule_id "
            "WHERE match.signal_id = signal.id) AS matched_rules"
            if self.has_table("signal_rule_matches") and self.has_table("alert_rules")
            else (
                "0 AS high_priority, 0 AS dashboard_highlight, "
                "0 AS include_in_digest, 0 AS csv_export_marker, "
                "NULL AS matched_rules"
            )
        )
        outcome_columns = (
            "outcome.status AS outcome_status, outcome.score_change, "
            "outcome.mentions_change, outcome.momentum_change, outcome.evaluated_at"
            if self.has_table("signal_outcomes")
            else (
                "NULL AS outcome_status, NULL AS score_change, "
                "NULL AS mentions_change, NULL AS momentum_change, "
                "NULL AS evaluated_at"
            )
        )
        outcome_join = (
            """
            LEFT JOIN signal_outcomes AS outcome
              ON outcome.id = (
                  SELECT latest.id
                  FROM signal_outcomes AS latest
                  WHERE latest.signal_id = signal.id
                  ORDER BY latest.evaluated_at DESC, latest.id DESC
                  LIMIT 1
              )
            """
            if self.has_table("signal_outcomes")
            else ""
        )
        watchlist_columns = (
            "(SELECT GROUP_CONCAT(DISTINCT watchlist.name) "
            "FROM signal_watchlists AS association "
            "JOIN watchlists AS watchlist ON watchlist.id = association.watchlist_id "
            "WHERE association.signal_id = signal.id) AS watchlist_names, "
            "EXISTS (SELECT 1 FROM signal_watchlists AS association "
            "JOIN watchlists AS watchlist ON watchlist.id = association.watchlist_id "
            "WHERE association.signal_id = signal.id "
            "AND watchlist.dashboard_highlight = 1) AS watchlist_dashboard_highlight, "
            "EXISTS (SELECT 1 FROM signal_watchlists AS association "
            "JOIN watchlists AS watchlist ON watchlist.id = association.watchlist_id "
            "WHERE association.signal_id = signal.id "
            "AND watchlist.include_in_digest = 1) AS watchlist_include_in_digest"
            if self.has_table("signal_watchlists") and self.has_table("watchlists")
            else (
                "NULL AS watchlist_names, 0 AS watchlist_dashboard_highlight, "
                "0 AS watchlist_include_in_digest"
            )
        )
        conditions: list[str] = []
        parameters: list[object] = []
        self._append_date_filters(
            conditions,
            parameters,
            "signal.timestamp",
            from_date,
            to_date,
        )
        if watchlist_id is not None:
            conditions.append(
                "EXISTS (SELECT 1 FROM signal_watchlists AS association "
                "WHERE association.signal_id = signal.id "
                "AND association.watchlist_id = ?)"
            )
            parameters.append(watchlist_id)
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            parameters.append(limit)
        return self.connection.execute(
            f"""
            SELECT
                signal.*,
                {outcome_columns},
                {rule_columns},
                {watchlist_columns}
            FROM signal_history AS signal
            {outcome_join}
            {where_clause}
            ORDER BY signal.timestamp DESC, signal.id DESC
            {limit_clause}
            """,
            parameters,
        ).fetchall()

    def get_latest_signals(self, limit: int = 50) -> list[sqlite3.Row]:
        return self.get_signals(limit=limit)

    def get_latest_narrative_momentum(self) -> list[sqlite3.Row]:
        if not self.has_table("daily_momentum"):
            return []
        return self.connection.execute(
            """
            WITH latest_dates AS (
                SELECT narrative, MAX(date) AS latest_date
                FROM daily_momentum
                GROUP BY narrative
            )
            SELECT snapshot.narrative, snapshot.momentum_score, snapshot.date
            FROM daily_momentum AS snapshot
            JOIN latest_dates
              ON latest_dates.narrative = snapshot.narrative
             AND latest_dates.latest_date = snapshot.date
            ORDER BY snapshot.momentum_score DESC, snapshot.narrative
            """
        ).fetchall()

    def clear_graph_projection(self) -> None:
        self.connection.execute("DELETE FROM graph_edges")
        self.connection.execute("DELETE FROM graph_nodes")
        self.connection.commit()

    def upsert_graph_node(
        self,
        *,
        node_type: str,
        entity_id: str,
        label: str,
        normalized_label: str,
        weight: float,
        activity_score: float,
        first_seen_at: str,
        last_seen_at: str,
        metadata_json: str,
    ) -> int:
        self.connection.execute(
            """
            INSERT INTO graph_nodes (
                node_type, entity_id, label, normalized_label, weight,
                activity_score, first_seen_at, last_seen_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(node_type, entity_id) DO UPDATE SET
                label = excluded.label,
                normalized_label = excluded.normalized_label,
                weight = MAX(graph_nodes.weight, excluded.weight),
                activity_score = MAX(graph_nodes.activity_score, excluded.activity_score),
                first_seen_at = MIN(graph_nodes.first_seen_at, excluded.first_seen_at),
                last_seen_at = MAX(graph_nodes.last_seen_at, excluded.last_seen_at),
                metadata_json = excluded.metadata_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                node_type, entity_id, label, normalized_label, weight,
                activity_score, first_seen_at, last_seen_at, metadata_json,
            ),
        )
        self.connection.commit()
        row = self.get_graph_node(node_type, entity_id)
        if row is None:
            raise RuntimeError("Graph node could not be persisted")
        return int(row["id"])

    def get_graph_node(self, node_type: str, entity_id: str) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM graph_nodes WHERE node_type = ? AND entity_id = ?",
            (node_type, entity_id),
        ).fetchone()

    def get_graph_node_by_id(self, node_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            "SELECT * FROM graph_nodes WHERE id = ?", (node_id,)
        ).fetchone()

    def get_graph_nodes(
        self,
        node_type: str | None = None,
        min_weight: float = 0.0,
        limit: int | None = 500,
    ) -> list[sqlite3.Row]:
        conditions = ["weight >= ?"]
        parameters: list[object] = [min_weight]
        if node_type:
            conditions.append("node_type = ?")
            parameters.append(node_type)
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            parameters.append(limit)
        return self.connection.execute(
            f"""
            SELECT * FROM graph_nodes WHERE {' AND '.join(conditions)}
            ORDER BY weight DESC, activity_score DESC, label COLLATE NOCASE
            {limit_clause}
            """,
            parameters,
        ).fetchall()

    def upsert_graph_edge(
        self,
        *,
        source_node_id: int,
        target_node_id: int,
        edge_type: str,
        derivation: str,
        weight: float,
        occurrence_increment: int,
        confidence: float,
        first_seen_at: str,
        last_seen_at: str,
        metadata_json: str,
    ) -> int:
        self.connection.execute(
            """
            INSERT INTO graph_edges (
                source_node_id, target_node_id, edge_type, derivation, weight,
                occurrence_count, confidence, first_seen_at, last_seen_at,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_node_id, target_node_id, edge_type, derivation)
            DO UPDATE SET
                weight = excluded.weight,
                occurrence_count = graph_edges.occurrence_count + excluded.occurrence_count,
                confidence = MAX(graph_edges.confidence, excluded.confidence),
                first_seen_at = MIN(graph_edges.first_seen_at, excluded.first_seen_at),
                last_seen_at = MAX(graph_edges.last_seen_at, excluded.last_seen_at),
                metadata_json = excluded.metadata_json,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                source_node_id, target_node_id, edge_type, derivation, weight,
                occurrence_increment, confidence, first_seen_at, last_seen_at,
                metadata_json,
            ),
        )
        self.connection.commit()
        row = self.connection.execute(
            """
            SELECT id FROM graph_edges
            WHERE source_node_id = ? AND target_node_id = ?
              AND edge_type = ? AND derivation = ?
            """,
            (source_node_id, target_node_id, edge_type, derivation),
        ).fetchone()
        return int(row["id"])

    def get_graph_edge(
        self,
        source_node_id: int,
        target_node_id: int,
        edge_type: str,
        derivation: str = "observed",
    ) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT * FROM graph_edges
            WHERE source_node_id = ? AND target_node_id = ?
              AND edge_type = ? AND derivation = ?
            """,
            (source_node_id, target_node_id, edge_type, derivation),
        ).fetchone()

    def update_graph_edge(
        self,
        edge_id: int,
        *,
        weight: float,
        confidence: float,
        last_seen_at: str,
        metadata_json: str,
    ) -> None:
        self.connection.execute(
            """
            UPDATE graph_edges SET weight = ?, confidence = MAX(confidence, ?),
                last_seen_at = MAX(last_seen_at, ?), metadata_json = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (weight, confidence, last_seen_at, metadata_json, edge_id),
        )
        self.connection.commit()

    def get_graph_edges(
        self,
        edge_type: str | None = None,
        min_weight: float = 0.0,
        min_occurrences: int = 1,
        node_id: int | None = None,
        limit: int | None = 1000,
    ) -> list[sqlite3.Row]:
        conditions = ["edge.weight >= ?", "edge.occurrence_count >= ?"]
        parameters: list[object] = [min_weight, min_occurrences]
        if edge_type:
            conditions.append("edge.edge_type = ?")
            parameters.append(edge_type)
        if node_id is not None:
            conditions.append("(edge.source_node_id = ? OR edge.target_node_id = ?)")
            parameters.extend((node_id, node_id))
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            parameters.append(limit)
        return self.connection.execute(
            f"""
            SELECT edge.*,
                   source.node_type AS source_type,
                   source.entity_id AS source_entity_id,
                   source.label AS source_label,
                   target.node_type AS target_type,
                   target.entity_id AS target_entity_id,
                   target.label AS target_label
            FROM graph_edges AS edge
            JOIN graph_nodes AS source ON source.id = edge.source_node_id
            JOIN graph_nodes AS target ON target.id = edge.target_node_id
            WHERE {' AND '.join(conditions)}
            ORDER BY edge.weight DESC, edge.occurrence_count DESC, edge.id
            {limit_clause}
            """,
            parameters,
        ).fetchall()

    def save_graph_snapshot(
        self,
        period_start: str,
        period_end: str,
        node_count: int,
        edge_count: int,
        metrics_json: str,
    ) -> tuple[int, bool]:
        cursor = self.connection.execute(
            """
            INSERT OR IGNORE INTO graph_snapshots (
                period_start, period_end, node_count, edge_count, metrics_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (period_start, period_end, node_count, edge_count, metrics_json),
        )
        self.connection.commit()
        row = self.connection.execute(
            """
            SELECT id FROM graph_snapshots
            WHERE period_start = ? AND period_end = ?
            """,
            (period_start, period_end),
        ).fetchone()
        return int(row["id"]), bool(cursor.rowcount)

    def get_graph_snapshots(self, limit: int = 100) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT * FROM graph_snapshots
            ORDER BY period_end DESC, id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()

    def delete_orphan_graph_nodes(self) -> int:
        cursor = self.connection.execute(
            """
            DELETE FROM graph_nodes
            WHERE id NOT IN (
                SELECT source_node_id FROM graph_edges
                UNION SELECT target_node_id FROM graph_edges
            )
            """
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def save_signal_quality_score(self, values: dict[str, Any]) -> int:
        columns = (
            "signal_id", "quality_score", "classification", "outcome_quality",
            "confidence_calibration", "source_reliability", "evidence_strength",
            "source_diversity", "timeliness", "rule_precision",
            "watchlist_relevance", "ai_agreement", "noise_risk",
            "evaluation_coverage", "evidence_count", "calculation_version",
            "breakdown_json", "calculated_at",
        )
        placeholders = ", ".join("?" for _ in columns)
        updates = ", ".join(
            f"{column} = excluded.{column}" for column in columns
            if column not in {"signal_id", "calculation_version"}
        )
        self.connection.execute(
            f"""
            INSERT INTO signal_quality_scores ({', '.join(columns)})
            VALUES ({placeholders})
            ON CONFLICT(signal_id, calculation_version) DO UPDATE SET
                {updates}, updated_at = CURRENT_TIMESTAMP
            """,
            tuple(values.get(column) for column in columns),
        )
        self.connection.commit()
        row = self.get_signal_quality_score(
            int(values["signal_id"]), int(values["calculation_version"])
        )
        if row is None:
            raise RuntimeError("Signal quality score could not be persisted")
        return int(row["id"])

    def get_signal_quality_score(
        self, signal_id: int, calculation_version: int | None = None,
    ) -> sqlite3.Row | None:
        condition = "AND score.calculation_version = ?" if calculation_version else ""
        parameters: tuple[object, ...] = (
            (signal_id, calculation_version) if calculation_version else (signal_id,)
        )
        return self.connection.execute(
            f"""
            SELECT score.*, signal.signal_type, signal.token, signal.narrative,
                   signal.hype_score, signal.momentum_score, signal.confidence,
                   signal.action, signal.mentions_count, signal.timestamp
            FROM signal_quality_scores AS score
            JOIN signal_history AS signal ON signal.id = score.signal_id
            WHERE score.signal_id = ? {condition}
            ORDER BY score.calculation_version DESC LIMIT 1
            """,
            parameters,
        ).fetchone()

    def get_signal_quality_scores(
        self,
        *,
        limit: int | None = 100,
        classification: str | None = None,
        calculation_version: int | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        signal_ids: list[int] | None = None,
    ) -> list[sqlite3.Row]:
        conditions: list[str] = []
        parameters: list[object] = []
        if classification:
            conditions.append("score.classification = ?")
            parameters.append(classification)
        if calculation_version:
            conditions.append("score.calculation_version = ?")
            parameters.append(calculation_version)
        if signal_ids is not None:
            if not signal_ids:
                return []
            conditions.append(f"score.signal_id IN ({','.join('?' for _ in signal_ids)})")
            parameters.extend(signal_ids)
        self._append_date_filters(
            conditions, parameters, "score.calculated_at", from_date, to_date
        )
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            parameters.append(max(1, min(limit, 5000)))
        return self.connection.execute(
            f"""
            SELECT score.*, signal.signal_type, signal.token, signal.narrative,
                   signal.hype_score, signal.momentum_score, signal.confidence,
                   signal.action, signal.mentions_count, signal.timestamp,
                   outcome.status AS outcome_status
            FROM signal_quality_scores AS score
            JOIN signal_history AS signal ON signal.id = score.signal_id
            LEFT JOIN signal_outcomes AS outcome ON outcome.id = (
                SELECT latest.id FROM signal_outcomes AS latest
                WHERE latest.signal_id = signal.id
                ORDER BY latest.evaluation_window_hours DESC, latest.id DESC LIMIT 1
            )
            {where}
            ORDER BY score.calculated_at DESC, score.id DESC {limit_clause}
            """,
            parameters,
        ).fetchall()

    def get_signal_quality_context(self, signal_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT signal.*, event.title AS event_title,
                   event.first_seen_at AS event_first_seen_at,
                   event.last_seen_at AS event_last_seen_at,
                   event.source_count, event.item_count, event.duplicate_count,
                   event.highest_source_priority, event.conflict_count,
                   event.requires_review, event.material_version
            FROM signal_history AS signal
            LEFT JOIN unified_events AS event ON event.id = signal.unified_event_id
            WHERE signal.id = ?
            """,
            (signal_id,),
        ).fetchone()

    def get_signal_quality_sources(self, signal_id: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT DISTINCT source.*, item.author, item.published_at, item.fetched_at,
                   item.status AS content_status, event.conflict_count
            FROM signal_history AS signal
            JOIN unified_event_items AS association
              ON association.unified_event_id = signal.unified_event_id
            JOIN content_items AS item ON item.id = association.content_item_id
            JOIN content_sources AS source ON source.id = item.source_id
            JOIN unified_events AS event ON event.id = association.unified_event_id
            WHERE signal.id = ?
            ORDER BY source.priority DESC, source.id
            """,
            (signal_id,),
        ).fetchall()

    def get_signal_ai_analyses_for_signal(self, signal_id: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT analysis.*, usage.latency_ms
            FROM signal_ai_analyses AS analysis
            LEFT JOIN ai_usage AS usage ON usage.id = (
                SELECT candidate.id FROM ai_usage AS candidate
                WHERE candidate.signal_id = analysis.signal_id
                  AND candidate.provider = analysis.provider
                  AND candidate.model = analysis.model
                ORDER BY candidate.id DESC LIMIT 1
            )
            WHERE analysis.signal_id = ?
            ORDER BY analysis.created_at DESC, analysis.id DESC
            """,
            (signal_id,),
        ).fetchall()

    def get_source_quality_statistics(self, source_id: int) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT source.*,
                   COUNT(DISTINCT signal.id) AS signal_count,
                   COUNT(DISTINCT item.id) AS content_item_count,
                   COUNT(DISTINCT CASE WHEN item.status = 'malformed' THEN item.id END) AS malformed_count,
                   COUNT(DISTINCT outcome.id) AS evaluated_count,
                   COUNT(DISTINCT CASE WHEN outcome.status = 'SUCCESS' THEN outcome.id END) AS successful_count,
                   COUNT(DISTINCT CASE WHEN outcome.status = 'NEUTRAL' THEN outcome.id END) AS neutral_count,
                   COUNT(DISTINCT CASE WHEN outcome.status = 'FAILED' THEN outcome.id END) AS failed_count,
                   AVG(quality.quality_score) AS average_quality,
                   AVG(CASE WHEN item.published_at IS NOT NULL THEN
                       (julianday(item.fetched_at) - julianday(item.published_at)) * 1440
                   END) AS average_ingestion_minutes,
                   AVG(event.conflict_count > 0) * 100.0 AS conflict_rate
            FROM content_sources AS source
            LEFT JOIN content_items AS item ON item.source_id = source.id
            LEFT JOIN unified_event_items AS membership ON membership.content_item_id = item.id
            LEFT JOIN unified_events AS event ON event.id = membership.unified_event_id
            LEFT JOIN signal_history AS signal ON signal.unified_event_id = event.id
            LEFT JOIN signal_outcomes AS outcome ON outcome.id = (
                SELECT latest.id FROM signal_outcomes AS latest
                WHERE latest.signal_id = signal.id
                ORDER BY latest.evaluation_window_hours DESC, latest.id DESC LIMIT 1
            )
            LEFT JOIN signal_quality_scores AS quality ON quality.id = (
                SELECT latest_quality.id FROM signal_quality_scores AS latest_quality
                WHERE latest_quality.signal_id = signal.id
                ORDER BY latest_quality.calculation_version DESC LIMIT 1
            )
            WHERE source.id = ? GROUP BY source.id
            """,
            (source_id,),
        ).fetchone()

    def save_quality_aggregate(self, values: dict[str, Any]) -> int:
        columns = (
            "entity_type", "entity_id", "period_start", "period_end",
            "signal_count", "evaluated_count", "successful_count", "neutral_count",
            "failed_count", "average_quality_score", "median_quality_score",
            "precision", "noise_rate", "evaluation_coverage", "average_confidence",
            "calibration_error", "reliability_score", "calculation_version",
            "metrics_json", "calculated_at",
        )
        updates = ", ".join(
            f"{column} = excluded.{column}" for column in columns
            if column not in {
                "entity_type", "entity_id", "period_start", "period_end",
                "calculation_version",
            }
        )
        self.connection.execute(
            f"""
            INSERT INTO quality_aggregates ({', '.join(columns)})
            VALUES ({','.join('?' for _ in columns)})
            ON CONFLICT(
                entity_type, entity_id, period_start, period_end, calculation_version
            ) DO UPDATE SET {updates}
            """,
            tuple(values.get(column) for column in columns),
        )
        self.connection.commit()
        row = self.connection.execute(
            """
            SELECT id FROM quality_aggregates WHERE entity_type = ? AND entity_id = ?
              AND period_start = ? AND period_end = ? AND calculation_version = ?
            """,
            (
                values["entity_type"], values["entity_id"], values["period_start"],
                values["period_end"], values["calculation_version"],
            ),
        ).fetchone()
        return int(row["id"])

    def get_quality_aggregates(
        self, entity_type: str | None = None, limit: int = 500,
    ) -> list[sqlite3.Row]:
        where = "WHERE entity_type = ?" if entity_type else ""
        parameters: list[object] = [entity_type] if entity_type else []
        parameters.append(max(1, min(limit, 5000)))
        return self.connection.execute(
            f"""SELECT * FROM quality_aggregates {where}
            ORDER BY period_end DESC, average_quality_score DESC, id DESC LIMIT ?""",
            parameters,
        ).fetchall()

    def save_quality_recommendation(self, values: dict[str, Any]) -> tuple[int, bool]:
        columns = (
            "entity_type", "entity_id", "recommendation_type", "severity", "title",
            "description", "suggested_action", "confidence",
            "minimum_sample_requirement", "evidence_json", "period_key",
        )
        cursor = self.connection.execute(
            f"""INSERT OR IGNORE INTO quality_recommendations ({', '.join(columns)})
            VALUES ({','.join('?' for _ in columns)})""",
            tuple(values.get(column) for column in columns),
        )
        self.connection.commit()
        row = self.connection.execute(
            """SELECT id FROM quality_recommendations
            WHERE entity_type = ? AND entity_id = ? AND recommendation_type = ?
              AND period_key = ?""",
            (
                values["entity_type"], values["entity_id"],
                values["recommendation_type"], values["period_key"],
            ),
        ).fetchone()
        return int(row["id"]), bool(cursor.rowcount)

    def get_quality_recommendations(
        self, status: str | None = None, limit: int = 500,
    ) -> list[sqlite3.Row]:
        where = "WHERE status = ?" if status else ""
        parameters: list[object] = [status] if status else []
        parameters.append(max(1, min(limit, 5000)))
        return self.connection.execute(
            f"""SELECT * FROM quality_recommendations {where}
            ORDER BY CASE severity WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC,
                     created_at DESC, id DESC LIMIT ?""",
            parameters,
        ).fetchall()

    def update_quality_recommendation_status(self, recommendation_id: int, status: str) -> bool:
        if status not in {"open", "acknowledged", "resolved", "dismissed"}:
            raise ValueError("Invalid quality recommendation status")
        resolved_at = (
            datetime.now(timezone.utc).isoformat()
            if status in {"resolved", "dismissed"} else None
        )
        cursor = self.connection.execute(
            """UPDATE quality_recommendations SET status = ?, resolved_at = ?
            WHERE id = ?""",
            (status, resolved_at, recommendation_id),
        )
        self.connection.commit()
        return bool(cursor.rowcount)

    def save_observability_snapshot(
        self, timestamp: str, metrics: dict[str, Any],
    ) -> int:
        self.connection.execute(
            """INSERT INTO observability_snapshots (timestamp, metrics_json)
            VALUES (?, ?) ON CONFLICT(timestamp) DO UPDATE SET
                metrics_json = excluded.metrics_json""",
            (timestamp, json.dumps(metrics, sort_keys=True)),
        )
        self.connection.commit()
        row = self.connection.execute(
            "SELECT id FROM observability_snapshots WHERE timestamp = ?", (timestamp,)
        ).fetchone()
        return int(row["id"])

    def get_observability_snapshots(self, limit: int = 200) -> list[sqlite3.Row]:
        return self.connection.execute(
            """SELECT * FROM observability_snapshots
            ORDER BY timestamp DESC, id DESC LIMIT ?""",
            (max(1, min(limit, 5000)),),
        ).fetchall()

    def cleanup_observability_snapshots(self, retention_days: int) -> int:
        cursor = self.connection.execute(
            """DELETE FROM observability_snapshots
            WHERE datetime(timestamp) < datetime('now', ?)""",
            (f"-{max(1, retention_days)} days",),
        )
        self.connection.commit()
        return int(cursor.rowcount)

    def get_dashboard_status(self) -> dict[str, object]:
        return {
            "analyzed_posts": self._table_count("analyzed_posts"),
            "signals": self._table_count("signal_history"),
            "outcomes": self._table_count("signal_outcomes"),
            "last_analysis_at": self._table_max("analyzed_posts", "analyzed_at"),
            "last_signal_at": self._table_max("signal_history", "timestamp"),
        }

    def has_table(self, table: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _append_date_filters(
        conditions: list[str],
        parameters: list[object],
        column: str,
        from_date: str | None,
        to_date: str | None,
    ) -> None:
        if from_date is not None:
            conditions.append(f"{column} >= ?")
            parameters.append(from_date)
        if to_date is not None:
            conditions.append(f"{column} < datetime(?, '+1 day')")
            parameters.append(to_date)

    def _table_count(self, table: str) -> int:
        if not self.has_table(table):
            return 0
        return int(self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    def _table_max(self, table: str, column: str):
        if not self.has_table(table):
            return None
        return self.connection.execute(
            f"SELECT MAX({column}) FROM {table}"
        ).fetchone()[0]

    def close(self) -> None:
        self.connection.close()
