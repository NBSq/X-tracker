from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai.report_summary import ReportSummaryService
from app.config import load_config
from app.dashboard.app import create_app
from app.db.database import Database
from app.events import (
    EventBus, SavedSearchExecuted, ScheduledReportCompleted,
    ScheduledReportDelivered, ScheduledReportStarted,
)
from app.reports import (
    ReportScheduler, ScheduledReportValidationError, calculate_next_run,
    format_report_message,
)
from app.search import SearchService, SearchValidationError


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def context(tmp_path: Path):
    config = replace(
        load_config(), database_path=tmp_path / "search.sqlite3",
        report_scheduler_enabled=False, report_output_dir=tmp_path / "scheduled",
        report_max_results=50, report_ai_summary_enabled=False,
        telegram_bot_token=None, telegram_chat_id=None,
    )
    db = Database(config.database_path)
    db.initialize()
    first = db.save_signal_history(
        "token + narrative", "TAO", "AI agents", 88, 92, 9, "research", 5,
    )
    second = db.save_signal_history(
        "token + narrative", "SOL", "Solana ecosystem", 65, 70, 7, "watch", 3,
    )
    db.save_signal_quality_score({
        "signal_id": first, "quality_score": 86, "classification": "excellent",
        "noise_risk": 8, "evaluation_coverage": 100, "evidence_count": 4,
        "calculation_version": 1, "breakdown_json": "{}",
        "calculated_at": datetime.now(timezone.utc).isoformat(),
    })
    db.save_signal_quality_score({
        "signal_id": second, "quality_score": 62, "classification": "moderate",
        "noise_risk": 35, "evaluation_coverage": 0, "evidence_count": 1,
        "calculation_version": 1, "breakdown_json": "{}",
        "calculated_at": datetime.now(timezone.utc).isoformat(),
    })
    watchlist_id = db.create_watchlist(
        "AI Narratives", "", True, 10, 0, 0, 0, True, True, True, True,
    )
    db.save_signal_watchlist(first, watchlist_id, "narrative", "AI agents")
    rule_id = db.create_alert_rule(
        "High quality", True, 5,
        {"field": "quality_score", "operator": "gte", "value": 80},
        ("dashboard_highlight",),
    )
    db.save_rule_match(first, rule_id, ("dashboard_highlight",))
    yield config, db, first, second
    db.close()


def search_payload(**changes):
    payload = {
        "name": "High Quality AI Signals", "description": "Focused AI research",
        "enabled": True, "target_type": "quality_signals",
        "filters": {
            "narrative": "AI agents", "quality_min": 80,
            "quality_classification": "excellent", "watchlist": ["AI Narratives"],
            "rule": "High quality", "minimum_evidence_count": 3,
        },
        "sort_by": "quality_score", "sort_direction": "desc", "result_limit": 10,
    }
    payload.update(changes)
    return payload


def report_payload(search_id: int, **changes):
    payload = {
        "name": "Daily AI Signals", "saved_search_id": search_id,
        "schedule_type": "daily", "schedule_value": "09:00", "timezone": "UTC",
        "delivery_type": "none", "include_summary": True,
        "include_top_results": True, "include_csv": False, "max_results": 10,
    }
    payload.update(changes)
    return payload


def test_saved_search_crud_duplicate_validation_and_migration(context, tmp_path: Path):
    config, db, *_ = context
    service = SearchService(db, config)
    created = service.create(search_payload())
    assert created.filters["quality_min"] == 80
    updated = service.update(created.id, {"description": "Updated", "result_limit": 5})
    assert updated.description == "Updated"
    assert updated.result_limit == 5
    duplicate = service.duplicate(created.id, "AI Signals Copy")
    assert duplicate.id != created.id
    with pytest.raises(SearchValidationError, match="already exists"):
        service.create(search_payload())
    with pytest.raises(SearchValidationError, match="target type"):
        service.create(search_payload(name="Bad", target_type="raw_sql"))
    with pytest.raises(SearchValidationError, match="Unsupported filter"):
        service.create(search_payload(name="Bad", filters={"sql": "DROP TABLE"}))
    with pytest.raises(SearchValidationError, match="cannot exceed"):
        service.create(search_payload(name="Bad", filters={"quality_min": 90, "quality_max": 20}))
    service.delete(duplicate.id)
    assert service.get(duplicate.id) is None

    legacy = tmp_path / "legacy.sqlite3"
    sqlite3.connect(legacy).execute("CREATE TABLE old_table (id INTEGER)").connection.close()
    migrated = Database(legacy)
    migrated.initialize()
    assert migrated.has_table("saved_searches")
    assert migrated.has_table("scheduled_reports")
    assert migrated.has_table("scheduled_report_runs")
    migrated.close()


def test_search_execution_preview_sort_limits_metadata_events_and_filters(context):
    config, db, first, _ = context
    events = []
    bus = EventBus()
    bus.subscribe(SavedSearchExecuted, events.append)
    service = SearchService(db, config, bus)
    saved = service.create(search_payload())
    preview = service.preview(saved.id)
    assert preview.total_matches == 1
    assert preview.results[0]["id"] == first
    assert preview.results[0]["quality_score"] == 86
    assert service.get(saved.id).run_count == 0

    executed = service.run(saved.id, limit=1)
    assert len(executed.results) == 1
    assert executed.search.run_count == 1
    assert executed.search.last_run_at
    assert events[0].saved_search_id == saved.id

    sorted_search = service.create(search_payload(
        name="All signals", target_type="signals", filters={},
        sort_by="hype_score", result_limit=1,
    ))
    result = service.run(sorted_search.id)
    assert result.total_matches == 2
    assert len(result.results) == 1
    assert result.results[0]["token"] == "TAO"


def test_entity_unified_event_and_graph_targets_reuse_existing_data(context):
    config, db, *_ = context
    service = SearchService(db, config)
    narratives = service.create(search_payload(
        name="Narratives", target_type="narratives",
        filters={"narrative": "AI agents", "hype_min": 80},
        sort_by="hype_score",
    ))
    assert service.preview(narratives.id).results[0]["name"] == "AI agents"

    source = db.connection.execute(
        "INSERT INTO content_sources (source_key,name,source_type,url) VALUES ('news','News','rss','https://example.test')"
    ).lastrowid
    item = db.connection.execute(
        """INSERT INTO content_items (
        source_id,external_id,title,body,fetched_at,content_hash,normalized_title
        ) VALUES (?,?,?,?,?,?,?)""",
        (source, "1", "AI update", "TAO", "2026-08-07T08:00:00+00:00", "hash", "ai update"),
    ).lastrowid
    event = db.connection.execute(
        """INSERT INTO unified_events (
        event_key,primary_content_item_id,title,tokens_json,narratives_json,
        first_seen_at,last_seen_at,source_count,item_count,hype_score,momentum_score,
        confidence,conflict_count,requires_review
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("event-1", item, "AI update", '["TAO"]', '["AI agents"]',
         "2026-08-07T08:00:00+00:00", "2026-08-07T08:00:00+00:00",
         3, 4, 84, 90, 9, 1, 1),
    ).lastrowid
    db.connection.execute(
        "INSERT INTO unified_event_items (unified_event_id,content_item_id,match_reason) VALUES (?,?,?)",
        (event, item, "primary"),
    )
    now = "2026-08-07T08:00:00+00:00"
    left = db.upsert_graph_node(
        node_type="narrative", entity_id="ai agents", label="AI agents",
        normalized_label="ai agents", weight=.8, activity_score=90,
        first_seen_at=now, last_seen_at=now, metadata_json="{}",
    )
    right = db.upsert_graph_node(
        node_type="token", entity_id="TAO", label="TAO", normalized_label="tao",
        weight=.75, activity_score=85, first_seen_at=now, last_seen_at=now,
        metadata_json="{}",
    )
    db.upsert_graph_edge(
        source_node_id=left, target_node_id=right,
        edge_type="narrative_mentions_token", derivation="observed", weight=.8,
        occurrence_increment=4, confidence=.9, first_seen_at=now, last_seen_at=now,
        metadata_json='{"source_count":3,"event_count":2,"hype_score":84,"momentum_score":90}',
    )
    db.connection.commit()

    events = service.create(search_payload(
        name="Unified events", target_type="unified_events",
        filters={"token": "TAO", "source": "News", "source_count_min": 3,
                 "conflict_status": "requires_review"},
        sort_by="momentum_score",
    ))
    assert service.preview(events.id).results[0]["id"] == event
    graph = service.create(search_payload(
        name="AI graph", target_type="graph_relationships",
        filters={"token": "TAO", "emerging_score_min": 1},
        sort_by="emerging_score",
    ))
    graph_result = service.preview(graph.id)
    assert graph_result.total_matches == 1
    assert graph_result.results[0]["target_label"] == "TAO"


@pytest.mark.parametrize(
    ("schedule_type", "value", "after", "expected"),
    [
        ("daily", "09:00", datetime(2026, 8, 7, 8, tzinfo=timezone.utc), "2026-08-07T09:00:00+00:00"),
        ("daily", "09:00", datetime(2026, 8, 7, 10, tzinfo=timezone.utc), "2026-08-08T09:00:00+00:00"),
        ("weekly", "MON@08:30", datetime(2026, 8, 7, 10, tzinfo=timezone.utc), "2026-08-10T08:30:00+00:00"),
        ("interval_hours", "6", datetime(2026, 8, 7, 10, tzinfo=timezone.utc), "2026-08-07T16:00:00+00:00"),
    ],
)
def test_next_run_calculation(schedule_type, value, after, expected):
    assert calculate_next_run(schedule_type, value, "UTC", after).isoformat() == expected


def test_named_timezone_is_converted_to_utc():
    after = datetime(2026, 8, 7, 4, tzinfo=timezone.utc)
    assert calculate_next_run("daily", "09:00", "Europe/Samara", after).isoformat() == (
        "2026-08-07T05:00:00+00:00"
    )


def test_scheduled_report_due_manual_lock_restart_and_run_history(context):
    config, db, *_ = context
    search = SearchService(db, config).create(search_payload())
    clock_value = datetime(2026, 8, 7, 8, tzinfo=timezone.utc)
    scheduler = ReportScheduler(db, config, clock=lambda: clock_value)
    report = scheduler.create(report_payload(search.id))
    assert report.next_run_at == "2026-08-07T09:00:00+00:00"
    assert scheduler.run_due() == []

    manual = scheduler.run(report.id)
    assert manual["status"] == "success"
    assert manual["result_count"] == 1
    assert scheduler.get(report.id).total_runs == 1
    assert scheduler.runs(report.id)[0]["status"] == "success"

    later = datetime(2026, 8, 8, 9, 1, tzinfo=timezone.utc)
    restarted = ReportScheduler(db, config, clock=lambda: later)
    due = restarted.run_due()
    assert len(due) == 1
    assert restarted.get(report.id).total_runs == 2

    run_id = db.claim_scheduled_report(report.id, later.isoformat(), force=True)
    assert run_id is not None
    assert restarted.run(report.id) is None


class FakeTelegram:
    messages: list[str] = []

    def __init__(self, *_args, **_kwargs):
        pass

    def send_saved_search_report(self, text: str):
        self.messages.append(text)


def test_telegram_delivery_html_escaping_size_events_and_csv(context):
    config, db, *_ = context
    config = replace(
        config, telegram_bot_token="test-token", telegram_chat_id="chat",
        report_output_dir=config.report_output_dir,
    )
    bus = EventBus()
    events = []
    for event_type in (ScheduledReportStarted, ScheduledReportCompleted, ScheduledReportDelivered):
        bus.subscribe(event_type, events.append)
    service = SearchService(db, config)
    search = service.create(search_payload(name="AI <Signals>"))
    scheduler = ReportScheduler(db, config, bus, telegram_factory=FakeTelegram)
    report = scheduler.create(report_payload(
        search.id, name="Daily / AI: Signals", delivery_type="telegram", include_csv=True,
    ))
    outcome = scheduler.run(report.id)
    assert outcome["status"] == "success"
    assert outcome["delivery_status"] == "sent"
    assert Path(outcome["csv_path"]).is_file()
    assert "Daily_AI_Signals" in Path(outcome["csv_path"]).name
    message = FakeTelegram.messages[-1]
    assert "AI &lt;Signals&gt;" in message
    assert len(message) <= 4096
    assert {type(item) for item in events} == {
        ScheduledReportStarted, ScheduledReportCompleted, ScheduledReportDelivered,
    }

    huge = service.create(search_payload(name="X" * 120))
    preview_report = scheduler.create(report_payload(huge.id, name="Bounded message"))
    result = service.preview(huge.id)
    assert len(format_report_message(preview_report, result, "Y" * 10000)) <= 4000


def test_csv_retention_only_removes_scheduler_files(context):
    config, db, *_ = context
    config.report_output_dir.mkdir(parents=True)
    old = config.report_output_dir / "old.csv"
    old.write_text("old", encoding="utf-8")
    outside = config.report_output_dir.parent / "manual.csv"
    outside.write_text("manual", encoding="utf-8")
    old_time = (datetime.now(timezone.utc) - timedelta(days=40)).timestamp()
    os.utime(old, (old_time, old_time))
    removed = ReportScheduler(db, config).cleanup_csv_retention()
    assert removed == 1
    assert not old.exists()
    assert outside.exists()


def test_scheduler_health_and_observability_metrics(context):
    config, db, *_ = context
    search = SearchService(db, config).create(search_payload())
    scheduler = ReportScheduler(db, config)
    scheduler.create(report_payload(search.id))
    health = scheduler.health()
    assert health["status"] == "disabled"
    assert health["enabled_reports"] == 1
    exposition = __import__("app.observability.metrics", fromlist=["metrics"]).metrics.prometheus().decode()
    assert "saved_search_runs_total" in exposition
    assert "scheduled_report_runs_total" in exposition
    assert "scheduled_report_duration_seconds" in exposition or "operation_duration_seconds" in exposition


class FakeResponse:
    output_text = "AI activity remains strong across the filtered evidence."


class FakeOpenAIClient:
    class Responses:
        def create(self, **_kwargs):
            return FakeResponse()
    responses = Responses()


class FailingOpenAIClient(FakeOpenAIClient):
    class Responses:
        def create(self, **_kwargs):
            raise RuntimeError("offline")
    responses = Responses()


def test_optional_ai_summary_and_fallback(context):
    config, db, *_ = context
    enabled = replace(config, report_ai_summary_enabled=True, openai_api_key="test")
    payload = {"match_count": 2}
    assert "remains strong" in ReportSummaryService(db, enabled, FakeOpenAIClient()).summarize(payload, "fallback")
    assert ReportSummaryService(db, enabled, FailingOpenAIClient()).summarize({"different": 1}, "fallback") == "fallback"


def test_dashboard_and_rest_api(context):
    config, db, *_ = context
    db.close()
    with TestClient(create_app(config.database_path, config=config)) as client:
        created = client.post("/api/saved-searches", json=search_payload())
        assert created.status_code == 201
        search_id = created.json()["id"]
        assert client.get("/saved-searches").status_code == 200
        assert client.get(f"/saved-searches/{search_id}").status_code == 200
        assert client.post(f"/api/saved-searches/{search_id}/preview").json()["total_matches"] == 1
        report = client.post("/api/scheduled-reports", json=report_payload(search_id))
        assert report.status_code == 201
        report_id = report.json()["id"]
        assert client.get("/scheduled-reports").status_code == 200
        assert client.get(f"/scheduled-reports/{report_id}").status_code == 200
        assert client.post(f"/api/scheduled-reports/{report_id}/preview").status_code == 200
        assert client.get(f"/api/scheduled-reports/{report_id}/runs").status_code == 200
        assert client.put(f"/api/saved-searches/{search_id}", json={"description": "API update"}).status_code == 200
        assert client.delete(f"/api/scheduled-reports/{report_id}").status_code == 204
        assert client.delete(f"/api/saved-searches/{search_id}").status_code == 204


def test_cli_commands_are_backward_compatible(context):
    config, db, *_ = context
    search = SearchService(db, config).create(search_payload())
    report = ReportScheduler(db, config).create(report_payload(search.id))
    db.close()
    env = {**os.environ, "DATABASE_PATH": str(config.database_path),
           "REPORT_SCHEDULER_ENABLED": "false", "AI_PROVIDER": "mock"}
    for arguments in (
        ["--list-saved-searches"], ["--saved-search-details", str(search.id)],
        ["--run-saved-search", str(search.id)], ["--list-scheduled-reports"],
        ["--scheduled-report-details", str(report.id)],
    ):
        result = subprocess.run(
            [sys.executable, "-m", "app.main", *arguments], cwd=ROOT, env=env,
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stderr
