import csv
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from app.db.database import Database
from app.export.csv_exporter import (
    NARRATIVE_PERFORMANCE_COLUMNS,
    OUTCOME_COLUMNS,
    PERFORMANCE_COLUMNS,
    SIGNAL_COLUMNS,
    CSVExportService,
)
from app.main import parse_args, requested_csv_exports


FIXED_NOW = datetime(2026, 7, 21, 18, 0, 0)


class CSVExportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db = Database(self.root / "test.sqlite3")
        self.db.initialize()
        self.output_dir = self.root / "nested" / "exports"

    def tearDown(self) -> None:
        self.db.close()
        self.temp_dir.cleanup()

    def _service(self) -> CSVExportService:
        return CSVExportService(self.db, self.output_dir, clock=lambda: FIXED_NOW)

    def _populate(self) -> tuple[int, int]:
        narrative = 'AI, "Agents"\nInfrastructure'
        signal_id = self.db.save_signal_history(
            "token + narrative",
            "TAO",
            narrative,
            72,
            81,
            8,
            "research, then\nwatch",
            3,
        )
        second_id = self.db.save_signal_history(
            "token",
            "BTC",
            None,
            55,
            60,
            6,
            "watch",
            None,
        )
        self.db.connection.execute(
            "UPDATE signal_history SET timestamp = ? WHERE id = ?",
            ("2026-07-20 10:30:00", signal_id),
        )
        self.db.connection.execute(
            "UPDATE signal_history SET timestamp = ? WHERE id = ?",
            ("2026-07-18 09:00:00", second_id),
        )
        outcome_id = self.db.save_signal_outcome(
            signal_id,
            24,
            "SUCCESS",
            12.5,
            2,
            9.5,
            'Attention rose, with "strong" follow-through.\nSecond line.',
            original_hype_score=72,
            current_hype_score=84.5,
            original_momentum_score=81,
            current_momentum_score=90.5,
            original_mentions=3,
            current_mentions=5,
        )
        self.db.connection.execute(
            "UPDATE signal_outcomes SET evaluated_at = ? WHERE id = ?",
            ("2026-07-21 12:00:00", outcome_id),
        )
        self.db.connection.commit()
        return signal_id, int(outcome_id)

    @staticmethod
    def _read(path: Path) -> list[dict[str, str]]:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def test_signals_csv_headers_rows_escaping_and_missing_values(self) -> None:
        self._populate()

        result = self._service().export(["signals"])
        exported = result.files[0]
        rows = self._read(exported.path)

        self.assertEqual(exported.record_count, 2)
        self.assertEqual(tuple(rows[0]), SIGNAL_COLUMNS)
        tao = next(row for row in rows if row["token"] == "TAO")
        btc = next(row for row in rows if row["token"] == "BTC")
        self.assertEqual(tao["created_at"], "2026-07-20T10:30:00")
        self.assertEqual(tao["narrative"], 'AI, "Agents"\nInfrastructure')
        self.assertEqual(tao["action"], "research, then\nwatch")
        self.assertEqual(tao["status"], "SUCCESS")
        self.assertEqual(btc["narrative"], "")
        self.assertEqual(btc["mention_count"], "")

    def test_outcomes_csv_headers_rows_and_multiline_notes(self) -> None:
        self._populate()

        result = self._service().export(["outcomes"])
        rows = self._read(result.files[0].path)

        self.assertEqual(tuple(rows[0]), OUTCOME_COLUMNS)
        self.assertEqual(rows[0]["signal_created_at"], "2026-07-20T10:30:00")
        self.assertEqual(rows[0]["evaluated_at"], "2026-07-21T12:00:00")
        self.assertEqual(rows[0]["hype_change"], "12.5")
        self.assertEqual(
            rows[0]["notes"],
            'Attention rose, with "strong" follow-through.\nSecond line.',
        )

    def test_performance_and_narrative_performance_exports(self) -> None:
        self._populate()

        result = self._service().export(["performance"])
        performance = next(item for item in result.files if item.kind == "performance")
        narratives = next(
            item for item in result.files if item.kind == "narrative_performance"
        )
        performance_rows = self._read(performance.path)
        narrative_rows = self._read(narratives.path)

        self.assertEqual(tuple(performance_rows[0]), PERFORMANCE_COLUMNS)
        self.assertEqual(performance_rows[0]["report_period"], "all time")
        self.assertEqual(performance_rows[0]["total_signals"], "2")
        self.assertEqual(performance_rows[0]["evaluated_signals"], "1")
        self.assertEqual(performance_rows[0]["successful_signals"], "1")
        self.assertEqual(performance_rows[0]["success_rate"], "100.0")
        self.assertEqual(performance_rows[0]["average_confidence"], "7.0")
        self.assertEqual(tuple(narrative_rows[0]), NARRATIVE_PERFORMANCE_COLUMNS)
        self.assertEqual(narrative_rows[0]["signal_count"], "1")
        self.assertEqual(narrative_rows[0]["successful_count"], "1")

    def test_files_have_utf8_bom_and_output_directory_is_created(self) -> None:
        self._populate()

        result = self._service().export(["signals", "outcomes"])

        self.assertTrue(self.output_dir.is_dir())
        for item in result.files:
            self.assertEqual(item.path.read_bytes()[:3], b"\xef\xbb\xbf")

    def test_empty_database_still_writes_valid_headers(self) -> None:
        result = self._service().export(["signals", "outcomes", "performance"])

        signals = next(item for item in result.files if item.kind == "signals")
        outcomes = next(item for item in result.files if item.kind == "outcomes")
        narratives = next(
            item for item in result.files if item.kind == "narrative_performance"
        )
        self.assertEqual(signals.record_count, 0)
        self.assertEqual(outcomes.record_count, 0)
        self.assertEqual(narratives.record_count, 0)
        self.assertEqual(signals.path.read_text(encoding="utf-8-sig").strip(), ",".join(SIGNAL_COLUMNS))
        self.assertEqual(outcomes.path.read_text(encoding="utf-8-sig").strip(), ",".join(OUTCOME_COLUMNS))
        self.assertEqual(
            narratives.path.read_text(encoding="utf-8-sig").strip(),
            ",".join(NARRATIVE_PERFORMANCE_COLUMNS),
        )

    def test_date_filters_are_inclusive_and_apply_to_each_dataset(self) -> None:
        self._populate()

        result = self._service().export(
            ["signals", "outcomes", "performance"],
            from_date=date(2026, 7, 20),
            to_date=date(2026, 7, 21),
        )

        self.assertEqual(result.count_for("signals"), 1)
        self.assertEqual(result.count_for("outcomes"), 1)
        performance = next(item for item in result.files if item.kind == "performance")
        row = self._read(performance.path)[0]
        self.assertEqual(row["report_period"], "2026-07-20 to 2026-07-21")
        self.assertEqual(row["total_signals"], "1")
        self.assertEqual(row["evaluated_signals"], "1")

        empty = self._service().export(
            ["signals", "outcomes"],
            from_date=date(2026, 7, 22),
        )
        self.assertEqual(empty.count_for("signals"), 0)
        self.assertEqual(empty.count_for("outcomes"), 0)

    def test_unique_filenames_do_not_overwrite(self) -> None:
        first = self._service().export(["signals"]).files[0]
        second = self._service().export(["signals"]).files[0]

        self.assertEqual(first.path.name, "signals_2026-07-21_180000.csv")
        self.assertEqual(second.path.name, "signals_2026-07-21_180000_2.csv")
        self.assertTrue(first.path.exists())
        self.assertTrue(second.path.exists())

    def test_invalid_date_range_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be after"):
            self._service().export(
                ["signals"],
                from_date=date(2026, 7, 22),
                to_date=date(2026, 7, 21),
            )

    def test_cli_command_exports_empty_database(self) -> None:
        database_path = self.root / "cli.sqlite3"
        output_path = self.root / "cli-exports"
        environment = os.environ.copy()
        environment["DATABASE_PATH"] = str(database_path)
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "app.main",
                "--export-signals-csv",
                "--output-dir",
                str(output_path),
                "--from-date",
                "2026-07-01",
            ],
            cwd=Path(__file__).resolve().parent.parent,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("CSV export complete", completed.stderr)
        self.assertIn("Signals exported: 0", completed.stderr)
        self.assertEqual(len(list(output_path.glob("signals_*.csv"))), 1)

    def test_combined_and_existing_cli_arguments_remain_compatible(self) -> None:
        with patch.object(sys, "argv", ["main.py", "--export-csv", "all"]):
            export_args = parse_args()
        self.assertEqual(
            requested_csv_exports(export_args),
            ("signals", "outcomes", "performance"),
        )

        with patch.object(sys, "argv", ["main.py", "--mode", "rss", "--mock-ai"]):
            existing_args = parse_args()
        self.assertEqual(existing_args.mode, "rss")
        self.assertTrue(existing_args.mock_ai)
        self.assertEqual(requested_csv_exports(existing_args), ())


if __name__ == "__main__":
    unittest.main()
