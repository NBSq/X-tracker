from __future__ import annotations

import io
import json
import logging
import sqlite3
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from app import __version__
from app.config import load_config
from app.dashboard.app import create_app
from app.db.database import Database
from app.events import EventBus
from app.main import parse_args
from app.observability.context import correlation_id, correlation_scope
from app.observability.errors import classify_error
from app.observability.health import HealthService, SnapshotService, format_performance_report
from app.observability.logging import ContextFilter, JsonFormatter
from app.observability.metrics import ObservabilityMetrics
from app.observability.models import ComponentHealth, aggregate_state
from app.observability.timing import timed_operation


@pytest.fixture
def observability_db():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "observability.sqlite3"
        db = Database(path)
        db.initialize()
        config = replace(
            load_config(), database_path=path, ai_provider="mock",
            openai_api_key=None, telegram_bot_token=None, telegram_chat_id=None,
        )
        yield db, config
        db.close()


def test_json_logging_is_structured_and_redacts_secrets():
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.addFilter(ContextFilter())
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.observability.json")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info(
        "request used Bearer very-secret-value",
        extra={"event": "test_event", "duration_ms": 12.5,
               "telegram_bot_token": "123456:secret"},
    )

    payload = json.loads(stream.getvalue())
    assert payload["event"] == "test_event"
    assert payload["duration_ms"] == 12.5
    assert "very-secret-value" not in stream.getvalue()
    assert "123456:secret" not in stream.getvalue()


def test_correlation_ids_generate_propagate_and_reject_unsafe_values():
    with correlation_scope("safe-flow_123") as value:
        assert value == "safe-flow_123"
        assert correlation_id() == value
    with correlation_scope("unsafe header with spaces") as generated:
        assert generated != "unsafe header with spaces"
        assert len(generated) == 32


def test_required_prometheus_metrics_and_latency_are_exposed():
    runtime = ObservabilityMetrics(CollectorRegistry())
    runtime.increment("signals_created_total")
    runtime.observe("source_fetch", 25)
    output = runtime.prometheus().decode()
    assert "signals_created_total 1.0" in output
    assert 'operation_duration_seconds_count{operation="source_fetch"} 1.0' in output
    assert "event_bus_queue_depth -1.0" in output


def test_timing_helper_records_duration_and_slow_operation():
    with timed_operation("database_query", threshold_ms=0) as timer:
        time.sleep(0.001)
    assert timer.duration_ms > 0


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (ValueError("bad"), "validation"),
        (json.JSONDecodeError("bad", "x", 0), "parsing"),
        (sqlite3.OperationalError("bad"), "database"),
    ],
)
def test_error_classification(error, category):
    assert classify_error(error) == category


def test_component_state_aggregation():
    assert aggregate_state([ComponentHealth("db", "healthy", True)]) == "healthy"
    assert aggregate_state([ComponentHealth("ai", "degraded", False)]) == "degraded"
    assert aggregate_state([ComponentHealth("db", "unhealthy", True)]) == "unhealthy"


def test_health_readiness_components_and_disabled_openai(observability_db):
    db, config = observability_db
    health = HealthService(db, config, ObservabilityMetrics(CollectorRegistry()))
    ready, code = health.ready()
    detailed = health.detailed()
    assert code == 200 and ready["ready"] is True
    assert detailed["components"]["database"]["status"] == "healthy"
    assert detailed["components"]["openai"]["status"] == "disabled"
    assert detailed["components"]["event_bus"]["details"]["queue_depth"] is None
    assert detailed["components"]["telegram"]["status"] == "disabled"


def test_openai_missing_key_is_degraded_when_fallback_enabled(observability_db):
    db, config = observability_db
    config = replace(
        config, ai_provider="openai", openai_api_key=None,
        openai_fallback_to_mock=True,
    )
    assert HealthService(db, config).ai_health().status == "degraded"


def test_readiness_reports_closed_database_as_unhealthy(observability_db):
    db, config = observability_db
    db.close()
    payload, code = HealthService(db, config).ready()
    assert code == 503
    assert payload["ready"] is False


def test_event_bus_runtime_health_tracks_handlers(observability_db):
    db, config = observability_db
    runtime = ObservabilityMetrics(CollectorRegistry())
    with patch("app.events.bus.metrics", runtime), patch(
        "app.events.bus.record_domain_event",
        lambda event: runtime.record_event_published(type(event).__name__),
    ):
        bus = EventBus()
        bus.subscribe(str, lambda event: None)
        bus.publish("event")
    summary = runtime.event_bus_summary()
    assert summary["events_published"] == 1
    assert summary["handler_executions"] == 1
    assert summary["queue_depth_note"].startswith("not applicable")


def test_snapshot_persistence_and_retention(observability_db):
    db, config = observability_db
    service = SnapshotService(db, replace(config, observability_snapshot_retention_days=1))
    old = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    db.save_observability_snapshot(old, {"old": True})
    assert service.save_if_due(force=True) is True
    rows = db.get_observability_snapshots()
    assert len(rows) == 1
    assert json.loads(rows[0]["metrics_json"])["performance"]


def test_database_schema_migrates_observability_snapshots(observability_db):
    db, _ = observability_db
    assert db.has_table("observability_snapshots")


def test_http_health_metrics_and_correlation_endpoints(observability_db):
    db, config = observability_db
    db.close()
    client = TestClient(create_app(config.database_path, config=config))
    response = client.get("/live", headers={"X-Correlation-ID": "request-42"})
    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "request-42"
    assert response.json()["version"] == __version__
    assert client.get("/ready").status_code == 200
    assert client.get("/health").json()["components"]["database"]["status"] == "healthy"
    prometheus = client.get("/metrics")
    assert prometheus.status_code == 200
    assert "http_requests_total" in prometheus.text
    assert client.get("/api/system/version").json() == {"version": __version__}
    assert client.get("/api/system/health").status_code == 200
    assert client.get("/api/system/performance").status_code == 200
    assert client.get("/api/system/metrics-summary").status_code == 200


def test_dashboard_observability_pages_render(observability_db):
    db, config = observability_db
    db.close()
    client = TestClient(create_app(config.database_path, config=config))
    for path in ("/system/health", "/system/performance", "/system/metrics"):
        response = client.get(path)
        assert response.status_code == 200
        assert "System" in response.text


def test_cli_observability_arguments_and_version():
    with patch.object(sys, "argv", ["app", "--health"]):
        assert parse_args().health is True
    with patch.object(sys, "argv", ["app", "--component-health", "database"]):
        assert parse_args().component_health == "database"
    with patch.object(sys, "argv", ["app", "--metrics-summary"]):
        assert parse_args().metrics_summary is True


def test_performance_report_uses_measured_values_only():
    report = format_performance_report({
        "uptime_seconds": 65,
        "operations": {"source_fetch": {"average_ms": 12}},
        "errors": {},
        "slow_operations": {},
    })
    assert "Uptime: 0h 1m" in report
    assert "Source Fetch avg: 12.00 ms" in report
    assert "None recorded" in report
