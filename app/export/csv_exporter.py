from __future__ import annotations

import csv
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.db.database import Database


SIGNAL_COLUMNS = (
    "id",
    "created_at",
    "signal_type",
    "token",
    "narrative",
    "hype_score",
    "momentum_score",
    "mention_count",
    "confidence",
    "action",
    "status",
)

OUTCOME_COLUMNS = (
    "id",
    "signal_id",
    "signal_created_at",
    "evaluated_at",
    "evaluation_window_hours",
    "status",
    "token",
    "narrative",
    "original_hype_score",
    "current_hype_score",
    "hype_change",
    "original_momentum_score",
    "current_momentum_score",
    "momentum_change",
    "original_mentions",
    "current_mentions",
    "mentions_change",
    "notes",
)

PERFORMANCE_COLUMNS = (
    "report_period",
    "total_signals",
    "evaluated_signals",
    "successful_signals",
    "neutral_signals",
    "failed_signals",
    "success_rate",
    "average_hype_change",
    "average_momentum_change",
    "average_mentions_change",
    "average_confidence",
)

NARRATIVE_PERFORMANCE_COLUMNS = (
    "narrative",
    "signal_count",
    "evaluated_count",
    "successful_count",
    "neutral_count",
    "failed_count",
    "success_rate",
    "average_hype_change",
    "average_momentum_change",
    "average_mentions_change",
)


@dataclass(frozen=True)
class ExportedCSV:
    kind: str
    path: Path
    record_count: int


@dataclass(frozen=True)
class CSVExportResult:
    files: tuple[ExportedCSV, ...]

    def count_for(self, kind: str) -> int:
        return sum(item.record_count for item in self.files if item.kind == kind)


class CSVExportService:
    def __init__(
        self,
        database: Database,
        output_dir: Path,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        self.database = database
        self.output_dir = output_dir
        self.clock = clock

    def export(
        self,
        kinds: Iterable[str],
        from_date: date | None = None,
        to_date: date | None = None,
    ) -> CSVExportResult:
        selected = tuple(dict.fromkeys(kinds))
        unsupported = set(selected) - {"signals", "outcomes", "performance"}
        if unsupported:
            raise ValueError(f"Unsupported CSV export type: {sorted(unsupported)[0]}")
        if from_date and to_date and from_date > to_date:
            raise ValueError("--from-date cannot be after --to-date")

        self.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = self.clock().strftime("%Y-%m-%d_%H%M%S")
        from_value = from_date.isoformat() if from_date else None
        to_value = to_date.isoformat() if to_date else None
        files: list[ExportedCSV] = []

        if "signals" in selected:
            files.append(self._export_signals(timestamp, from_value, to_value))
        if "outcomes" in selected:
            files.append(self._export_outcomes(timestamp, from_value, to_value))
        if "performance" in selected:
            files.extend(self._export_performance(timestamp, from_value, to_value))
        return CSVExportResult(tuple(files))

    def _export_signals(
        self,
        timestamp: str,
        from_date: str | None,
        to_date: str | None,
    ) -> ExportedCSV:
        rows = [
            {
                "id": row["id"],
                "created_at": _iso_timestamp(row["timestamp"]),
                "signal_type": row["signal_type"],
                "token": row["token"],
                "narrative": row["narrative"],
                "hype_score": row["hype_score"],
                "momentum_score": row["momentum_score"],
                "mention_count": row["mentions_count"],
                "confidence": row["confidence"],
                "action": row["action"],
                "status": row["outcome_status"],
            }
            for row in self.database.get_signals(
                limit=None,
                from_date=from_date,
                to_date=to_date,
            )
        ]
        path = self._write("signals", timestamp, SIGNAL_COLUMNS, rows)
        return ExportedCSV("signals", path, len(rows))

    def _export_outcomes(
        self,
        timestamp: str,
        from_date: str | None,
        to_date: str | None,
    ) -> ExportedCSV:
        rows = [
            {
                "id": row["id"],
                "signal_id": row["signal_id"],
                "signal_created_at": _iso_timestamp(row["signal_timestamp"]),
                "evaluated_at": _iso_timestamp(row["evaluated_at"]),
                "evaluation_window_hours": row["evaluation_window_hours"],
                "status": row["status"],
                "token": row["token"],
                "narrative": row["narrative"],
                "original_hype_score": row["original_hype_score"],
                "current_hype_score": row["current_hype_score"],
                "hype_change": row["hype_change"],
                "original_momentum_score": row["original_momentum_score"],
                "current_momentum_score": row["current_momentum_score"],
                "momentum_change": row["momentum_change"],
                "original_mentions": row["original_mentions"],
                "current_mentions": row["current_mentions"],
                "mentions_change": row["mentions_change"],
                "notes": row["notes"],
            }
            for row in self.database.get_signal_outcomes(
                limit=None,
                from_date=from_date,
                to_date=to_date,
            )
        ]
        path = self._write("outcomes", timestamp, OUTCOME_COLUMNS, rows)
        return ExportedCSV("outcomes", path, len(rows))

    def _export_performance(
        self,
        timestamp: str,
        from_date: str | None,
        to_date: str | None,
    ) -> tuple[ExportedCSV, ExportedCSV]:
        signals = self.database.get_signal_performance_summary(from_date, to_date)
        outcomes = self.database.get_signal_outcome_summary(
            from_date=from_date,
            to_date=to_date,
        )
        evaluated = int(outcomes["signals_evaluated"] or 0)
        successful = int(outcomes["success"] or 0)
        performance_rows = [
            {
                "report_period": _report_period(from_date, to_date),
                "total_signals": int(signals["signals_generated"] or 0),
                "evaluated_signals": evaluated,
                "successful_signals": successful,
                "neutral_signals": int(outcomes["neutral"] or 0),
                "failed_signals": int(outcomes["failed"] or 0),
                "success_rate": successful / evaluated * 100 if evaluated else 0.0,
                "average_hype_change": outcomes["average_hype_change"],
                "average_momentum_change": outcomes["average_momentum_change"],
                "average_mentions_change": outcomes["average_mention_change"],
                "average_confidence": signals["average_confidence"],
            }
        ]
        performance_path = self._write(
            "performance",
            timestamp,
            PERFORMANCE_COLUMNS,
            performance_rows,
        )

        narrative_rows = [
            dict(row)
            for row in self.database.get_narrative_performance_summary(
                from_date,
                to_date,
            )
        ]
        narrative_path = self._write(
            "narrative_performance",
            timestamp,
            NARRATIVE_PERFORMANCE_COLUMNS,
            narrative_rows,
        )
        return (
            ExportedCSV("performance", performance_path, 1),
            ExportedCSV("narrative_performance", narrative_path, len(narrative_rows)),
        )

    def _write(
        self,
        stem: str,
        timestamp: str,
        columns: Sequence[str],
        rows: Sequence[Mapping[str, object]],
    ) -> Path:
        path = self._unique_path(stem, timestamp)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({column: _cell(row.get(column)) for column in columns})
        return path

    def _unique_path(self, stem: str, timestamp: str) -> Path:
        path = self.output_dir / f"{stem}_{timestamp}.csv"
        suffix = 2
        while path.exists():
            path = self.output_dir / f"{stem}_{timestamp}_{suffix}.csv"
            suffix += 1
        return path


def _cell(value: object) -> object:
    return "" if value is None else value


def _iso_timestamp(value: object) -> str:
    if value is None:
        return ""
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return text


def _report_period(from_date: str | None, to_date: str | None) -> str:
    if from_date and to_date:
        return f"{from_date} to {to_date}"
    if from_date:
        return f"from {from_date}"
    if to_date:
        return f"through {to_date}"
    return "all time"
