import tempfile
import unittest
import sqlite3
from pathlib import Path

from app.ai.analyzer import AnalysisResult
from app.db.database import Database
from app.events.bus import EventBus
from app.events.models import SignalEvaluated
from app.events.subscribers import SignalOutcomeStorage
from app.scoring.signal_outcomes import (
    OutcomeThresholds,
    classify_outcome,
    evaluate_pending_signals,
)
from app.sources.x_client import XPost


class SignalOutcomeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.temp_dir.name) / "test.sqlite3")
        self.db.initialize()

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def test_classifies_success_neutral_and_failure(self) -> None:
        thresholds = OutcomeThresholds(success=10, failure=-10)

        self.assertEqual(classify_outcome(20, 3, 15, 2, thresholds)[0], "SUCCESS")
        self.assertEqual(classify_outcome(1, 0, -1, 2, thresholds)[0], "NEUTRAL")
        self.assertEqual(classify_outcome(-20, -2, -15, 2, thresholds)[0], "FAILED")

    def test_initialize_migrates_existing_signal_history(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy.sqlite3"
        connection = sqlite3.connect(legacy_path)
        connection.execute(
            """
            CREATE TABLE signal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                signal_type TEXT NOT NULL,
                token TEXT,
                narrative TEXT,
                hype_score REAL NOT NULL,
                momentum_score REAL NOT NULL,
                confidence INTEGER NOT NULL,
                action TEXT NOT NULL
            )
            """
        )
        connection.commit()
        connection.close()

        legacy_db = Database(legacy_path)
        legacy_db.initialize()
        columns = {
            row["name"]
            for row in legacy_db.connection.execute("PRAGMA table_info(signal_history)")
        }
        outcome_table = legacy_db.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'signal_outcomes'"
        ).fetchone()
        legacy_db.close()

        self.assertIn("mentions_count", columns)
        self.assertIsNotNone(outcome_table)

    def test_evaluates_mature_signal_once_and_persists_outcome(self) -> None:
        signal_id = self.db.save_signal_history(
            signal_type="token + narrative",
            token="SOL",
            narrative="Solana ecosystem",
            hype_score=20,
            momentum_score=20,
            confidence=8,
            action="research",
            mentions_count=1,
        )
        self.db.connection.execute(
            "UPDATE signal_history SET timestamp = datetime('now', '-25 hours') WHERE id = ?",
            (signal_id,),
        )
        for index in range(3):
            self.db.save_analysis(
                XPost(
                    id=f"post-{index}",
                    username="rss-source",
                    text="SOL activity is accelerating.",
                    created_at=None,
                    url=f"https://example.com/{index}",
                ),
                AnalysisResult(
                    tokens=["SOL"],
                    narratives=["Solana ecosystem"],
                    sentiment="bullish",
                    importance=9,
                    summary="Solana activity is accelerating.",
                ),
            )
        self.db.connection.commit()

        events = []
        event_bus = EventBus()
        event_bus.subscribe(SignalEvaluated, SignalOutcomeStorage(self.db, event_bus))
        event_bus.subscribe(SignalEvaluated, events.append)
        first = evaluate_pending_signals(
            self.db,
            hours_after=24,
            thresholds=OutcomeThresholds(),
            event_bus=event_bus,
        )
        second = evaluate_pending_signals(
            self.db,
            hours_after=24,
            thresholds=OutcomeThresholds(),
        )
        outcome = self.db.connection.execute(
            "SELECT * FROM signal_outcomes WHERE signal_id = ?",
            (signal_id,),
        ).fetchone()

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(outcome["status"], "SUCCESS")
        self.assertEqual(outcome["mentions_change"], 2)
        self.assertGreater(outcome["score_change"], 0)
        self.assertGreater(outcome["momentum_change"], 0)

    def test_outcome_summary_and_narrative_rankings(self) -> None:
        strong_id = self.db.save_signal_history(
            "narrative", None, "AI agents", 70, 80, 8, "research", 4
        )
        weak_id = self.db.save_signal_history(
            "narrative", None, "Memecoins", 50, 60, 5, "ignore", 3
        )
        self.db.save_signal_outcome(strong_id, 24, "SUCCESS", 12, 2, 10, "good")
        self.db.save_signal_outcome(weak_id, 24, "FAILED", -15, -2, -20, "bad")

        summary = self.db.get_signal_outcome_summary()
        best = self.db.get_signal_outcome_narratives("DESC")
        worst = self.db.get_signal_outcome_narratives("ASC")

        self.assertEqual(summary["signals_evaluated"], 2)
        self.assertEqual(summary["success"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(best[0]["name"], "AI agents")
        self.assertEqual(worst[0]["name"], "Memecoins")


if __name__ == "__main__":
    unittest.main()
