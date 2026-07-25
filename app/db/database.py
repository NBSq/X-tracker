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
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row

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
            """
        )
        self._add_column_if_missing("signal_history", "mentions_count", "INTEGER")
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
                   signal.hype_score, signal.momentum_score
            FROM signal_ai_analyses AS analysis
            JOIN signal_history AS signal ON signal.id = analysis.signal_id
            {provider_filter}
            ORDER BY analysis.created_at DESC, analysis.id DESC LIMIT ?
            """,
            parameters,
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
    ) -> int:
        self.connection.execute(
            """
            INSERT INTO signal_history (
                signal_type, token, narrative, hype_score, momentum_score,
                confidence, action, mentions_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
