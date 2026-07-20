import tempfile
import unittest
import sqlite3
from pathlib import Path

from app.ai.analyzer import AnalysisResult
from app.db.database import Database
from app.events.bus import EventBus
from app.events.models import SignalEvaluationRequested, SignalEvaluated
from app.events.subscribers import SignalOutcomeStorage
from app.main import build_outcome_report
from app.scoring.signal_outcomes import (
    OutcomeThresholds,
    OutcomeEvaluator,
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

    def test_threshold_boundaries_and_success_precedence(self) -> None:
        thresholds = OutcomeThresholds(success=10, failure=-10)

        self.assertEqual(classify_outcome(10, 0, 0, 1, thresholds)[0], "SUCCESS")
        self.assertEqual(classify_outcome(0, 0, -10, 1, thresholds)[0], "FAILED")
        self.assertEqual(classify_outcome(9.99, 0, -9.99, 1, thresholds)[0], "NEUTRAL")
        self.assertEqual(classify_outcome(10, 0, -20, 1, thresholds)[0], "SUCCESS")

        with self.assertRaisesRegex(ValueError, "below success"):
            OutcomeThresholds(success=10, failure=10)

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

    def test_initialize_backfills_legacy_outcome_columns(self) -> None:
        legacy_path = Path(self.temp_dir.name) / "legacy-outcomes.sqlite3"
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE signal_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                signal_type TEXT NOT NULL, token TEXT, narrative TEXT,
                hype_score REAL NOT NULL, momentum_score REAL NOT NULL,
                confidence INTEGER NOT NULL, action TEXT NOT NULL,
                mentions_count INTEGER
            );
            CREATE TABLE signal_outcomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id INTEGER NOT NULL,
                evaluated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                hours_after INTEGER NOT NULL, status TEXT NOT NULL,
                score_change REAL NOT NULL, mentions_change INTEGER NOT NULL,
                momentum_change REAL NOT NULL, notes TEXT NOT NULL,
                UNIQUE(signal_id, hours_after)
            );
            INSERT INTO signal_history VALUES
                (1, CURRENT_TIMESTAMP, 'narrative', NULL, 'RWA', 40, 50, 7, 'watch', 3);
            INSERT INTO signal_outcomes
                (signal_id, hours_after, status, score_change, mentions_change, momentum_change, notes)
                VALUES (1, 24, 'SUCCESS', 12, 2, 15, 'legacy');
            """
        )
        connection.commit()
        connection.close()

        legacy_db = Database(legacy_path)
        legacy_db.initialize()
        outcome = legacy_db.get_signal_outcomes(signal_id=1)[0]
        legacy_db.close()

        self.assertEqual(outcome["evaluation_window_hours"], 24)
        self.assertEqual(outcome["original_hype_score"], 40)
        self.assertEqual(outcome["current_hype_score"], 52)
        self.assertEqual(outcome["current_mentions"], 5)

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
        self.assertEqual(summary["average_hype_change"], -1.5)
        self.assertEqual(best[0]["success_rate"], 100.0)

        report = build_outcome_report(self.db)
        self.assertEqual(report.signals_evaluated, 2)
        self.assertEqual(report.success_rate, 50.0)
        self.assertEqual(report.average_hype_change, -1.5)
        self.assertEqual(report.best_narratives[0].outcome_score, 100.0)

    def test_missing_current_metrics_are_safe_and_failed(self) -> None:
        signal_id = self.db.save_signal_history(
            "token", "SOL", None, 40, 40, 7, "watch", 4
        )
        self.db.connection.execute(
            "UPDATE signal_history SET timestamp = datetime('now', '-25 hours') WHERE id = ?",
            (signal_id,),
        )
        self.db.connection.commit()

        count = OutcomeEvaluator(self.db, OutcomeThresholds()).evaluate_due([24])
        outcome = self.db.get_signal_outcomes(signal_id=signal_id)[0]

        self.assertEqual(count, 1)
        self.assertEqual(outcome["status"], "FAILED")
        self.assertEqual(outcome["current_mentions"], 0)
        self.assertIn("treated as zero", outcome["notes"])

    def test_multiple_windows_publish_events_and_do_not_duplicate(self) -> None:
        signal_id = self.db.save_signal_history(
            "narrative", None, "DePIN", 30, 30, 7, "research", 2
        )
        self.db.connection.execute(
            "UPDATE signal_history SET timestamp = datetime('now', '-169 hours') WHERE id = ?",
            (signal_id,),
        )
        self.db.connection.commit()
        requested = []
        evaluated = []
        bus = EventBus()
        bus.subscribe(SignalEvaluationRequested, requested.append)
        bus.subscribe(SignalEvaluated, evaluated.append)
        evaluator = OutcomeEvaluator(self.db, OutcomeThresholds(), bus)

        first = evaluator.evaluate_due([24, 72, 168])
        second = evaluator.evaluate_due([24, 72, 168])
        rows = self.db.get_signal_outcomes(signal_id=signal_id)

        self.assertEqual(first, 3)
        self.assertEqual(second, 0)
        self.assertEqual({row["evaluation_window_hours"] for row in rows}, {24, 72, 168})
        self.assertEqual(len(evaluated), 3)
        self.assertEqual(len(requested), 6)
        self.assertTrue(all(event.outcome_id for event in evaluated))


if __name__ == "__main__":
    unittest.main()
