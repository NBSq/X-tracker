from __future__ import annotations

import json
import sqlite3
from pathlib import Path

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

            CREATE INDEX IF NOT EXISTS idx_signal_outcomes_signal_id
            ON signal_outcomes(signal_id);
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
        self.connection.execute("DELETE FROM signal_outcomes")
        self.connection.execute("DELETE FROM alerts")
        self.connection.execute("DELETE FROM analyzed_posts")
        self.connection.execute("DELETE FROM narrative_score_history")
        self.connection.execute("DELETE FROM daily_momentum")
        self.connection.execute("DELETE FROM signal_history")
        self.connection.commit()

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
    ) -> list[sqlite3.Row]:
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
    ) -> list[sqlite3.Row]:
        if not self.has_table("signal_history"):
            return []
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
        conditions: list[str] = []
        parameters: list[object] = []
        self._append_date_filters(
            conditions,
            parameters,
            "signal.timestamp",
            from_date,
            to_date,
        )
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            parameters.append(limit)
        return self.connection.execute(
            f"""
            SELECT
                signal.*,
                {outcome_columns}
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
