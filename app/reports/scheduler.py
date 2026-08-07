from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from datetime import datetime, time as clock_time, timedelta, timezone
from html import escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.ai.report_summary import ReportSummaryService
from app.alerts.telegram import TelegramAlerter
from app.config import Config
from app.db.database import Database
from app.events import (
    EventBus, ScheduledReportCompleted, ScheduledReportDelivered,
    ScheduledReportFailed, ScheduledReportStarted,
)
from app.export.csv_exporter import CSVExportService
from app.observability.metrics import metrics
from app.reports.models import ScheduledReport, ScheduledReportValidationError
from app.search import SearchResult, SearchService


logger = logging.getLogger("x_narrative_tracker.reports")
WEEKDAYS = {name: index for index, name in enumerate(("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"))}


def calculate_next_run(
    schedule_type: str, schedule_value: str, timezone_name: str,
    after: datetime,
) -> datetime:
    if timezone_name.upper() == "UTC":
        zone = timezone.utc
    else:
        try:
            zone = ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError as exc:
            raise ScheduledReportValidationError("timezone must be a valid IANA timezone") from exc
    current = after.astimezone(timezone.utc).astimezone(zone)
    if schedule_type == "interval_hours":
        try:
            hours = int(schedule_value)
        except ValueError as exc:
            raise ScheduledReportValidationError("interval_hours schedule_value must be an integer") from exc
        if not 1 <= hours <= 168:
            raise ScheduledReportValidationError("interval_hours must be between 1 and 168")
        return (after.astimezone(timezone.utc) + timedelta(hours=hours)).replace(microsecond=0)
    if schedule_type == "daily":
        hour, minute = _parse_time(schedule_value)
        candidate = datetime.combine(current.date(), clock_time(hour, minute), zone)
        if candidate <= current:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)
    if schedule_type == "weekly":
        match = re.fullmatch(r"(MON|TUE|WED|THU|FRI|SAT|SUN)@(\d{2}:\d{2})", schedule_value.upper())
        if not match:
            raise ScheduledReportValidationError("weekly schedule_value must use MON@HH:MM")
        hour, minute = _parse_time(match.group(2))
        days = (WEEKDAYS[match.group(1)] - current.weekday()) % 7
        candidate = datetime.combine(current.date() + timedelta(days=days), clock_time(hour, minute), zone)
        if candidate <= current:
            candidate += timedelta(days=7)
        return candidate.astimezone(timezone.utc)
    raise ScheduledReportValidationError("schedule_type must be daily, weekly, or interval_hours")


class ReportScheduler:
    def __init__(
        self, db: Database, config: Config, event_bus: EventBus | None = None,
        *, clock=lambda: datetime.now(timezone.utc), telegram_factory=TelegramAlerter,
        ai_summary_service: ReportSummaryService | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.event_bus = event_bus
        self.clock = clock
        self.telegram_factory = telegram_factory
        self.ai_summary_service = ai_summary_service or ReportSummaryService(db, config)

    def list(self, enabled: bool | None = None) -> list[ScheduledReport]:
        return [ScheduledReport.from_row(row) for row in self.db.get_scheduled_reports(enabled)]

    def get(self, report_id: int) -> ScheduledReport | None:
        row = self.db.get_scheduled_report(report_id)
        return ScheduledReport.from_row(row) if row else None

    def create(self, payload: dict[str, Any]) -> ScheduledReport:
        values = self.validate_definition(payload)
        try:
            report_id = self.db.create_scheduled_report(values)
        except sqlite3.IntegrityError as exc:
            raise ScheduledReportValidationError("A scheduled report with this name already exists") from exc
        return self._required(report_id)

    def update(self, report_id: int, payload: dict[str, Any]) -> ScheduledReport:
        current = self._required(report_id)
        values = self.validate_definition({**current.as_dict(), **payload})
        try:
            if not self.db.update_scheduled_report(report_id, values):
                raise KeyError(report_id)
        except sqlite3.IntegrityError as exc:
            raise ScheduledReportValidationError("A scheduled report with this name already exists") from exc
        return self._required(report_id)

    def delete(self, report_id: int) -> None:
        if not self.db.delete_scheduled_report(report_id):
            raise KeyError(report_id)

    def runs(self, report_id: int, limit: int = 100) -> list[dict[str, Any]]:
        self._required(report_id)
        return [dict(row) for row in self.db.get_scheduled_report_runs(report_id, min(max(limit, 1), 500))]

    def preview(self, report_id: int) -> dict[str, Any]:
        report = self._required(report_id)
        result = SearchService(self.db, self.config, self.event_bus).preview(
            report.saved_search_id, report.max_results,
        )
        summary_text = deterministic_summary(result)
        return {
            "matching_count": result.total_matches,
            "top_results": list(result.results),
            "message_preview": format_report_message(report, result, summary_text),
            "estimated_csv_row_count": len(result.results) if report.include_csv else 0,
            "summary": result.summary,
        }

    def run_due(self) -> list[dict[str, Any]]:
        now = self.clock().astimezone(timezone.utc)
        due = self.db.get_due_scheduled_reports(now.isoformat())
        metrics.set_gauge("scheduled_reports_due", len(due))
        metrics.set_gauge("scheduled_reports_enabled", self.db.count_enabled_scheduled_reports())
        results = []
        for row in due:
            outcome = self.run(int(row["id"]), force=False)
            if outcome is not None:
                results.append(outcome)
        self.cleanup_csv_retention(now)
        return results

    def run(self, report_id: int, *, force: bool = True) -> dict[str, Any] | None:
        report = self._required(report_id)
        if not report.enabled:
            raise ScheduledReportValidationError("Scheduled report is disabled")
        started = self.clock().astimezone(timezone.utc)
        started_perf = time.perf_counter()
        run_id = self.db.claim_scheduled_report(report_id, started.isoformat(), force=force)
        if run_id is None:
            return None
        self._publish(ScheduledReportStarted(report_id, run_id))
        logger.info(
            "Scheduled report started report_id=%s run_id=%s",
            report_id, run_id,
            extra={"event": "scheduled_report_started", "component": "report_scheduler"},
        )
        status = "success"
        delivery_status = "not_requested"
        csv_path: str | None = None
        error_type: str | None = None
        result_count = 0
        try:
            result = SearchService(self.db, self.config, self.event_bus).run(
                report.saved_search_id, report.max_results,
            )
            result_count = len(result.results)
            local_summary = deterministic_summary(result)
            summary_text = self.ai_summary_service.summarize(
                {"search": result.search.name, "summary": result.summary,
                 "top_results": list(result.results)[:10]}, local_summary,
            )
            if report.include_csv:
                csv_path = str(self._write_csv(report, result))
            if report.delivery_type == "telegram":
                self._deliver_telegram(report, format_report_message(report, result, summary_text))
                delivery_status = "sent"
                self._publish(ScheduledReportDelivered(report_id, run_id, "telegram"))
            self._publish(ScheduledReportCompleted(
                report_id, run_id, result_count,
                int((time.perf_counter() - started_perf) * 1000),
            ))
            metrics.increment("scheduled_report_runs_total")
            metrics.record_success("report_scheduler")
        except Exception as exc:
            status = "failed"
            error_type = type(exc).__name__
            delivery_status = "failed" if report.delivery_type == "telegram" else delivery_status
            metrics.increment("scheduled_report_failures_total")
            if delivery_status == "failed":
                metrics.increment("scheduled_report_delivery_failures_total")
            metrics.record_error("report_scheduler", error_type)
            self._publish(ScheduledReportFailed(report_id, run_id, error_type))
            logger.exception(
                "Scheduled report failed report_id=%s run_id=%s error_type=%s",
                report_id, run_id, error_type,
                extra={"event": "scheduled_report_failed", "component": "report_scheduler"},
            )
        completed = self.clock().astimezone(timezone.utc)
        duration_ms = max(0, int((completed - started).total_seconds() * 1000))
        next_run = calculate_next_run(
            report.schedule_type, report.schedule_value, report.timezone, started,
        )
        self.db.complete_scheduled_report_run(
            run_id, completed_at=completed.isoformat(), status=status,
            result_count=result_count, delivery_status=delivery_status,
            csv_path=csv_path, error_type=error_type, duration_ms=duration_ms,
            next_run_at=next_run.isoformat(),
        )
        metrics.observe("scheduled_report", duration_ms)
        metrics.scheduled_report_duration.observe(duration_ms / 1000.0)
        logger.info(
            "Scheduled report completed report_id=%s run_id=%s status=%s result_count=%s delivery_status=%s duration_ms=%s",
            report_id, run_id, status, result_count, delivery_status, duration_ms,
            extra={"event": "scheduled_report_completed", "component": "report_scheduler"},
        )
        return {
            "run_id": run_id, "status": status, "result_count": result_count,
            "delivery_status": delivery_status, "csv_path": csv_path,
            "error_type": error_type, "next_run_at": next_run.isoformat(),
        }

    def validate_definition(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = " ".join(str(payload.get("name", "")).split())
        if not name or len(name) > 120:
            raise ScheduledReportValidationError("name must contain 1 to 120 characters")
        try:
            search_id = int(payload.get("saved_search_id"))
        except (TypeError, ValueError) as exc:
            raise ScheduledReportValidationError("saved_search_id must be an integer") from exc
        if self.db.get_saved_search(search_id) is None:
            raise ScheduledReportValidationError("Saved search does not exist")
        schedule_type = str(payload.get("schedule_type", "daily")).strip().lower()
        schedule_value = str(payload.get("schedule_value", "09:00")).strip().upper()
        timezone_name = str(payload.get("timezone") or self.config.report_default_timezone).strip()
        now = self.clock().astimezone(timezone.utc)
        next_run = calculate_next_run(schedule_type, schedule_value, timezone_name, now)
        delivery = str(payload.get("delivery_type", "telegram")).strip().lower()
        if delivery not in {"telegram", "none"}:
            raise ScheduledReportValidationError("delivery_type must be telegram or none")
        try:
            maximum = int(payload.get("max_results", self.config.report_max_results))
        except (TypeError, ValueError) as exc:
            raise ScheduledReportValidationError("max_results must be an integer") from exc
        if not 1 <= maximum <= self.config.report_max_results:
            raise ScheduledReportValidationError(
                f"max_results must be between 1 and {self.config.report_max_results}"
            )
        return {
            "name": name, "saved_search_id": search_id,
            "enabled": bool(payload.get("enabled", True)), "schedule_type": schedule_type,
            "schedule_value": schedule_value, "timezone": timezone_name,
            "delivery_type": delivery,
            "destination": str(payload.get("destination") or "").strip() or None,
            "include_summary": bool(payload.get("include_summary", True)),
            "include_top_results": bool(payload.get("include_top_results", True)),
            "include_csv": bool(payload.get("include_csv", False)),
            "max_results": maximum, "next_run_at": next_run.isoformat(),
        }

    def cleanup_csv_retention(self, now: datetime | None = None) -> int:
        root = self.config.report_output_dir.resolve()
        if not root.exists():
            return 0
        cutoff = (now or self.clock()).timestamp() - self.config.report_csv_retention_days * 86400
        removed = 0
        for path in root.glob("*.csv"):
            resolved = path.resolve()
            if resolved.parent != root or path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            removed += 1
        return removed

    def health(self) -> dict[str, Any]:
        now = self.clock().astimezone(timezone.utc).isoformat()
        return {
            "status": "disabled" if not self.config.report_scheduler_enabled else (
                "degraded" if metrics.performance_summary()["error_counts"].get("report_scheduler") else "healthy"
            ),
            "enabled": self.config.report_scheduler_enabled,
            "enabled_reports": self.db.count_enabled_scheduled_reports(),
            "due_reports": self.db.count_due_scheduled_reports(now),
            "poll_seconds": self.config.report_scheduler_poll_seconds,
            "last_successful_run": metrics.performance_summary()["last_success"].get("report_scheduler"),
            "recent_failure_count": metrics.performance_summary()["error_counts"].get("report_scheduler", 0),
        }

    def _write_csv(self, report: ScheduledReport, result: SearchResult) -> Path:
        columns = list(result.results[0].keys()) if result.results else ["id"]
        exported = CSVExportService(
            self.db, self.config.report_output_dir,
            clock=lambda: self.clock().replace(tzinfo=None),
        ).export_rows(f"scheduled_report_{report.name}", columns, list(result.results))
        return exported.path

    def _deliver_telegram(self, report: ScheduledReport, text: str) -> None:
        token = self.config.telegram_bot_token
        destination = report.destination or self.config.telegram_chat_id
        if not token or not destination:
            raise ScheduledReportValidationError("Telegram not configured")
        self.telegram_factory(
            token, destination, slow_threshold_ms=self.config.slow_telegram_send_ms,
        ).send_saved_search_report(text)

    def _required(self, report_id: int) -> ScheduledReport:
        report = self.get(report_id)
        if report is None:
            raise KeyError(report_id)
        return report

    def _publish(self, event: object) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(event)


class SchedulerLoop:
    def __init__(self, config: Config, event_bus: EventBus | None = None) -> None:
        self.config = config
        self.event_bus = event_bus
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.config.report_scheduler_enabled or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._run, name="report-scheduler", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(min(30, self.config.tracker_shutdown_timeout_seconds))

    def _run(self) -> None:
        while not self.stop_event.is_set():
            db = Database(self.config.database_path)
            try:
                db.initialize()
                ReportScheduler(db, self.config, self.event_bus).run_due()
            except Exception:
                logger.exception("Report scheduler poll failed")
                metrics.increment("scheduled_report_failures_total")
                metrics.record_error("report_scheduler", "poll_failed")
            finally:
                db.close()
            self.stop_event.wait(self.config.report_scheduler_poll_seconds)


def deterministic_summary(result: SearchResult) -> str:
    summary = result.summary
    parts = [f"{result.total_matches} matching result(s)."]
    for label, key in (("Average quality", "average_quality"), ("average hype", "average_hype"),
                       ("average momentum", "average_momentum")):
        if summary.get(key) is not None:
            parts.append(f"{label}: {summary[key]:.1f}.")
    if summary.get("success_rate") is not None:
        parts.append(f"Evaluated success rate: {summary['success_rate']:.1f}%.")
    return " ".join(parts)


def format_report_message(
    report: ScheduledReport, result: SearchResult, summary_text: str,
) -> str:
    period = "Daily" if report.schedule_type == "daily" else (
        "Weekly" if report.schedule_type == "weekly" else "Scheduled"
    )
    lines = [
        f"<b>📊 {period} Saved Search Report</b>", "",
        f"<b>Search:</b> {escape(result.search.name)}",
        f"<b>Matches:</b> {result.total_matches}",
    ]
    if report.include_top_results:
        lines.extend(["", "<b>Top results:</b>"])
        for index, item in enumerate(result.results[: min(report.max_results, 10)], start=1):
            name = item.get("token") or item.get("narrative") or item.get("name") or item.get("title")
            if not name:
                name = f"Result {item.get('id', index)}"
            scores = []
            for label, key in (("Quality", "quality_score"), ("Hype", "hype_score"),
                               ("Momentum", "momentum_score")):
                if item.get(key) is not None:
                    scores.append(f"{label} {float(item[key]):.0f}")
            suffix = f" — {' — '.join(scores)}" if scores else ""
            lines.append(f"{index}. {escape(str(name))}{escape(suffix)}")
    if report.include_summary:
        lines.extend(["", "<b>Summary:</b>", escape(summary_text)])
    message = "\n".join(lines)
    if len(message) > 4000:
        message = message[:3970].rsplit("\n", 1)[0] + "\n…"
    return message


def _parse_time(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{2}):(\d{2})", value)
    if not match:
        raise ScheduledReportValidationError("time must use HH:MM")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ScheduledReportValidationError("time must use a valid 24-hour value")
    return hour, minute
