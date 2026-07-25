from __future__ import annotations

import csv
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import load_config
from app.dashboard import create_app
from app.db.database import Database
from app.events import (
    ContentAccepted,
    ContentDeduplicated,
    EventBus,
    UnifiedEventCreated,
    UnifiedEventMateriallyChanged,
)
from app.export.csv_exporter import CSVExportService
from app.ingestion.deduplication import jaccard_similarity
from app.ingestion.models import (
    NormalizedContentItem,
    SourceDefinition,
    canonicalize_url,
)
from app.ingestion.service import MultiSourceIngestionService, format_deduplication_report
from app.rules.models import SignalFacts, evaluate_condition


class MultiSourceIngestionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "tracker.sqlite3")
        self.db.initialize()
        self.sources_path = self.root / "sources.json"
        self.config = replace(
            load_config(),
            database_path=self.db.path,
            content_sources_path=self.sources_path,
            deduplication_min_shared_entities=1,
            event_update_min_new_sources=1,
            event_update_cooldown_minutes=0,
        )

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def write_source(self, key: str, filename: str, items: list[dict], priority=5):
        path = self.root / filename
        path.write_text(json.dumps({"items": items}), encoding="utf-8")
        return {
            "id": key,
            "name": key.title(),
            "type": "local_json",
            "url": str(path),
            "priority": priority,
            "fetch_interval_seconds": 300,
        }

    def test_url_canonicalization_removes_tracking_and_fragment(self) -> None:
        self.assertEqual(
            canonicalize_url("HTTPS://Example.com/news/?utm_source=x&b=2&a=1#top"),
            "https://example.com/news?a=1&b=2",
        )

    def test_source_validation_rejects_unknown_type(self) -> None:
        with self.assertRaises(ValueError):
            SourceDefinition.from_mapping(
                {"id": "bad", "name": "Bad", "type": "paid", "url": "x"}
            )

    def test_local_sources_create_one_event_for_exact_duplicate(self) -> None:
        first = self.write_source(
            "alpha", "alpha.json",
            [{"id": "a1", "title": "Solana usage rises", "text": "SOL activity expands.",
              "url": "https://example.com/story?utm_source=alpha"}],
            4,
        )
        second = self.write_source(
            "beta", "beta.json",
            [{"id": "b1", "title": "Solana usage rises", "text": "SOL activity expands.",
              "url": "https://example.com/story"}],
            9,
        )
        self.sources_path.write_text(json.dumps([first, second]), encoding="utf-8")
        result = MultiSourceIngestionService(self.db, self.config).fetch_all()
        self.assertEqual((result.fetched_count, result.accepted_count, result.duplicate_count), (2, 1, 1))
        events = self.db.get_unified_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["source_count"], 2)
        self.assertEqual(events[0]["primary_source_name"], "Beta")

    def test_near_duplicate_requires_shared_entity(self) -> None:
        self.assertGreater(jaccard_similarity("SOL usage rises quickly", "SOL usage rises fast"), 0.4)
        first = self.write_source(
            "one", "one.json",
            [{"id": "1", "title": "SOL network activity rises quickly", "text": "Solana users return"}],
        )
        second = self.write_source(
            "two", "two.json",
            [{"id": "2", "title": "SOL network activity rises fast", "text": "Solana users return today"}],
        )
        self.sources_path.write_text(json.dumps([first, second]), encoding="utf-8")
        config = replace(self.config, deduplication_title_similarity_threshold=0.6)
        result = MultiSourceIngestionService(self.db, config).fetch_all()
        self.assertEqual(result.duplicate_count, 1)

    def test_event_bus_publishes_accept_dedup_create_and_material_update(self) -> None:
        first = self.write_source("one", "one.json", [{"id": "1", "title": "BTC ETF approved", "text": "BTC ETF approved"}])
        second = self.write_source("two", "two.json", [{"id": "2", "title": "BTC ETF approved", "text": "BTC ETF approved"}], 9)
        self.sources_path.write_text(json.dumps([first, second]), encoding="utf-8")
        bus = EventBus()
        seen: list[object] = []
        for event_type in (ContentAccepted, ContentDeduplicated, UnifiedEventCreated, UnifiedEventMateriallyChanged):
            bus.subscribe(event_type, seen.append)
        MultiSourceIngestionService(self.db, self.config, bus).fetch_all()
        self.assertTrue(any(isinstance(item, ContentAccepted) for item in seen))
        self.assertTrue(any(isinstance(item, ContentDeduplicated) for item in seen))
        self.assertTrue(any(isinstance(item, UnifiedEventCreated) for item in seen))
        self.assertTrue(any(isinstance(item, UnifiedEventMateriallyChanged) for item in seen))

    def test_repeated_fetch_is_idempotent_for_event_membership(self) -> None:
        source = self.write_source("one", "one.json", [{"id": "1", "title": "ETH upgrade", "text": "ETH infrastructure"}])
        self.sources_path.write_text(json.dumps([source]), encoding="utf-8")
        service = MultiSourceIngestionService(self.db, self.config)
        service.fetch_all()
        service.fetch_all()
        event = self.db.get_unified_events()[0]
        self.assertEqual(event["item_count"], 1)
        self.assertEqual(len(self.db.get_unified_events()), 1)

    def test_deduplication_report_is_valid_on_empty_database(self) -> None:
        report = format_deduplication_report(self.db)
        self.assertIn("Raw items fetched: 0", report)
        self.assertIn("Duplicate reduction: 0.0%", report)

    def test_rule_condition_can_use_unified_event_fields(self) -> None:
        facts = SignalFacts("SOL", "Solana ecosystem", 80, 70, 8, 5, 60,
                            source_count=4, item_count=6, source_priority=9,
                            conflict_count=1, requires_review=True)
        condition = {"AND": [
            {"field": "source_count", "operator": "gte", "value": 3},
            {"field": "requires_review", "operator": "eq", "value": True},
        ]}
        self.assertTrue(evaluate_condition(condition, facts))

    def test_csv_exports_sources_items_events_and_deduplication(self) -> None:
        source = self.write_source("one", "one.json", [{"id": "1", "title": "RWA grows", "text": "RWA treasury"}])
        self.sources_path.write_text(json.dumps([source]), encoding="utf-8")
        MultiSourceIngestionService(self.db, self.config).fetch_all()
        result = CSVExportService(
            self.db, self.root / "exports", clock=lambda: datetime(2026, 7, 25, 12)
        ).export(("sources", "content_items", "unified_events", "deduplication"))
        self.assertEqual(len(result.files), 4)
        for exported in result.files:
            self.assertEqual(exported.path.read_bytes()[:3], b"\xef\xbb\xbf")
            with exported.path.open(encoding="utf-8-sig", newline="") as handle:
                self.assertTrue(next(csv.reader(handle)))

    def test_dashboard_pages_and_api_expose_ingestion_data(self) -> None:
        source = self.write_source("one", "one.json", [{"id": "1", "title": "DePIN expands", "text": "DePIN infrastructure"}])
        self.sources_path.write_text(json.dumps([source]), encoding="utf-8")
        MultiSourceIngestionService(self.db, self.config).fetch_all()
        client = TestClient(create_app(self.db.path, config=self.config))
        self.assertEqual(client.get("/sources").status_code, 200)
        self.assertEqual(client.get("/unified-events").status_code, 200)
        self.assertEqual(client.get("/deduplication").status_code, 200)
        self.assertEqual(len(client.get("/api/sources").json()["sources"]), 1)
        event_id = client.get("/api/unified-events").json()["events"][0]["id"]
        self.assertEqual(client.get(f"/api/unified-events/{event_id}").status_code, 200)
        source_id = client.get("/api/sources").json()["sources"][0]["id"]
        self.assertEqual(client.get(f"/api/sources/{source_id}").status_code, 200)
        self.assertFalse(client.post(f"/api/sources/{source_id}/disable").json()["enabled"])
        self.assertTrue(client.post(f"/api/sources/{source_id}/enable").json()["enabled"])
        self.assertEqual(
            len(client.get(f"/api/unified-events/{event_id}/items").json()["items"]), 1
        )
        self.assertIn(
            "history", client.get(f"/api/unified-events/{event_id}/history").json()
        )
        self.assertIn("raw_items", client.get("/api/deduplication/stats").json())

    def test_rebuild_is_idempotent_and_preserves_content(self) -> None:
        source = self.write_source(
            "one", "one.json", [{"id": "1", "title": "Gaming event", "text": "Gaming token update"}]
        )
        self.sources_path.write_text(json.dumps([source]), encoding="utf-8")
        service = MultiSourceIngestionService(self.db, self.config)
        service.fetch_all()
        first = service.events.rebuild()
        second = service.events.rebuild()
        self.assertEqual(first, (1, 0))
        self.assertEqual(second, (1, 0))
        self.assertEqual(len(self.db.get_content_items(limit=None)), 1)

    def test_backward_compatible_schema_initialization_is_repeatable(self) -> None:
        self.db.initialize()
        self.db.initialize()
        tables = {"content_sources", "content_items", "unified_events", "unified_event_items"}
        self.assertTrue(all(self.db.has_table(table) for table in tables))
        columns = {
            row["name"]
            for row in self.db.connection.execute("PRAGMA table_info(signal_history)")
        }
        self.assertIn("unified_event_id", columns)


if __name__ == "__main__":
    unittest.main()
