import csv
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.analytics.historical import (
    HistoricalAnalyticsService,
    HistoricalThresholds,
    format_historical_report,
    parse_period,
)
from app.db.database import Database
from app.export.csv_exporter import CSVExportService


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


class HistoricalAnalyticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.database_path = self.root / "history.sqlite3"
        self.db = Database(self.database_path)
        self.db.initialize()

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def _add_signal(
        self,
        timestamp: str,
        narrative: str,
        token: str,
        hype: float,
        momentum: float,
        mentions: int,
        status: str,
    ) -> int:
        signal_id = self.db.save_signal_history(
            "token + narrative",
            token,
            narrative,
            hype,
            momentum,
            8,
            "research",
            mentions,
        )
        self.db.connection.execute(
            "UPDATE signal_history SET timestamp = ? WHERE id = ?",
            (timestamp, signal_id),
        )
        change = 10 if status == "SUCCESS" else 0 if status == "NEUTRAL" else -10
        outcome_id = self.db.save_signal_outcome(
            signal_id,
            24,
            status,
            change,
            int(change / 5),
            change,
            "Historical fixture",
        )
        self.db.connection.execute(
            "UPDATE signal_outcomes SET evaluated_at = ? WHERE id = ?",
            (timestamp, outcome_id),
        )
        self.db.connection.commit()
        return signal_id

    def _populate(self) -> None:
        rows = [
            ("2026-07-01 10:00:00", "AI Agents", "TAO", 70, 65, 4, "SUCCESS"),
            ("2026-07-08 10:00:00", "AI Agents", "TAO", 75, 70, 5, "SUCCESS"),
            ("2026-07-15 10:00:00", "AI Agents", "TAO", 80, 75, 6, "SUCCESS"),
            ("2026-07-21 10:00:00", "AI Agents", "TAO", 85, 80, 7, "SUCCESS"),
            ("2026-06-01 10:00:00", "AI Agents", "TAO", 40, 40, 2, "SUCCESS"),
            ("2026-06-15 10:00:00", "AI Agents", "TAO", 45, 45, 2, "SUCCESS"),
            ("2026-07-02 10:00:00", "Stablecoins", "USDC", 50, 50, 3, "SUCCESS"),
            ("2026-07-16 10:00:00", "Stablecoins", "USDC", 50, 50, 3, "NEUTRAL"),
            ("2026-06-02 10:00:00", "Stablecoins", "USDC", 50, 50, 3, "SUCCESS"),
            ("2026-06-16 10:00:00", "Stablecoins", "USDC", 50, 50, 3, "NEUTRAL"),
            ("2026-07-03 10:00:00", "Gaming", "GALA", 35, 30, 1, "FAILED"),
            ("2026-06-03 10:00:00", "Gaming", "GALA", 70, 65, 4, "SUCCESS"),
            ("2026-06-10 10:00:00", "Gaming", "GALA", 65, 60, 4, "FAILED"),
            ("2026-06-20 10:00:00", "Gaming", "GALA", 60, 55, 3, "FAILED"),
            ("2026-07-05 10:00:00", "RWA", "ONDO", 70, 70, 4, "SUCCESS"),
            ("2026-07-19 10:00:00", "RWA", "ONDO", 80, 80, 5, "NEUTRAL"),
            ("2026-06-04 10:00:00", "Memecoins", "PEPE", 40, 40, 3, "FAILED"),
            ("2026-06-18 10:00:00", "Memecoins", "PEPE", 35, 35, 2, "FAILED"),
        ]
        for row in rows:
            self._add_signal(*row)

    def _service(self) -> HistoricalAnalyticsService:
        return HistoricalAnalyticsService(
            self.db,
            HistoricalThresholds(growth_percent=20, minimum_activity=2),
            clock=lambda tz=None: NOW,
        )

    def test_supported_periods_and_bucket_cadence(self) -> None:
        self._populate()

        seven = self._service().build_report("7d")
        thirty = self._service().build_report("30d")
        ninety = self._service().build_report("90d")
        all_time = self._service().build_report("all")

        self.assertEqual(len(seven.timeline), 7)
        self.assertTrue(all(item.bucket_start == item.bucket_end for item in seven.timeline))
        self.assertGreaterEqual(len(thirty.timeline), 5)
        self.assertTrue(
            all(datetime.fromisoformat(item.bucket_start).weekday() == 0 for item in thirty.timeline)
        )
        self.assertGreaterEqual(len(ninety.timeline), 13)
        self.assertEqual([item.bucket_start for item in all_time.timeline], [
            "2026-06-01",
            "2026-07-01",
        ])
        self.assertEqual(parse_period("30D").key, "30d")

    def test_summary_success_rates_and_averages(self) -> None:
        self._populate()

        summary = self._service().build_report("30d").summary

        self.assertEqual(summary.total_signals, 9)
        self.assertEqual(summary.evaluated_signals, 9)
        self.assertEqual(summary.successful_signals, 6)
        self.assertEqual(summary.neutral_signals, 2)
        self.assertEqual(summary.failed_signals, 1)
        self.assertAlmostEqual(summary.success_rate, 66.666, places=2)
        self.assertIsNotNone(summary.average_hype_score)
        self.assertIsNotNone(summary.average_hype_change)

    def test_current_previous_growth_classification_and_rank_change(self) -> None:
        self._populate()
        narratives = {
            item.name: item for item in self._service().build_report("30d").narratives
        }

        self.assertEqual(narratives["AI Agents"].trend, "RISING")
        self.assertEqual(narratives["Stablecoins"].trend, "STABLE")
        self.assertEqual(narratives["Gaming"].trend, "DECLINING")
        self.assertEqual(narratives["RWA"].trend, "NEW")
        self.assertEqual(narratives["Memecoins"].trend, "INACTIVE")
        self.assertEqual(narratives["AI Agents"].growth.signal_count_growth, 100.0)
        self.assertIsNone(narratives["RWA"].growth.signal_count_growth)
        self.assertEqual(narratives["AI Agents"].current_rank, 1)
        self.assertGreater(narratives["AI Agents"].rank_change, 0)
        self.assertIsNone(narratives["Memecoins"].current_rank)

    def test_token_analytics_use_stored_tokens(self) -> None:
        self._populate()
        tokens = {item.name: item for item in self._service().build_report("30d").tokens}

        self.assertEqual(tokens["TAO"].signal_count, 4)
        self.assertEqual(tokens["TAO"].trend, "RISING")
        self.assertEqual(tokens["PEPE"].trend, "INACTIVE")

    def test_consistency_score_is_bounded_and_rewards_repeat_activity(self) -> None:
        self._populate()
        narratives = {
            item.name: item for item in self._service().build_report("30d").narratives
        }

        self.assertGreater(narratives["Stablecoins"].consistency_score, 0)
        self.assertLessEqual(narratives["Stablecoins"].consistency_score, 100)
        self.assertGreater(
            narratives["Stablecoins"].consistency_score,
            narratives["Gaming"].consistency_score,
        )

    def test_no_previous_data_and_division_by_zero_return_null_growth(self) -> None:
        self._add_signal("2026-07-10 10:00:00", "DePIN", "HNT", 60, 55, 2, "SUCCESS")
        self._add_signal("2026-07-20 10:00:00", "DePIN", "HNT", 65, 60, 2, "SUCCESS")

        entity = self._service().build_report("30d").narratives[0]

        self.assertEqual(entity.trend, "NEW")
        self.assertIsNone(entity.growth.signal_count_growth)
        self.assertIsNone(entity.growth.average_hype_growth)
        self.assertIsNone(entity.growth.success_rate_change)

    def test_empty_database_returns_zero_summary_and_empty_entities(self) -> None:
        report = self._service().build_report("30d")

        self.assertEqual(report.summary.total_signals, 0)
        self.assertEqual(report.summary.success_rate, 0.0)
        self.assertEqual(report.narratives, ())
        self.assertEqual(report.tokens, ())
        self.assertGreaterEqual(len(report.timeline), 1)

    def test_detail_includes_timeline_recent_signals_and_outcomes(self) -> None:
        self._populate()

        detail = self._service().entity_detail("narrative", "AI Agents", "30d")

        self.assertIsNotNone(detail)
        self.assertEqual(detail.analytics.first_seen, "2026-06-01T10:00:00+00:00")
        self.assertEqual(detail.analytics.last_seen, "2026-07-21T10:00:00+00:00")
        self.assertEqual(len(detail.recent_signals), 6)
        self.assertEqual(len(detail.recent_outcomes), 6)
        self.assertGreaterEqual(len(detail.timeline), 5)

    def test_report_formatter_and_cli(self) -> None:
        self._populate()
        text = format_historical_report(self._service().build_report("30d"))

        self.assertIn("Historical Analytics - 30 days", text)
        self.assertIn("Fastest-growing narratives", text)
        self.assertIn("Most consistent narratives", text)

        self.db.close()
        environment = os.environ.copy()
        environment["DATABASE_PATH"] = str(self.database_path)
        completed = subprocess.run(
            [sys.executable, "-m", "app.main", "--history-report", "--period", "30d"],
            cwd=Path(__file__).resolve().parent.parent,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.db = Database(self.database_path)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("Historical Analytics - 30 days", completed.stderr)

    def test_history_csv_reuses_bom_writer(self) -> None:
        self._populate()
        output = self.root / "exports"
        service = CSVExportService(
            self.db,
            output,
            clock=lambda: NOW.replace(tzinfo=None),
        )

        result = service.export(["history"], history_period="30d")

        self.assertEqual(len(result.files), 4)
        self.assertEqual({item.kind for item in result.files}, {
            "history_summary",
            "history_timeline",
            "narrative_history",
            "token_history",
        })
        for item in result.files:
            self.assertEqual(item.path.read_bytes()[:3], b"\xef\xbb\xbf")
        narrative_file = next(item.path for item in result.files if item.kind == "narrative_history")
        with narrative_file.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertIn("trend", rows[0])
        self.assertIn("consistency_score", rows[0])

    def test_invalid_period_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "period must be one of"):
            self._service().build_report("14d")

    def test_backward_compatible_indexes_exist(self) -> None:
        indexes = {
            row["name"]
            for row in self.db.connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }

        self.assertIn("idx_signal_history_timestamp", indexes)
        self.assertIn("idx_signal_history_narrative_timestamp", indexes)
        self.assertIn("idx_signal_history_token_timestamp", indexes)
        self.assertIn("idx_signal_outcomes_evaluated_at", indexes)


if __name__ == "__main__":
    unittest.main()
