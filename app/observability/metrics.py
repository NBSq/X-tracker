from __future__ import annotations

import os
import platform
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest

from app import __version__


LATENCY_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 60)
OPERATIONS = frozenset({
    "source_fetch", "normalization", "deduplication", "unified_event_processing",
    "signal_creation", "ai_analysis", "rule_evaluation", "watchlist_matching",
    "graph_update", "quality_calculation", "telegram_send", "database_query",
    "http_request", "event_handler",
    "saved_search", "scheduled_report",
})


class ObservabilityMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self.started_at = time.time()
        self._lock = threading.RLock()
        self._durations: dict[str, list[float]] = defaultdict(list)
        self._slow_counts: dict[str, int] = defaultdict(int)
        self._last_success: dict[str, str] = {}
        self._last_error: dict[str, dict[str, str]] = {}
        self._error_counts: dict[str, int] = defaultdict(int)
        self._event_bus = {
            "events_published": 0, "handler_executions": 0, "failures": 0,
            "handler_duration_total_ms": 0.0, "slow_handlers": 0,
            "last_event_at": None,
        }
        counter_names = (
            "content_items_fetched_total", "content_items_accepted_total",
            "content_items_deduplicated_total", "unified_events_created_total",
            "unified_events_updated_total", "signals_created_total",
            "signals_evaluated_total", "telegram_notifications_sent_total",
            "telegram_notifications_failed_total", "ai_requests_total",
            "ai_failures_total", "ai_fallbacks_total", "ai_cache_hits_total",
            "rule_evaluations_total", "rule_matches_total", "watchlist_matches_total",
            "graph_updates_total", "quality_scores_calculated_total",
            "event_bus_events_published_total", "event_bus_handler_failures_total",
            "database_errors_total", "http_requests_total",
            "saved_search_runs_total", "saved_search_failures_total",
            "scheduled_report_runs_total", "scheduled_report_failures_total",
            "scheduled_report_delivery_failures_total",
        )
        self.counters = {
            name: Counter(name, name.replace("_", " "), registry=self.registry)
            for name in counter_names if name != "http_requests_total"
        }
        self.http_requests = Counter(
            "http_requests_total", "HTTP requests", ("method", "route", "status"),
            registry=self.registry,
        )
        gauge_names = (
            "active_sources", "failing_sources", "open_unified_events",
            "pending_signal_outcomes", "active_watchlists", "active_rules",
            "ai_requests_today", "ai_cache_entries", "open_quality_recommendations",
            "process_memory_bytes", "process_cpu_percent", "process_uptime_seconds",
            "database_size_bytes", "process_threads", "process_open_files",
            "scheduled_reports_due", "scheduled_reports_enabled",
        )
        self.gauges = {
            name: Gauge(name, name.replace("_", " "), registry=self.registry)
            for name in gauge_names
        }
        self.event_bus_queue_depth = Gauge(
            "event_bus_queue_depth", "Synchronous bus queue depth; -1 means not applicable",
            registry=self.registry,
        )
        self.event_bus_queue_depth.set(-1)
        self.operation_latency = Histogram(
            "operation_duration_seconds", "Operation duration", ("operation",),
            buckets=LATENCY_BUCKETS, registry=self.registry,
        )
        self.slow_operations = Counter(
            "slow_operations_total", "Operations exceeding configured threshold",
            ("operation",), registry=self.registry,
        )
        self.http_latency = Histogram(
            "http_request_duration_seconds", "HTTP request duration",
            ("method", "route"), buckets=LATENCY_BUCKETS, registry=self.registry,
        )
        self.scheduled_report_duration = Histogram(
            "scheduled_report_duration_seconds", "Scheduled report duration",
            buckets=LATENCY_BUCKETS, registry=self.registry,
        )
        self.application_info = Gauge(
            "application_info", "Application build information",
            ("version", "python_version"), registry=self.registry,
        )
        self.application_info.labels(__version__, platform.python_version()).set(1)

    def increment(self, name: str, amount: float = 1.0) -> None:
        counter = self.counters.get(name)
        if counter is not None:
            counter.inc(max(0.0, amount))

    def set_gauge(self, name: str, value: float) -> None:
        gauge = self.gauges.get(name)
        if gauge is not None:
            gauge.set(value)

    def observe(self, operation: str, duration_ms: float, *, slow: bool = False) -> None:
        normalized = operation if operation in OPERATIONS else "database_query"
        seconds = max(0.0, duration_ms) / 1000.0
        self.operation_latency.labels(normalized).observe(seconds)
        with self._lock:
            values = self._durations[normalized]
            values.append(float(duration_ms))
            if len(values) > 2000:
                del values[:-1000]
            if slow:
                self._slow_counts[normalized] += 1
                self.slow_operations.labels(normalized).inc()

    def record_success(self, component: str) -> None:
        self._last_success[component] = datetime.now(timezone.utc).isoformat()

    def record_error(self, component: str, error_type: str) -> None:
        self._error_counts[component] += 1
        self._last_error[component] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "error_type": error_type,
        }

    def record_event_published(self, event_name: str) -> None:
        self.increment("event_bus_events_published_total")
        with self._lock:
            self._event_bus["events_published"] += 1
            self._event_bus["last_event_at"] = datetime.now(timezone.utc).isoformat()

    def record_handler(self, duration_ms: float, *, failed: bool, slow: bool) -> None:
        with self._lock:
            self._event_bus["handler_executions"] += 1
            self._event_bus["handler_duration_total_ms"] += duration_ms
            if failed:
                self._event_bus["failures"] += 1
            if slow:
                self._event_bus["slow_handlers"] += 1
        if failed:
            self.increment("event_bus_handler_failures_total")
        self.observe("event_handler", duration_ms, slow=slow)

    def event_bus_summary(self) -> dict[str, Any]:
        with self._lock:
            result = dict(self._event_bus)
        executions = int(result["handler_executions"])
        duration_total = float(result.pop("handler_duration_total_ms"))
        result["average_handler_latency_ms"] = round(
            duration_total / executions, 2
        ) if executions else 0.0
        result["queue_depth"] = None
        result["queue_depth_note"] = "not applicable: synchronous Event Bus"
        return result

    def performance_summary(self) -> dict[str, Any]:
        with self._lock:
            operations = {
                name: {
                    "count": len(values),
                    "average_ms": round(sum(values) / len(values), 2) if values else 0.0,
                    "maximum_ms": round(max(values), 2) if values else 0.0,
                    "slow_count": self._slow_counts.get(name, 0),
                }
                for name, values in sorted(self._durations.items())
            }
        return {
            "uptime_seconds": round(time.time() - self.started_at, 2),
            "operations": operations,
            "slow_operations": dict(self._slow_counts),
            "last_success": dict(self._last_success),
            "last_errors": dict(self._last_error),
            "error_counts": dict(self._error_counts),
            "event_bus": self.event_bus_summary(),
        }

    def update_process_metrics(self, database_path: Path | None = None) -> dict[str, Any]:
        details: dict[str, Any] = {
            "uptime_seconds": round(time.time() - self.started_at, 2),
            "python_version": platform.python_version(), "application_version": __version__,
            "thread_count": threading.active_count(), "open_file_count": None,
        }
        try:
            import psutil

            process = psutil.Process(os.getpid())
            details.update({
                "memory_rss_bytes": process.memory_info().rss,
                "cpu_percent": process.cpu_percent(interval=None),
                "thread_count": process.num_threads(),
                "open_file_count": len(process.open_files()),
            })
        except (ImportError, OSError, PermissionError):
            details.update({"memory_rss_bytes": 0, "cpu_percent": 0.0})
        details["database_size_bytes"] = (
            database_path.stat().st_size if database_path and database_path.exists() else 0
        )
        for metric, key in (
            ("process_memory_bytes", "memory_rss_bytes"),
            ("process_cpu_percent", "cpu_percent"),
            ("process_uptime_seconds", "uptime_seconds"),
            ("process_threads", "thread_count"),
            ("database_size_bytes", "database_size_bytes"),
        ):
            self.set_gauge(metric, float(details[key] or 0))
        self.set_gauge("process_open_files", float(details["open_file_count"] or 0))
        return details

    def prometheus(self) -> bytes:
        return generate_latest(self.registry)

    def metric_values(self) -> dict[str, dict[str, float]]:
        counters = {
            name: float(counter._value.get())
            for name, counter in self.counters.items()
        }
        gauges = {
            name: float(gauge._value.get())
            for name, gauge in self.gauges.items()
        }
        gauges["event_bus_queue_depth"] = float(self.event_bus_queue_depth._value.get())
        return {"counters": counters, "gauges": gauges}

    def snapshot(self, database_path: Path | None = None) -> dict[str, Any]:
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "performance": self.performance_summary(),
            "process": self.update_process_metrics(database_path),
            "metrics": self.metric_values(),
        }


metrics = ObservabilityMetrics()


EVENT_COUNTERS: dict[str, tuple[str, str | None]] = {
    "ContentFetched": ("content_items_fetched_total", "item_count"),
    "ContentAccepted": ("content_items_accepted_total", None),
    "ContentDeduplicated": ("content_items_deduplicated_total", None),
    "UnifiedEventCreated": ("unified_events_created_total", None),
    "UnifiedEventUpdated": ("unified_events_updated_total", None),
    "SignalCreated": ("signals_created_total", None),
    "SignalEvaluated": ("signals_evaluated_total", None),
    "RuleTriggered": ("rule_matches_total", None),
    "WatchlistMatched": ("watchlist_matches_total", "watchlist_ids"),
    "GraphUpdated": ("graph_updates_total", None),
    "SignalQualityCalculated": ("quality_scores_calculated_total", None),
}


def record_domain_event(event: object) -> None:
    name = type(event).__name__
    metrics.record_event_published(name)
    mapping = EVENT_COUNTERS.get(name)
    if mapping is None:
        return
    counter, amount_field = mapping
    amount = 1
    if amount_field:
        value = getattr(event, amount_field, 1)
        amount = len(value) if isinstance(value, (tuple, list, set)) else int(value or 0)
    metrics.increment(counter, amount)
