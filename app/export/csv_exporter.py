from __future__ import annotations

import csv
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.analytics.historical import HistoricalAnalyticsService, HistoricalThresholds
from app.db.database import Database
from app.watchlists import WatchlistService


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
    "csv_export_marker",
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

HISTORY_SUMMARY_COLUMNS = (
    "period",
    "generated_at",
    "total_signals",
    "evaluated_signals",
    "successful_signals",
    "neutral_signals",
    "failed_signals",
    "success_rate",
    "average_hype_score",
    "average_momentum_score",
    "average_confidence",
    "average_hype_change",
    "average_momentum_change",
    "average_mentions_change",
)

HISTORY_TIMELINE_COLUMNS = (
    "bucket_start",
    "bucket_end",
    "signal_count",
    "evaluated_count",
    "success_rate",
    "average_hype_score",
    "average_momentum_score",
    "average_confidence",
    "average_hype_change",
    "average_momentum_change",
    "average_mentions_change",
)

ENTITY_HISTORY_COLUMNS = (
    "name",
    "signal_count",
    "evaluated_count",
    "success_count",
    "neutral_count",
    "failed_count",
    "success_rate",
    "average_hype_score",
    "average_momentum_score",
    "average_hype_change",
    "average_momentum_change",
    "average_mentions_change",
    "mention_count",
    "first_seen",
    "last_seen",
    "active_days",
    "current_rank",
    "previous_period_rank",
    "rank_change",
    "trend",
    "signal_count_growth",
    "average_hype_growth",
    "average_momentum_growth",
    "mention_growth",
    "success_rate_change",
    "consistency_score",
    "latest_hype_score",
    "latest_momentum_score",
)

WATCHLIST_COLUMNS = (
    "id",
    "name",
    "description",
    "enabled",
    "priority",
    "minimum_hype_score",
    "minimum_momentum_score",
    "minimum_confidence",
    "telegram_enabled",
    "include_in_digest",
    "dashboard_highlight",
    "case_insensitive",
    "token_count",
    "narrative_count",
    "signal_count",
    "evaluated_count",
    "success_rate",
    "last_matched_at",
    "created_at",
    "updated_at",
)

WATCHLIST_ITEM_COLUMNS = (
    "id",
    "watchlist_id",
    "watchlist_name",
    "item_type",
    "item_value",
    "created_at",
)

WATCHLIST_SIGNAL_COLUMNS = (
    "watchlist_id",
    "watchlist_name",
    "signal_id",
    "signal_created_at",
    "matched_at",
    "matched_item_types",
    "matched_item_values",
    "signal_type",
    "token",
    "narrative",
    "hype_score",
    "momentum_score",
    "confidence",
    "mention_count",
    "action",
    "outcome_status",
)

SOURCE_COLUMNS = (
    "id", "source_key", "name", "source_type", "url", "enabled", "priority",
    "fetch_interval_seconds", "last_fetch_at", "last_success_at", "last_error_at",
    "consecutive_failures", "successful_fetches", "failed_fetches", "success_rate",
    "average_latency_ms",
)

CONTENT_ITEM_COLUMNS = (
    "id", "source_id", "source_key", "source_name", "external_id", "title", "body",
    "canonical_url", "author", "published_at", "fetched_at", "language", "status",
    "duplicate_reason", "duplicate_of_content_item_id", "unified_event_id",
)

UNIFIED_EVENT_COLUMNS = (
    "id", "event_key", "title", "summary", "token", "narrative", "tokens_json",
    "narratives_json", "first_seen_at", "last_seen_at", "source_count", "item_count",
    "duplicate_count", "highest_source_priority", "hype_score", "momentum_score",
    "confidence", "conflict_count", "requires_review", "material_version", "status",
)

DEDUPLICATION_COLUMNS = (
    "period_days", "raw_items", "accepted_items", "exact_duplicates",
    "near_duplicates", "unified_events", "duplicate_reduction_percent",
    "average_sources", "maximum_sources",
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
        history_thresholds: HistoricalThresholds | None = None,
    ) -> None:
        self.database = database
        self.output_dir = output_dir
        self.clock = clock
        self.history_thresholds = history_thresholds

    def export(
        self,
        kinds: Iterable[str],
        from_date: date | None = None,
        to_date: date | None = None,
        history_period: str = "30d",
        watchlist_name: str | None = None,
    ) -> CSVExportResult:
        selected = tuple(dict.fromkeys(kinds))
        unsupported = set(selected) - {
            "signals",
            "outcomes",
            "performance",
            "history",
            "watchlists",
            "watchlist_signals",
            "sources",
            "content_items",
            "unified_events",
            "deduplication",
        }
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
        if "history" in selected:
            files.extend(self._export_history(timestamp, history_period))
        if "watchlists" in selected:
            files.extend(self._export_watchlists(timestamp))
        if "watchlist_signals" in selected:
            if not watchlist_name:
                raise ValueError("Watchlist name is required for watchlist signal export")
            files.append(
                self._export_watchlist_signals(
                    timestamp,
                    watchlist_name,
                    from_value,
                    to_value,
                )
            )
        if "sources" in selected:
            files.append(self._export_sources(timestamp))
        if "content_items" in selected:
            files.append(self._export_content_items(timestamp, from_value, to_value))
        if "unified_events" in selected:
            files.append(self._export_unified_events(timestamp))
        if "deduplication" in selected:
            files.append(self._export_deduplication(timestamp))
        return CSVExportResult(tuple(files))

    def _export_sources(self, timestamp: str) -> ExportedCSV:
        rows = [dict(row) for row in self.database.get_content_sources()]
        path = self._write("sources", timestamp, SOURCE_COLUMNS, rows)
        return ExportedCSV("sources", path, len(rows))

    def _export_content_items(
        self, timestamp: str, from_date: str | None, to_date: str | None,
    ) -> ExportedCSV:
        rows = [
            dict(row) for row in self.database.get_content_items(
                limit=None, from_date=from_date, to_date=to_date
            )
        ]
        path = self._write("content_items", timestamp, CONTENT_ITEM_COLUMNS, rows)
        return ExportedCSV("content_items", path, len(rows))

    def _export_unified_events(self, timestamp: str) -> ExportedCSV:
        rows = [dict(row) for row in self.database.get_unified_events(limit=None)]
        path = self._write("unified_events", timestamp, UNIFIED_EVENT_COLUMNS, rows)
        return ExportedCSV("unified_events", path, len(rows))

    def _export_deduplication(self, timestamp: str) -> ExportedCSV:
        stats = dict(self.database.get_deduplication_stats(30))
        raw = int(stats.get("raw_items") or 0)
        duplicates = int(stats.get("exact_duplicates") or 0) + int(
            stats.get("near_duplicates") or 0
        )
        row = {
            "period_days": 30,
            **stats,
            "duplicate_reduction_percent": duplicates / raw * 100 if raw else 0.0,
        }
        path = self._write(
            "deduplication_report", timestamp, DEDUPLICATION_COLUMNS, [row]
        )
        return ExportedCSV("deduplication", path, 1)

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
                "csv_export_marker": row["csv_export_marker"],
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

    def _export_history(
        self,
        timestamp: str,
        period: str,
    ) -> tuple[ExportedCSV, ...]:
        report = HistoricalAnalyticsService(
            self.database,
            self.history_thresholds,
            clock=lambda tz=None: self.clock().replace(tzinfo=tz),
        ).build_report(period)
        summary_row = {
            "period": report.period.key,
            "generated_at": report.generated_at,
            **report.summary.__dict__,
        }
        summary_path = self._write(
            "history_summary",
            timestamp,
            HISTORY_SUMMARY_COLUMNS,
            [summary_row],
        )
        timeline_rows = [bucket.__dict__ for bucket in report.timeline]
        timeline_path = self._write(
            "history_timeline",
            timestamp,
            HISTORY_TIMELINE_COLUMNS,
            timeline_rows,
        )

        def entity_rows(items) -> list[dict[str, object]]:
            return [
                {
                    **item.__dict__,
                    **item.growth.__dict__,
                }
                for item in items
            ]

        narrative_rows = entity_rows(report.narratives)
        token_rows = entity_rows(report.tokens)
        narrative_path = self._write(
            "narrative_history",
            timestamp,
            ENTITY_HISTORY_COLUMNS,
            narrative_rows,
        )
        token_path = self._write(
            "token_history",
            timestamp,
            ENTITY_HISTORY_COLUMNS,
            token_rows,
        )
        return (
            ExportedCSV("history_summary", summary_path, 1),
            ExportedCSV("history_timeline", timeline_path, len(timeline_rows)),
            ExportedCSV("narrative_history", narrative_path, len(narrative_rows)),
            ExportedCSV("token_history", token_path, len(token_rows)),
        )

    def _unique_path(self, stem: str, timestamp: str) -> Path:
        path = self.output_dir / f"{stem}_{timestamp}.csv"
        suffix = 2
        while path.exists():
            path = self.output_dir / f"{stem}_{timestamp}_{suffix}.csv"
            suffix += 1
        return path

    def _export_watchlists(self, timestamp: str) -> tuple[ExportedCSV, ...]:
        service = WatchlistService(self.database)
        watchlist_rows = []
        item_rows = []
        for watchlist in service.list_watchlists():
            report = service.report(watchlist.id)
            token_count = sum(item.item_type == "token" for item in report.items)
            narrative_count = sum(
                item.item_type == "narrative" for item in report.items
            )
            watchlist_rows.append(
                {
                    **watchlist.as_dict(),
                    "token_count": token_count,
                    "narrative_count": narrative_count,
                    "signal_count": report.signals_count,
                    "evaluated_count": report.evaluated_count,
                    "success_rate": report.success_rate,
                    "last_matched_at": report.last_matched_at,
                }
            )
            item_rows.extend(
                {
                    **item.as_dict(),
                    "watchlist_name": watchlist.name,
                }
                for item in report.items
            )
        watchlists_path = self._write(
            "watchlists",
            timestamp,
            WATCHLIST_COLUMNS,
            watchlist_rows,
        )
        items_path = self._write(
            "watchlist_items",
            timestamp,
            WATCHLIST_ITEM_COLUMNS,
            item_rows,
        )
        return (
            ExportedCSV("watchlists", watchlists_path, len(watchlist_rows)),
            ExportedCSV("watchlist_items", items_path, len(item_rows)),
        )

    def _export_watchlist_signals(
        self,
        timestamp: str,
        watchlist_name: str,
        from_date: str | None,
        to_date: str | None,
    ) -> ExportedCSV:
        service = WatchlistService(self.database)
        watchlist = service.get_watchlist(watchlist_name)
        if watchlist is None:
            raise ValueError(f"Watchlist '{watchlist_name}' does not exist")
        rows = []
        for row in self.database.get_watchlist_signals(watchlist.id, limit=None):
            signal_date = str(row["timestamp"] or "")[:10]
            if from_date and signal_date < from_date:
                continue
            if to_date and signal_date > to_date:
                continue
            rows.append(
                {
                    "watchlist_id": watchlist.id,
                    "watchlist_name": watchlist.name,
                    "signal_id": row["id"],
                    "signal_created_at": _iso_timestamp(row["timestamp"]),
                    "matched_at": _iso_timestamp(row["matched_at"]),
                    "matched_item_types": row["matched_item_types"],
                    "matched_item_values": row["matched_item_values"],
                    "signal_type": row["signal_type"],
                    "token": row["token"],
                    "narrative": row["narrative"],
                    "hype_score": row["hype_score"],
                    "momentum_score": row["momentum_score"],
                    "confidence": row["confidence"],
                    "mention_count": row["mentions_count"],
                    "action": row["action"],
                    "outcome_status": row["outcome_status"],
                }
            )
        path = self._write(
            "watchlist_signals",
            timestamp,
            WATCHLIST_SIGNAL_COLUMNS,
            rows,
        )
        return ExportedCSV("watchlist_signals", path, len(rows))


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
