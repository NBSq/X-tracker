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
            """
        )
        self.connection.commit()

    def reset(self) -> None:
        self.connection.execute("DELETE FROM alerts")
        self.connection.execute("DELETE FROM analyzed_posts")
        self.connection.execute("DELETE FROM narrative_score_history")
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
            SELECT username, text, tokens_json, narratives_json, importance
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

    def close(self) -> None:
        self.connection.close()
