from __future__ import annotations

import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import load_config
from app.dashboard import create_app
from app.db.database import Database
from app.events import EventBus, GraphSnapshotCreated, GraphUpdated
from app.export.csv_exporter import CSVExportService
from app.graph.models import GraphEdge, normalize_entity
from app.graph.service import GraphService
from app.graph.weights import GraphWeightCalculator, classify_relationship
from app.ingestion.service import MultiSourceIngestionService
from app.main import parse_args, requested_csv_exports, requested_graph_command
from app.rules import RuleService, SignalFacts, evaluate_condition
from app.watchlists import WatchlistService


class NarrativeGraphTests(unittest.TestCase):
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
            graph_min_edge_weight=0,
            graph_min_node_weight=0,
            graph_max_nodes=250,
            graph_ai_relationship_min_confidence=0.75,
        )
        self.graph = GraphService(self.db, self.config)

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def _signal(
        self,
        token: str = "SOL",
        narrative: str = "Solana ecosystem",
        *,
        hype: float = 82,
        momentum: float = 76,
        unified_event_id: int | None = None,
    ) -> int:
        return self.db.save_signal_history(
            "token + narrative", token, narrative, hype, momentum, 8,
            "research", 4, unified_event_id=unified_event_id,
        )

    def _event(self) -> int:
        source_path = self.root / "source.json"
        source_path.write_text(
            json.dumps({"items": [{
                "id": "story-1",
                "title": "Solana AI infrastructure expands",
                "text": "SOL and TAO activity connect AI Agents with DePIN.",
                "url": "https://example.test/sol-ai",
                "tokens": ["SOL", "TAO"],
                "narratives": ["Solana ecosystem", "AI Agents", "DePIN"],
            }]}),
            encoding="utf-8",
        )
        self.sources_path.write_text(
            json.dumps([{
                "id": "local-research",
                "name": "Local Research",
                "type": "local_json",
                "url": str(source_path),
                "priority": 8,
                "fetch_interval_seconds": 300,
            }]),
            encoding="utf-8",
        )
        MultiSourceIngestionService(self.db, self.config).fetch_all()
        return int(self.db.get_unified_events()[0]["id"])

    def _ai_result(self, confidence: int) -> dict:
        return {
            "provider": "mock",
            "model": "deterministic-rules",
            "prompt_version": "test-v1",
            "summary": "Related infrastructure signal.",
            "why_it_matters": "The narratives share observed entities.",
            "action": "research",
            "confidence": confidence,
            "risk_level": "medium",
            "supporting_factors": ["co-occurrence"],
            "risk_factors": ["limited history"],
            "related_tokens": ["TAO"],
            "related_narratives": ["AI Agents"],
            "market_context": "Narrative continuation only.",
            "invalidation_conditions": ["attention fades"],
        }

    def test_token_and_narrative_normalization(self) -> None:
        self.assertEqual(normalize_entity("token", " bitcoin ")[0], "BTC")
        self.assertEqual(normalize_entity("token", "$btc")[0], "BTC")
        self.assertEqual(
            normalize_entity("narrative", "  AI   Agents ")[0], "ai agents"
        )

    def test_node_and_edge_upserts_are_deterministic(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        btc = self.graph._node("token", "btc", "BTC", now)
        duplicate = self.graph._node("token", "Bitcoin", "Bitcoin", now)
        ai = self.graph._node("narrative", "AI Agents", "AI Agents", now)
        self.assertEqual(btc, duplicate)

        first = self.graph._edge(
            btc, ai, "narrative_related_to_narrative", now, "evidence:1", {}
        )
        second = self.graph._edge(
            ai, btc, "narrative_related_to_narrative", now, "evidence:1", {}
        )
        row = self.db.connection.execute(
            "SELECT * FROM graph_edges WHERE id = ?", (first,)
        ).fetchone()
        self.assertEqual(first, second)
        self.assertLess(int(row["source_node_id"]), int(row["target_node_id"]))
        self.assertEqual(row["occurrence_count"], 1)

        self.graph._edge(
            ai, btc, "narrative_related_to_narrative", now, "evidence:2", {}
        )
        self.assertEqual(self.db.get_graph_edges(limit=None)[0]["occurrence_count"], 2)

    def test_weight_formula_is_bounded_explainable_and_decays(self) -> None:
        calculator = GraphWeightCalculator(half_life_days=14)
        now = datetime.now(timezone.utc)
        recent = calculator.edge_weight(
            occurrence_count=6, source_count=4, event_count=3,
            hype_score=90, momentum_score=80, confidence=9,
            outcome_success_rate=70, priority=80,
            last_seen_at=now.isoformat(), now=now,
        )
        old = calculator.edge_weight(
            occurrence_count=6, source_count=4, event_count=3,
            hype_score=90, momentum_score=80, confidence=9,
            outcome_success_rate=70, priority=80,
            last_seen_at=(now - timedelta(days=28)).isoformat(), now=now,
        )
        self.assertGreater(recent, old)
        self.assertTrue(0 <= old <= recent <= 1)
        self.assertAlmostEqual(calculator.recency_decay(
            (now - timedelta(days=14)).isoformat(), now
        ), 0.5, places=4)

    def test_relationship_classifications_cover_growth_and_decay(self) -> None:
        self.assertEqual(classify_relationship(80, 1, 5, 2), "accelerating")
        self.assertEqual(classify_relationship(60, 1, 3, 1), "emerging")
        self.assertEqual(classify_relationship(25, 1, 2, 2), "stable")
        self.assertEqual(classify_relationship(30, 0.4, 2, 2), "weakening")
        self.assertEqual(classify_relationship(10, 0.1, 2, 2), "inactive")

    def test_signal_builds_nodes_edge_metrics_and_outcomes(self) -> None:
        signal_id = self._signal()
        self.db.save_signal_outcome(signal_id, 24, "SUCCESS", 12, 2, 10, "continued")
        self.graph.update_signal(signal_id)

        detail = self.graph.node_detail("token", "sol")
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail["connected_narrative_count"], 1)
        self.assertEqual(detail["outcomes"]["success_rate"], 100.0)
        self.assertGreater(detail["weighted_degree"], 0)

    def test_unified_event_builds_source_entities_and_cooccurrences(self) -> None:
        event_id = self._event()
        self.graph.update_event(event_id)
        types = {row["node_type"] for row in self.db.get_graph_nodes(limit=None)}
        edge_types = {row["edge_type"] for row in self.db.get_graph_edges(limit=None)}
        self.assertTrue({"source", "unified_event", "token", "narrative"} <= types)
        self.assertTrue({
            "source_reports_event", "event_contains_narrative",
            "event_mentions_token", "token_co_occurs_with_token",
            "narrative_related_to_narrative",
        } <= edge_types)

    def test_watchlist_filter_and_rule_relationships(self) -> None:
        event_id = self._event()
        signal_id = self._signal(unified_event_id=event_id)
        watchlists = WatchlistService(self.db)
        watchlist = watchlists.create_watchlist("AI focus", priority=9)
        watchlists.add_item(watchlist.id, "token", "SOL")
        watchlists.add_item(watchlist.id, "narrative", "AI Agents")
        self.db.save_signal_watchlist(signal_id, watchlist.id, "token", "SOL")
        rule = RuleService(self.db).create_rule(
            "Bridge alert", {"field": "bridge_score", "operator": ">=", "value": 20},
            ["dashboard_highlight"], priority=8,
        )
        self.db.save_rule_match(signal_id, rule.id, ("dashboard_highlight",))

        self.graph.update_event(event_id)
        self.graph.update_watchlist(watchlist.id)
        self.graph.update_rule_match(rule.id, signal_id)
        filtered = self.graph.graph_view(watchlist_id=watchlist.id, min_weight=0)
        self.assertEqual(
            {node["node_type"] for node in filtered["nodes"]},
            {"watchlist", "token", "narrative", "rule"},
        )
        edge_types = {row["edge_type"] for row in self.db.get_graph_edges(limit=None)}
        self.assertIn("rule_triggered_by_event", edge_types)
        self.assertIn("rule_matches_watchlist", edge_types)

    def test_ai_edges_are_separate_and_thresholded(self) -> None:
        low_signal = self._signal("SOL", "Solana ecosystem")
        self.db.save_signal_ai_analysis(low_signal, self._ai_result(7))
        self.graph.update_ai_analysis(low_signal)
        self.assertFalse(self.db.get_graph_edges(limit=None))

        high_signal = self._signal("SOL", "Solana ecosystem", hype=83)
        self.db.save_signal_ai_analysis(high_signal, self._ai_result(9))
        self.graph.update_ai_analysis(high_signal)
        edges = [GraphEdge.from_row(row) for row in self.db.get_graph_edges(limit=None)]
        self.assertTrue(edges)
        self.assertTrue(all(edge.derivation == "ai" for edge in edges))
        self.assertTrue(all(edge.confidence == 0.9 for edge in edges))
        self.assertTrue(all(edge.metadata["ai_confidence"] == 0.9 for edge in edges))

    def test_incremental_event_publication_and_rebuild_idempotency(self) -> None:
        signal_id = self._signal()
        bus = EventBus()
        seen: list[GraphUpdated] = []
        bus.subscribe(GraphUpdated, seen.append)
        incremental = GraphService(self.db, self.config, bus)
        incremental.update_signal(signal_id)
        self.assertEqual(seen[-1].update_reason, "signal")
        first = incremental.rebuild()
        second = incremental.rebuild()
        self.assertEqual(first["nodes"], second["nodes"])
        self.assertEqual(first["edges"], second["edges"])
        self.assertIsNotNone(self.db.get_signal(signal_id))

    def test_metrics_components_bridge_and_emerging_scores(self) -> None:
        now = datetime.now(timezone.utc).isoformat()
        center = self.graph._node("token", "TAO", "TAO", now)
        leaves = [
            self.graph._node("narrative", name, name, now)
            for name in ("AI Agents", "DePIN", "Infrastructure")
        ]
        for index, leaf in enumerate(leaves):
            self.graph._edge(
                center, leaf, "narrative_mentions_token", now,
                f"event:{index}",
                {"source_count": 3, "event_count": 2, "hype_score": 90,
                 "momentum_score": 85, "confidence": 9},
            )
        self.graph._refresh_node_weights()
        view = self.graph.graph_view(min_weight=0)
        tao = next(node for node in view["nodes"] if node["label"] == "TAO")
        self.assertEqual(tao["degree"], 3)
        self.assertGreater(tao["weighted_degree"], 0)
        self.assertGreater(tao["bridge_score"], 0)
        self.assertEqual(view["metrics"]["component_count"], 1)
        self.assertGreater(self.graph.emerging()[0]["emerging_relationship_score"], 0)

    def test_minimum_weight_and_period_filters_apply_fresh_decay(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
        left = self.graph._node("token", "SOL", "SOL", old)
        right = self.graph._node("narrative", "Solana ecosystem", "Solana ecosystem", old)
        self.graph._edge(left, right, "narrative_mentions_token", old, "old:1", {})
        self.assertEqual(self.graph.graph_view(period_days=30, min_weight=0)["nodes"], [])
        self.assertEqual(self.graph.graph_view(period_days=365, min_weight=0.9)["edges"], [])

    def test_snapshots_are_idempotent_and_publish_once(self) -> None:
        self.graph.update_signal(self._signal())
        bus = EventBus()
        events: list[GraphSnapshotCreated] = []
        bus.subscribe(GraphSnapshotCreated, events.append)
        service = GraphService(self.db, self.config, bus)
        first, created = service.create_snapshot("daily")
        second, duplicate_created = service.create_snapshot("daily")
        self.assertTrue(created)
        self.assertFalse(duplicate_created)
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(events), 1)

    def test_validation_reports_invalid_orphan_edge_without_repairing(self) -> None:
        self.db.connection.execute("PRAGMA foreign_keys = OFF")
        self.db.connection.execute(
            """
            INSERT INTO graph_edges (
                source_node_id, target_node_id, edge_type, derivation, weight,
                occurrence_count, confidence, first_seen_at, last_seen_at, metadata_json
            ) VALUES (9001, 9002, 'narrative_mentions_token', 'observed', .5, 1, .8,
                      CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, '{}')
            """
        )
        self.db.connection.commit()
        issues = self.graph.validate()
        self.assertTrue(any(issue["type"] == "missing_node" for issue in issues))
        count = self.db.connection.execute(
            "SELECT COUNT(*) FROM graph_edges"
        ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_rule_conditions_accept_graph_metrics(self) -> None:
        facts = SignalFacts(
            "TAO", "AI Agents", 80, 85, 8, 4, 70,
            node_degree=8, weighted_degree=4.2, bridge_score=65,
            emerging_relationship_score=78, source_diversity=4,
            connected_narrative_count=3, connected_token_count=2,
        )
        self.assertTrue(evaluate_condition({"AND": [
            {"field": "emerging_relationship_score", "operator": ">=", "value": 75},
            {"field": "source_diversity", "operator": ">=", "value": 3},
            {"field": "bridge_score", "operator": ">=", "value": 60},
        ]}, facts))

    def test_csv_exports_have_bom_headers_and_rows(self) -> None:
        self.graph.update_signal(self._signal())
        self.graph.create_snapshot("daily")
        result = CSVExportService(
            self.db, self.root / "exports",
            clock=lambda: datetime(2026, 7, 27, 12, 30),
        ).export((
            "graph_nodes", "graph_edges", "emerging_relationships",
            "graph_snapshots",
        ))
        self.assertEqual(len(result.files), 4)
        for item in result.files:
            self.assertEqual(item.path.read_bytes()[:3], b"\xef\xbb\xbf")
            with item.path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.reader(handle))
            self.assertTrue(rows[0])
            self.assertGreaterEqual(len(rows), 2)

    def test_cli_flags_and_csv_selection(self) -> None:
        with patch.object(sys, "argv", ["tracker", "--graph-node", "token", "SOL"]):
            args = parse_args()
        self.assertTrue(requested_graph_command(args))
        self.assertEqual(args.graph_node, ["token", "SOL"])
        with patch.object(sys, "argv", ["tracker", "--export-graph-edges-csv"]):
            args = parse_args()
        self.assertEqual(requested_csv_exports(args), ("graph_edges",))

    def test_dashboard_pages_and_all_graph_apis(self) -> None:
        self.graph.update_signal(self._signal())
        self.graph.create_snapshot("daily")
        client = TestClient(create_app(self.db.path, config=self.config))
        try:
            for path, heading in (
                ("/graph", "Relationship Graph"),
                ("/graph/emerging", "Emerging Relationships"),
                ("/graph/bridges", "Bridge Nodes"),
                ("/graph/analytics", "Graph Analytics"),
                ("/graph/nodes/token/SOL", "SOL"),
            ):
                response = client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn(heading, response.text)
            for path in (
                "/api/graph?min_weight=0", "/api/graph/nodes?min_weight=0",
                "/api/graph/nodes/token/SOL", "/api/graph/edges?min_weight=0",
                "/api/graph/summary", "/api/graph/emerging",
                "/api/graph/bridges", "/api/graph/snapshots", "/api/graph/validate",
            ):
                self.assertEqual(client.get(path).status_code, 200, path)
            self.assertEqual(
                client.post("/api/graph/snapshots", json={"frequency": "weekly"}).status_code,
                201,
            )
            self.assertEqual(client.post("/api/graph/rebuild").status_code, 200)
            self.assertEqual(client.get("/api/graph?limit=501").status_code, 422)
            self.assertEqual(client.get("/api/graph?node_type=unknown").status_code, 422)
        finally:
            client.close()

    def test_schema_migration_is_repeatable_and_preserves_existing_data(self) -> None:
        signal_id = self._signal()
        self.db.initialize()
        self.db.initialize()
        self.assertTrue(all(self.db.has_table(name) for name in (
            "graph_nodes", "graph_edges", "graph_snapshots"
        )))
        self.assertIsNotNone(self.db.get_signal(signal_id))
        indexes = self.db.connection.execute("PRAGMA index_list(graph_edges)").fetchall()
        self.assertTrue(any(bool(row["unique"]) for row in indexes))


if __name__ == "__main__":
    unittest.main()
