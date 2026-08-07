from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app import __version__
from app.config import Config
from app.db.database import Database
from app.observability.errors import classify_error
from app.observability.logging import log_event
from app.observability.metrics import ObservabilityMetrics, metrics
from app.observability.models import ComponentHealth, aggregate_state


logger = logging.getLogger("x_narrative_tracker.observability")
REQUIRED_TABLES = frozenset({
    "analyzed_posts", "signal_history", "signal_outcomes", "content_sources",
    "unified_events", "alert_rules", "watchlists", "signal_quality_scores",
    "observability_snapshots",
    "saved_searches", "scheduled_reports", "scheduled_report_runs",
})


class HealthService:
    def __init__(
        self, db: Database, config: Config,
        runtime_metrics: ObservabilityMetrics = metrics,
    ) -> None:
        self.db = db
        self.config = config
        self.metrics = runtime_metrics

    def live(self) -> dict[str, Any]:
        return {
            "status": "healthy", "version": __version__,
            "uptime_seconds": round(time.time() - self.metrics.started_at, 2),
        }

    def ready(self) -> tuple[dict[str, Any], int]:
        database = self.database_health()
        configuration = self.configuration_health()
        ready = all(item.status == "healthy" for item in (database, configuration))
        return {
            "status": "healthy" if ready else "unhealthy",
            "ready": ready,
            "checks": {
                "database": database.as_dict(),
                "configuration": configuration.as_dict(),
            },
        }, 200 if ready else 503

    def detailed(self) -> dict[str, Any]:
        components = [
            self.configuration_health(), self.database_health(), self.source_health(),
            self.telegram_health(), self.ai_health(), self.event_bus_health(),
            self.report_scheduler_health(), self.process_health(),
        ]
        self.update_gauges()
        return {
            "status": aggregate_state(components),
            "version": __version__,
            "uptime_seconds": round(time.time() - self.metrics.started_at, 2),
            "components": {item.name: item.as_dict() for item in components},
        }

    def configuration_health(self) -> ComponentHealth:
        valid = self.config.database_path is not None and self.config.log_format in {"text", "json"}
        return ComponentHealth(
            "configuration", "healthy" if valid else "unhealthy", True,
            "configuration loaded" if valid else "configuration is invalid",
            {"ai_provider": self.config.ai_provider},
        )

    def database_health(self) -> ComponentHealth:
        started = time.perf_counter()
        try:
            self.db.connection.execute("SELECT 1").fetchone()
            tables = {
                str(row["name"]) for row in self.db.connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            missing = sorted(REQUIRED_TABLES - tables)
            latency = round((time.perf_counter() - started) * 1000, 2)
            self.metrics.observe(
                "database_query", latency,
                slow=latency > self.config.slow_database_query_ms,
            )
            self.metrics.record_success("database")
            status = "healthy" if not missing else "unhealthy"
            return ComponentHealth(
                "database", status, True,
                "database accessible" if not missing else "database migration incomplete",
                {
                    "query_latency_ms": latency,
                    "file_size_bytes": (
                        self.db.path.stat().st_size if self.db.path.exists() else 0
                    ),
                    "migration_status": "current" if not missing else "incomplete",
                    "missing_tables": missing,
                    "last_successful_operation": self.metrics.performance_summary()["last_success"].get("database"),
                    "recent_error_count": self.metrics.performance_summary()["error_counts"].get("database", 0),
                },
            )
        except sqlite3.Error as exc:
            error_type = classify_error(exc)
            self.metrics.increment("database_errors_total")
            self.metrics.record_error("database", error_type)
            return ComponentHealth(
                "database", "unhealthy", True, "database inaccessible",
                {"error_type": error_type},
            )

    def source_health(self) -> ComponentHealth:
        rows = self.db.get_content_sources(enabled=True)
        now = datetime.now(timezone.utc)
        failing = [row for row in rows if int(row["consecutive_failures"] or 0) > 0]
        stale = []
        for row in rows:
            timestamp = row["last_success_at"] or row["last_fetch_at"]
            if not timestamp:
                continue
            try:
                seen = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                if seen.tzinfo is None:
                    seen = seen.replace(tzinfo=timezone.utc)
            except ValueError:
                stale.append(row)
                continue
            interval = int(row["fetch_interval_seconds"] or self.config.source_default_interval_seconds)
            if now > seen + timedelta(seconds=max(1, interval) * 2):
                stale.append(row)
        last_success = max(
            (str(row["last_success_at"]) for row in rows if row["last_success_at"]),
            default=None,
        )
        status = "disabled" if not self.config.source_enabled else (
            "degraded" if failing or stale else "healthy"
        )
        unhealthy_ids = {
            int(row["id"]) for row in [*failing, *stale]
        }
        return ComponentHealth(
            "sources", status, False,
            "source ingestion disabled" if status == "disabled" else "source health summarized",
            {
                "enabled_sources": len(rows),
                "healthy_sources": max(0, len(rows) - len(unhealthy_ids)),
                "failing_sources": len(failing), "last_successful_fetch": last_success,
                "maximum_consecutive_failures": max(
                    (int(row["consecutive_failures"] or 0) for row in rows), default=0
                ),
                "stale_source_count": len(stale),
            },
        )

    def ai_health(self) -> ComponentHealth:
        usage = self.db.get_ai_usage(limit=200)
        today = self.db.count_openai_requests_today()
        relevant = [row for row in usage if str(row["provider"]) == "openai"]
        successful = sum(bool(row["success"]) for row in relevant)
        fallbacks = sum(bool(row["fallback_used"]) for row in relevant)
        cache_hits = sum(bool(row["cached"]) for row in relevant)
        last_success = next((str(row["requested_at"]) for row in relevant if row["success"]), None)
        last_failure = next((str(row["error_type"]) for row in relevant if not row["success"]), None)
        if self.config.ai_provider == "mock":
            status = "disabled"
            message = "OpenAI disabled; deterministic mock AI active"
        elif not self.config.openai_api_key:
            status = "degraded" if self.config.openai_fallback_to_mock else "unhealthy"
            message = "OpenAI is not configured"
        else:
            success_rate = successful / len(relevant) * 100 if relevant else 100.0
            status = "healthy" if success_rate >= 80 else "degraded"
            message = "AI provider operational" if status == "healthy" else "recent AI failures detected"
        return ComponentHealth(
            "openai", status, False, message,
            {
                "provider_mode": self.config.ai_provider,
                "configured": bool(self.config.openai_api_key),
                "requests_today": today, "local_limit": self.config.openai_daily_request_limit,
                "recent_success_rate": round(successful / len(relevant) * 100, 1) if relevant else None,
                "fallback_count": fallbacks,
                "cache_hit_rate": round(cache_hits / len(relevant) * 100, 1) if relevant else None,
                "last_successful_request": last_success, "last_failure_type": last_failure,
            },
        )

    def telegram_health(self) -> ComponentHealth:
        configured = bool(self.config.telegram_bot_token and self.config.telegram_chat_id)
        performance = self.metrics.performance_summary()
        telegram_error_count = performance["error_counts"].get("telegram", 0)
        operation = performance["operations"].get("telegram_send", {})
        if not configured:
            status = "disabled"
        else:
            status = "degraded" if telegram_error_count else "healthy"
        return ComponentHealth(
            "telegram", status, False,
            "Telegram configured" if configured else "Telegram not configured",
            {
                "configured": configured,
                "last_successful_send": performance["last_success"].get("telegram"),
                "recent_failure_count": telegram_error_count,
                "average_latency_ms": operation.get("average_ms"),
                "cooldown_state": "not active",
            },
        )

    def event_bus_health(self) -> ComponentHealth:
        summary = self.metrics.event_bus_summary()
        status = "degraded" if summary["failures"] else "healthy"
        return ComponentHealth(
            "event_bus", status, True, "synchronous Event Bus",
            summary,
        )

    def report_scheduler_health(self) -> ComponentHealth:
        performance = self.metrics.performance_summary()
        failures = performance["error_counts"].get("report_scheduler", 0)
        enabled_count = self.db.count_enabled_scheduled_reports()
        due_count = self.db.count_due_scheduled_reports(
            datetime.now(timezone.utc).isoformat()
        )
        status = "disabled" if not self.config.report_scheduler_enabled else (
            "degraded" if failures else "healthy"
        )
        return ComponentHealth(
            "report_scheduler", status, False,
            "report scheduler disabled" if status == "disabled" else "report scheduler operational",
            {
                "enabled_reports": enabled_count, "due_reports": due_count,
                "poll_seconds": self.config.report_scheduler_poll_seconds,
                "last_successful_run": performance["last_success"].get("report_scheduler"),
                "recent_failure_count": failures,
            },
        )

    def process_health(self) -> ComponentHealth:
        return ComponentHealth(
            "process", "healthy", True, "process running",
            self.metrics.update_process_metrics(self.db.path),
        )

    def update_gauges(self) -> None:
        sources = self.db.get_content_sources(enabled=True)
        self.metrics.set_gauge("active_sources", len(sources))
        self.metrics.set_gauge(
            "failing_sources", sum(int(row["consecutive_failures"] or 0) > 0 for row in sources)
        )
        self.metrics.set_gauge("active_rules", len(self.db.get_alert_rules(enabled=True)))
        self.metrics.set_gauge("active_watchlists", len(self.db.get_watchlists(enabled=True)))
        self.metrics.set_gauge("ai_requests_today", self.db.count_openai_requests_today())
        self.metrics.set_gauge("ai_cache_entries", self.db.get_active_ai_cache_count())
        self.metrics.set_gauge(
            "open_quality_recommendations", len(self.db.get_quality_recommendations("open"))
        )
        self.metrics.set_gauge(
            "scheduled_reports_enabled", self.db.count_enabled_scheduled_reports()
        )
        self.metrics.set_gauge(
            "scheduled_reports_due",
            self.db.count_due_scheduled_reports(datetime.now(timezone.utc).isoformat()),
        )
        events = self.db.get_unified_events(limit=None)
        self.metrics.set_gauge(
            "open_unified_events", sum(row["status"] == "active" for row in events)
        )
        pending = self.db.connection.execute(
            """SELECT COUNT(*) FROM signal_history AS signal
            WHERE NOT EXISTS (
                SELECT 1 FROM signal_outcomes AS outcome WHERE outcome.signal_id = signal.id
            )"""
        ).fetchone()[0]
        self.metrics.set_gauge("pending_signal_outcomes", int(pending))

    def metrics_summary(self) -> dict[str, Any]:
        self.update_gauges()
        process = self.metrics.update_process_metrics(self.db.path)
        return {
            "version": __version__,
            "metrics": self.metrics.metric_values(),
            "runtime": self.metrics.performance_summary(),
            "process": process,
            "database": self.database_health().details,
            "sources": self.source_health().details,
            "ai": self.ai_health().details,
            "telegram": self.telegram_health().details,
        }

    def performance_report(self) -> dict[str, Any]:
        report = self.metrics.performance_summary()
        values = self.metrics.metric_values()
        report["throughput"] = values["counters"]
        report["recent_trends"] = SnapshotService(self.db, self.config).history(48)
        report["errors"] = {
            "source_failures": report["error_counts"].get("source_fetch", 0),
            "ai_failures": report["error_counts"].get("ai_analysis", 0),
            "telegram_failures": report["error_counts"].get("telegram", 0),
            "database_errors": report["error_counts"].get("database", 0),
        }
        return report


class SnapshotService:
    def __init__(
        self, db: Database, config: Config,
        runtime_metrics: ObservabilityMetrics = metrics,
    ) -> None:
        self.db = db
        self.config = config
        self.metrics = runtime_metrics

    def save_if_due(self, *, force: bool = False) -> bool:
        if not self.config.observability_snapshot_enabled:
            return False
        rows = self.db.get_observability_snapshots(limit=1)
        now = datetime.now(timezone.utc)
        if rows and not force:
            previous = datetime.fromisoformat(str(rows[0]["timestamp"]).replace("Z", "+00:00"))
            if previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
            if now < previous + timedelta(
                minutes=self.config.observability_snapshot_interval_minutes
            ):
                return False
        interval_seconds = self.config.observability_snapshot_interval_minutes * 60
        bucket_epoch = int(now.timestamp()) // interval_seconds * interval_seconds
        timestamp = datetime.fromtimestamp(bucket_epoch, timezone.utc).isoformat()
        self.db.save_observability_snapshot(
            timestamp, self.metrics.snapshot(self.db.path)
        )
        self.db.cleanup_observability_snapshots(
            self.config.observability_snapshot_retention_days
        )
        return True

    def history(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.db.get_observability_snapshots(limit)
        result = []
        for row in reversed(rows):
            try:
                payload = json.loads(str(row["metrics_json"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                payload = {}
            result.append({"timestamp": row["timestamp"], "metrics": payload})
        return result


def format_performance_report(report: dict[str, Any]) -> str:
    uptime = float(report.get("uptime_seconds", 0))
    hours, remainder = divmod(int(uptime), 3600)
    minutes = remainder // 60
    lines = ["Performance Report", "", f"Uptime: {hours}h {minutes}m", "", "Pipeline:"]
    for operation in (
        "source_fetch", "deduplication", "signal_creation", "ai_analysis",
        "rule_evaluation", "telegram_send", "database_query",
    ):
        values = report.get("operations", {}).get(operation, {})
        lines.append(f"{operation.replace('_', ' ').title()} avg: {float(values.get('average_ms', 0)):.2f} ms")
    lines.extend(("", "Errors:"))
    for name, count in report.get("errors", {}).items():
        lines.append(f"{name.replace('_', ' ').title()}: {count}")
    lines.extend(("", "Slow operations:"))
    for name, count in report.get("slow_operations", {}).items():
        lines.append(f"{name.replace('_', ' ').title()}: {count}")
    if not report.get("slow_operations"):
        lines.append("None recorded")
    return "\n".join(lines)
