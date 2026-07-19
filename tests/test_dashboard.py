import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.ai.analyzer import AnalysisResult
from app.dashboard.app import create_app
from app.db.database import Database
from app.events import EventBus, PerformanceUpdated
from app.scoring.momentum_score import NarrativeMomentum
from app.sources.x_client import XPost


class DashboardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temp_dir.name) / "dashboard.sqlite3"
        db = Database(self.database_path)
        db.initialize()
        db.save_analysis(
            XPost(
                id="article-1",
                username="CoinDesk",
                text="SOL activity is rising across the Solana ecosystem.",
                created_at="2026-07-19T08:00:00Z",
                url="https://example.com/sol",
            ),
            AnalysisResult(
                tokens=["SOL"],
                narratives=["Solana ecosystem"],
                sentiment="bullish",
                importance=9,
                summary="Solana activity is rising.",
            ),
        )
        signal_id = db.save_signal_history(
            signal_type="token + narrative",
            token="SOL",
            narrative="Solana ecosystem",
            hype_score=72,
            momentum_score=81,
            confidence=8,
            action="research",
            mentions_count=3,
        )
        db.save_signal_outcome(
            signal_id=signal_id,
            hours_after=24,
            status="SUCCESS",
            score_change=12,
            mentions_change=2,
            momentum_change=11,
            notes="Growing signal",
        )
        db.save_daily_momentum(
            [NarrativeMomentum(name="Solana ecosystem", score=81)]
        )
        db.close()
        self.event_bus = EventBus()
        self.client = TestClient(create_app(self.database_path, self.event_bus))

    def tearDown(self) -> None:
        self.client.close()
        self.temp_dir.cleanup()

    def test_dashboard_pages_render(self) -> None:
        for path, heading in (
            ("/", "Overview"),
            ("/signals", "Signals"),
            ("/performance", "Performance"),
            ("/narratives", "Narratives"),
            ("/tokens", "Tokens"),
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn(heading, response.text)
            self.assertIn("bootstrap@5.3.3", response.text)
            self.assertIn("dashboard.js", response.text)

    def test_signals_endpoint_returns_latest_signal_and_outcome(self) -> None:
        response = self.client.get("/api/signals")

        self.assertEqual(response.status_code, 200)
        signal = response.json()["signals"][0]
        self.assertEqual(signal["token"], "SOL")
        self.assertEqual(signal["outcome_status"], "SUCCESS")
        self.assertEqual(signal["momentum_score"], 81.0)

    def test_performance_endpoint_uses_evaluated_outcomes(self) -> None:
        data = self.client.get("/api/performance").json()

        self.assertEqual(data["signals_generated"], 1)
        self.assertEqual(data["signals_evaluated"], 1)
        self.assertEqual(data["accuracy"], 100.0)
        self.assertEqual(data["best_narratives"][0]["name"], "Solana ecosystem")

    def test_narrative_and_token_endpoints_use_recent_analysis(self) -> None:
        narratives = self.client.get("/api/narratives").json()["narratives"]
        tokens = self.client.get("/api/tokens").json()["tokens"]

        self.assertEqual(narratives[0]["name"], "Solana ecosystem")
        self.assertEqual(narratives[0]["momentum_score"], 81)
        self.assertEqual(tokens[0]["name"], "SOL")
        self.assertGreater(tokens[0]["hype_score"], 0)

    def test_status_endpoint_reports_database_and_event_activity(self) -> None:
        self.event_bus.publish(
            PerformanceUpdated(
                signals_generated=1,
                signals_evaluated=1,
                success_rate=100.0,
            )
        )

        data = self.client.get("/api/status").json()

        self.assertEqual(data["status"], "operational")
        self.assertEqual(data["analyzed_posts"], 1)
        self.assertEqual(data["signals"], 1)
        self.assertIsNotNone(data["last_event_at"])

    def test_api_limits_are_validated(self) -> None:
        self.assertEqual(self.client.get("/api/signals?limit=0").status_code, 422)
        self.assertEqual(self.client.get("/api/tokens?limit=101").status_code, 422)

    def test_empty_legacy_database_is_served_without_migration(self) -> None:
        empty_path = Path(self.temp_dir.name) / "empty.sqlite3"
        empty_client = TestClient(create_app(empty_path))

        self.assertEqual(empty_client.get("/api/status").json()["signals"], 0)
        self.assertEqual(empty_client.get("/api/signals").json()["signals"], [])
        self.assertEqual(
            empty_client.get("/api/performance").json()["signals_generated"],
            0,
        )
        self.assertEqual(empty_client.get("/").status_code, 200)
        empty_client.close()


if __name__ == "__main__":
    unittest.main()
