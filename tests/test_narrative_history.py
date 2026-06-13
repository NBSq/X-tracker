import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.ai.analyzer import AnalysisResult
from app.db.database import Database
from app.scoring.momentum_score import NarrativeMomentum
from app.sources.x_client import XPost


class NarrativeHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "history.sqlite3")
        self.db.initialize()

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_stores_and_reads_narrative_scores(self) -> None:
        rows = [
            {
                "kind": "narrative",
                "name": "AI Agents",
                "mentions_count": 4,
                "average_importance": 8.0,
            },
            {
                "kind": "token",
                "name": "SOL",
                "mentions_count": 5,
                "average_importance": 7.0,
            },
        ]

        self.db.save_narrative_score_history(rows)
        history = self.db.get_top_narrative_history(24)

        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["narrative"], "AI Agents")
        self.assertEqual(history[0]["score"], 32.0)

    def test_growth_compares_last_24h_with_previous_24h(self) -> None:
        self.db.connection.executemany(
            """
            INSERT INTO narrative_score_history (
                narrative, hype_score, mentions_count, average_importance, recorded_at
            )
            VALUES (?, ?, ?, ?, datetime('now', ?))
            """,
            [
                ("RWA", 10.0, 2, 5.0, "-30 hours"),
                ("RWA", 15.0, 3, 5.0, "-1 hour"),
            ],
        )
        self.db.connection.commit()

        growth = self.db.get_fastest_growing_narratives()

        self.assertEqual(growth[0]["narrative"], "RWA")
        self.assertAlmostEqual(growth[0]["growth_percent"], 50.0)

    def test_momentum_inputs_include_mentions_importance_and_recency(self) -> None:
        post = XPost(
            id="momentum-post",
            username="analyst",
            text="AI agents are growing",
            created_at=None,
            url="local://momentum-post",
        )
        analysis = AnalysisResult(
            tokens=[],
            narratives=["AI Agents"],
            sentiment="bullish",
            importance=8,
            summary="AI agents are growing",
        )
        self.db.save_analysis(post, analysis)

        rows = self.db.get_narrative_momentum_inputs()

        self.assertEqual(rows[0]["narrative"], "AI Agents")
        self.assertEqual(rows[0]["mentions_count"], 1)
        self.assertEqual(rows[0]["average_importance"], 8.0)
        self.assertLess(rows[0]["recency_hours"], 1.0)

    def test_daily_momentum_upserts_today_and_reads_seven_day_history(self) -> None:
        self.db.connection.execute(
            """
            INSERT INTO daily_momentum (date, narrative, momentum_score)
            VALUES (date('now', '-7 days'), 'AI Agents', 32)
            """
        )
        self.db.connection.commit()

        self.db.save_daily_momentum([NarrativeMomentum(name="AI Agents", score=80)])
        self.db.save_daily_momentum([NarrativeMomentum(name="AI Agents", score=92)])
        rows = self.db.get_momentum_history_report()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["narrative"], "AI Agents")
        self.assertEqual(rows[0]["seven_days_ago_score"], 32)
        self.assertEqual(rows[0]["today_score"], 92)

    def test_opportunity_inputs_use_latest_and_seven_day_scores(self) -> None:
        self.db.connection.executemany(
            """
            INSERT INTO daily_momentum (date, narrative, momentum_score)
            VALUES (date('now', ?), ?, ?)
            """,
            [
                ("-8 days", "RWA", 44),
                ("-1 day", "RWA", 61),
            ],
        )
        self.db.connection.commit()

        rows = self.db.get_opportunity_inputs()

        self.assertEqual(rows[0]["narrative"], "RWA")
        self.assertEqual(rows[0]["momentum_score"], 61)
        self.assertEqual(rows[0]["seven_days_ago_score"], 44)


if __name__ == "__main__":
    unittest.main()
