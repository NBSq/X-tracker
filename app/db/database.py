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
            """
        )
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
