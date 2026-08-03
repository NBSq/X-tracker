from __future__ import annotations

import json
import logging
import time
from collections import Counter, defaultdict, deque
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from itertools import combinations
from typing import Any, Iterable

from app.config import Config
from app.db.database import Database
from app.events.bus import EventBus
from app.events.models import (
    EmergingRelationshipDetected,
    GraphSnapshotCreated,
    GraphUpdated,
)
from app.graph.models import (
    DERIVATIONS,
    EDGE_TYPES,
    NODE_TYPES,
    UNDIRECTED_EDGE_TYPES,
    GraphEdge,
    GraphMetrics,
    GraphNode,
    GraphSnapshot,
    normalize_entity,
)
from app.graph.weights import GraphWeightCalculator, classify_relationship
from app.observability.timing import timed


logger = logging.getLogger("x_narrative_tracker")


class GraphService:
    def __init__(
        self,
        db: Database,
        config: Config,
        event_bus: EventBus | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.event_bus = event_bus
        self.weights = GraphWeightCalculator(config.graph_recency_half_life_days)

    @timed("graph_update")
    def rebuild(self) -> dict[str, int]:
        started = time.perf_counter()
        self.db.clear_graph_projection()
        processed_events = 0
        for row in self.db.get_unified_events(limit=None):
            self.update_event(int(row["id"]), publish=False)
            processed_events += 1
        processed_signals = 0
        for row in reversed(self.db.get_signals(limit=None)):
            self.update_signal(int(row["id"]), publish=False)
            processed_signals += 1
        for row in self.db.get_watchlists():
            self.update_watchlist(int(row["id"]), publish=False)
        self._rebuild_rule_edges()
        self._rebuild_ai_edges()
        self._refresh_node_weights()
        counts = {
            "nodes": len(self.db.get_graph_nodes(limit=None)),
            "edges": len(self.db.get_graph_edges(limit=None)),
            "events_processed": processed_events,
            "signals_processed": processed_signals,
        }
        logger.info(
            "Graph rebuild complete node_count=%s edge_count=%s duration_ms=%s",
            counts["nodes"], counts["edges"],
            int((time.perf_counter() - started) * 1000),
        )
        self._publish(GraphUpdated("rebuild", counts["nodes"], counts["edges"]))
        return counts

    @timed("graph_update")
    def update_event(self, event_id: int, *, publish: bool = True) -> None:
        event = self.db.get_unified_event(event_id)
        if event is None:
            return
        timestamp = str(event["last_seen_at"] or event["created_at"])
        event_node = self._node(
            "unified_event", event_id, str(event["title"]), timestamp,
            activity=max(float(event["hype_score"]), float(event["momentum_score"])),
            metadata={
                "source_count": int(event["source_count"]),
                "item_count": int(event["item_count"]),
                "status": str(event["status"]),
                "requires_review": bool(event["requires_review"]),
            },
            first_seen=str(event["first_seen_at"]),
        )
        narratives = _json_strings(event["narratives_json"])
        tokens = _json_strings(event["tokens_json"])
        edge_metrics = {
            "source_count": int(event["source_count"]),
            "event_count": 1,
            "hype_score": float(event["hype_score"]),
            "momentum_score": float(event["momentum_score"]),
            "confidence": float(event["confidence"]),
        }
        narrative_nodes = [
            self._node("narrative", name, name, timestamp, activity=edge_metrics["hype_score"])
            for name in narratives
        ]
        token_nodes = [
            self._node("token", symbol, symbol, timestamp, activity=edge_metrics["hype_score"])
            for symbol in tokens
        ]
        evidence = f"event:{event_id}"
        for node in narrative_nodes:
            self._edge(event_node, node, "event_contains_narrative", timestamp, evidence, edge_metrics)
        for node in token_nodes:
            self._edge(event_node, node, "event_mentions_token", timestamp, evidence, edge_metrics)
        for narrative_node in narrative_nodes:
            for token_node in token_nodes:
                self._edge(
                    narrative_node, token_node, "narrative_mentions_token",
                    timestamp, evidence, edge_metrics,
                )
        for left, right in combinations(narrative_nodes, 2):
            self._edge(
                left, right, "narrative_related_to_narrative", timestamp,
                evidence, edge_metrics,
            )
        for left, right in combinations(token_nodes, 2):
            self._edge(
                left, right, "token_co_occurs_with_token", timestamp,
                evidence, edge_metrics,
            )
        seen_sources = set()
        for item in self.db.get_unified_event_items(event_id):
            source_id = int(item["source_id"])
            if source_id in seen_sources:
                continue
            seen_sources.add(source_id)
            source_node = self._node(
                "source", source_id, str(item["source_name"]), timestamp,
                activity=float(item["source_priority"]) * 10,
                metadata={"source_type": str(item["source_type"])},
            )
            self._edge(
                source_node, event_node, "source_reports_event", timestamp,
                evidence, {**edge_metrics, "priority": float(item["source_priority"]) * 10},
            )
        if publish:
            self._refresh_node_weights()
            self._publish(GraphUpdated("unified_event", 1, 0))

    @timed("graph_update")
    def update_signal(self, signal_id: int, *, publish: bool = True) -> None:
        signal = self.db.get_signal(signal_id)
        if signal is None:
            return
        timestamp = str(signal["timestamp"])
        metrics = {
            "source_count": 1,
            "event_count": 1 if signal["unified_event_id"] else 0,
            "hype_score": float(signal["hype_score"]),
            "momentum_score": float(signal["momentum_score"]),
            "confidence": float(signal["confidence"]),
            "outcome_success_rate": self.db.get_entity_outcome_success_rate(
                signal["token"], signal["narrative"]
            ),
        }
        token_node = (
            self._node("token", signal["token"], signal["token"], timestamp,
                       activity=float(signal["hype_score"]))
            if signal["token"] else None
        )
        narrative_node = (
            self._node("narrative", signal["narrative"], signal["narrative"], timestamp,
                       activity=max(float(signal["hype_score"]), float(signal["momentum_score"])))
            if signal["narrative"] else None
        )
        if token_node and narrative_node:
            self._edge(
                narrative_node, token_node, "narrative_mentions_token", timestamp,
                f"signal:{signal_id}", metrics,
            )
        if publish:
            self._refresh_node_weights()
            self._publish(GraphUpdated("signal", 0, 1 if token_node and narrative_node else 0))

    @timed("graph_update")
    def update_watchlist(self, watchlist_id: int, *, publish: bool = True) -> None:
        watchlist = self.db.get_watchlist(watchlist_id)
        if watchlist is None:
            return
        timestamp = str(watchlist["updated_at"])
        watchlist_node = self._node(
            "watchlist", watchlist_id, str(watchlist["name"]), timestamp,
            activity=float(watchlist["priority"]),
            metadata={"enabled": bool(watchlist["enabled"])},
        )
        for item in self.db.get_watchlist_items(watchlist_id):
            node = self._node(
                str(item["item_type"]), item["item_value"], item["item_value"],
                timestamp,
            )
            self._edge(
                watchlist_node,
                node,
                f"watchlist_tracks_{item['item_type']}",
                timestamp,
                f"watchlist_item:{item['id']}",
                {"priority": float(watchlist["priority"])},
            )
        if publish:
            self._refresh_node_weights()
            self._publish(GraphUpdated("watchlist", 1, 0))

    @timed("graph_update")
    def update_ai_analysis(self, signal_id: int, *, publish: bool = True) -> None:
        signal = self.db.get_signal(signal_id)
        analysis = self.db.get_signal_ai_analysis(signal_id)
        if signal is None or analysis is None:
            return
        confidence = float(analysis["confidence"]) / 10
        if confidence < self.config.graph_ai_relationship_min_confidence:
            return
        timestamp = str(analysis["created_at"])
        own_token = str(signal["token"]) if signal["token"] else None
        own_narrative = str(signal["narrative"]) if signal["narrative"] else None
        related_tokens = _json_strings(analysis["related_tokens_json"])
        related_narratives = _json_strings(analysis["related_narratives_json"])
        metrics = {
            "hype_score": float(signal["hype_score"]),
            "momentum_score": float(signal["momentum_score"]),
            "confidence": float(signal["confidence"]),
            "ai_confidence": confidence,
        }
        evidence = f"ai:{analysis['id']}"
        if own_token:
            own = self._node("token", own_token, own_token, timestamp)
            for value in related_tokens:
                target = self._node("token", value, value, timestamp)
                self._edge(
                    own, target, "token_co_occurs_with_token", timestamp,
                    evidence, metrics, derivation="ai", confidence=confidence,
                )
        if own_narrative:
            own = self._node("narrative", own_narrative, own_narrative, timestamp)
            for value in related_narratives:
                target = self._node("narrative", value, value, timestamp)
                self._edge(
                    own, target, "narrative_related_to_narrative", timestamp,
                    evidence, metrics, derivation="ai", confidence=confidence,
                )
            for value in related_tokens:
                target = self._node("token", value, value, timestamp)
                self._edge(
                    own, target, "narrative_mentions_token", timestamp,
                    evidence, metrics, derivation="ai", confidence=confidence,
                )
        if publish:
            self._refresh_node_weights()
            self._publish(GraphUpdated("ai_analysis", 0, 1))

    @timed("graph_update")
    def update_rule_match(self, rule_id: int, signal_id: int, *, publish: bool = True) -> None:
        rule = self.db.get_alert_rule(rule_id)
        signal = self.db.get_signal(signal_id)
        if rule is None or signal is None:
            return
        timestamp = str(signal["timestamp"])
        rule_node = self._node(
            "rule", rule_id, str(rule["name"]), timestamp,
            activity=float(rule["priority"]), metadata={"enabled": bool(rule["enabled"])},
        )
        if signal["unified_event_id"]:
            event = self.db.get_unified_event(int(signal["unified_event_id"]))
            if event is not None:
                event_node = self._node(
                    "unified_event", event["id"], event["title"], timestamp
                )
                self._edge(
                    rule_node, event_node, "rule_triggered_by_event", timestamp,
                    f"rule_match:{rule_id}:{signal_id}",
                    {"priority": float(rule["priority"])},
                )
        for watchlist in self.db.get_signal_watchlists(signal_id):
            watchlist_node = self._node(
                "watchlist", watchlist["id"], watchlist["name"], timestamp
            )
            self._edge(
                rule_node, watchlist_node, "rule_matches_watchlist", timestamp,
                f"rule_watchlist:{rule_id}:{signal_id}:{watchlist['id']}",
                {"priority": float(rule["priority"])},
            )
        if publish:
            self._refresh_node_weights()
            self._publish(GraphUpdated("rule", 1, 0))

    def graph_view(
        self,
        *,
        period_days: int | None = None,
        node_type: str | None = None,
        edge_type: str | None = None,
        min_weight: float | None = None,
        min_occurrences: int = 1,
        watchlist_id: int | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        if node_type and node_type not in NODE_TYPES:
            raise ValueError("Invalid graph node type")
        if edge_type and edge_type not in EDGE_TYPES:
            raise ValueError("Invalid graph edge type")
        maximum = min(limit or self.config.graph_max_nodes, self.config.graph_max_nodes)
        node_threshold = self.config.graph_min_node_weight if min_weight is None else min_weight
        edge_threshold = self.config.graph_min_edge_weight if min_weight is None else min_weight
        nodes = [GraphNode.from_row(row) for row in self.db.get_graph_nodes(node_type, 0, None)]
        cutoff = datetime.now(timezone.utc) - timedelta(
            days=period_days or self.config.graph_default_period_days
        )
        nodes = [node for node in nodes if _timestamp(node.last_seen_at) >= cutoff]
        if search:
            needle = " ".join(search.casefold().split())
            nodes = [node for node in nodes if needle in node.normalized_label]
        edges = [
            self._effective_edge(GraphEdge.from_row(row))
            for row in self.db.get_graph_edges(
                edge_type,
                0,
                min_occurrences,
                limit=None,
            )
        ]
        edges = [
            edge for edge in edges
            if edge.weight >= edge_threshold and _timestamp(edge.last_seen_at) >= cutoff
        ]
        provisional_ids = {node.id for node in nodes}
        provisional_edges = [
            edge for edge in edges
            if edge.source_node_id in provisional_ids and edge.target_node_id in provisional_ids
        ]
        nodes = [self._effective_node(node, provisional_edges) for node in nodes]
        nodes = [node for node in nodes if node.weight >= node_threshold]
        if watchlist_id is not None:
            watchlist = self.db.get_graph_node("watchlist", str(watchlist_id))
            connected = {int(watchlist["id"])} if watchlist else set()
            if watchlist:
                for edge in edges:
                    if edge.source_node_id == int(watchlist["id"]):
                        connected.add(edge.target_node_id)
                    if edge.target_node_id == int(watchlist["id"]):
                        connected.add(edge.source_node_id)
            nodes = [node for node in nodes if node.id in connected]
        nodes.sort(
            key=lambda node: (node.weight, node.activity_score, node.last_seen_at),
            reverse=True,
        )
        nodes = nodes[:maximum]
        node_ids = {node.id for node in nodes}
        edges = [
            edge for edge in edges
            if edge.source_node_id in node_ids and edge.target_node_id in node_ids
        ]
        metrics = self.calculate_metrics(nodes, edges)
        node_payloads = [self._node_payload(node, edges) for node in nodes]
        for component_index, component in enumerate(
            _components(node_ids, edges), start=1
        ):
            for payload in node_payloads:
                if payload["id"] in component:
                    payload["connected_component"] = component_index
        return {
            "nodes": node_payloads,
            "edges": [self._edge_payload(edge) for edge in edges],
            "metrics": metrics.as_dict(),
        }

    def node_detail(self, node_type: str, entity_id: str) -> dict[str, Any] | None:
        canonical_id, _, _ = normalize_entity(node_type, entity_id)
        row = self.db.get_graph_node(node_type, canonical_id)
        if row is None:
            return None
        node = GraphNode.from_row(row)
        edges = [GraphEdge.from_row(item) for item in self.db.get_graph_edges(node_id=node.id, limit=None)]
        payload = self._node_payload(node, edges)
        payload["edges"] = [self._edge_payload(edge) for edge in edges]
        payload["connected_entities"] = [
            self._edge_payload(edge) for edge in edges[:25]
        ]
        payload["recent_events"] = self._related_events(node, edges)
        payload["related_signals"] = self._related_signals(node)
        payload["watchlists"] = self._related_watchlists(node)
        payload["triggered_rules"] = self._related_rules(node)
        payload["ai_analyses"] = self._related_ai_analyses(node)
        payload["outcomes"] = self._outcome_metrics(node)
        payload["history"] = self._node_history(node)
        return payload

    def summary(self, period_days: int | None = None) -> dict[str, Any]:
        view = self.graph_view(period_days=period_days, min_weight=0, limit=self.config.graph_max_nodes)
        nodes = view["nodes"]
        edges = view["edges"]
        narratives = sorted(
            (node for node in nodes if node["node_type"] == "narrative"),
            key=lambda item: (item["degree"], item["weighted_degree"]), reverse=True,
        )
        tokens = sorted(
            (node for node in nodes if node["node_type"] == "token"),
            key=lambda item: (item["connected_narrative_count"], item["degree"]), reverse=True,
        )
        return {
            "period_days": period_days or self.config.graph_default_period_days,
            **view["metrics"],
            "most_connected_narratives": narratives[:5],
            "most_connected_tokens": tokens[:5],
            "emerging_relationships": self.emerging(limit=5),
            "bridge_nodes": self.bridges(limit=5),
        }

    def top_nodes(self, node_type: str, limit: int = 10) -> list[dict[str, Any]]:
        view = self.graph_view(node_type=node_type, min_weight=0, limit=self.config.graph_max_nodes)
        return sorted(
            view["nodes"],
            key=lambda item: (item["weighted_degree"], item["activity_score"]),
            reverse=True,
        )[:limit]

    def emerging(self, limit: int = 25) -> list[dict[str, Any]]:
        results = []
        for row in self.db.get_graph_edges(min_weight=0, limit=None):
            edge = GraphEdge.from_row(row)
            metadata = edge.metadata
            decay = self.weights.recency_decay(edge.last_seen_at)
            score = self.weights.emerging_score(
                occurrence_count=edge.occurrence_count,
                source_count=int(metadata.get("source_count", 1)),
                event_count=int(metadata.get("event_count", 1)),
                hype_score=float(metadata.get("hype_score", 0)),
                momentum_score=float(metadata.get("momentum_score", 0)),
                last_seen_at=edge.last_seen_at,
                previous_occurrences=int(metadata.get("previous_occurrences", 0)),
            )
            classification = classify_relationship(
                score, decay, edge.occurrence_count,
                int(metadata.get("previous_occurrences", 0)),
            )
            results.append({
                **self._edge_payload(edge),
                "emerging_relationship_score": score,
                "classification": classification,
            })
        results.sort(
            key=lambda item: (item["emerging_relationship_score"], item["weight"]),
            reverse=True,
        )
        return results[:limit]

    def bridges(self, limit: int = 25) -> list[dict[str, Any]]:
        nodes = [GraphNode.from_row(row) for row in self.db.get_graph_nodes(limit=None)]
        edges = [GraphEdge.from_row(row) for row in self.db.get_graph_edges(min_weight=0, limit=None)]
        results = []
        for node in nodes:
            if node.node_type not in {"token", "narrative"}:
                continue
            metrics = self._node_metrics(node.id, nodes, edges)
            if metrics["bridge_score"] <= 0:
                continue
            results.append({**node.as_dict(), **metrics})
        results.sort(key=lambda item: (item["bridge_score"], item["weighted_degree"]), reverse=True)
        return results[:limit]

    def calculate_metrics(
        self,
        nodes: Iterable[GraphNode] | None = None,
        edges: Iterable[GraphEdge] | None = None,
    ) -> GraphMetrics:
        node_list = list(nodes) if nodes is not None else [
            GraphNode.from_row(row) for row in self.db.get_graph_nodes(limit=None)
        ]
        edge_list = list(edges) if edges is not None else [
            GraphEdge.from_row(row) for row in self.db.get_graph_edges(min_weight=0, limit=None)
        ]
        node_ids = {node.id for node in node_list}
        edge_list = [
            edge for edge in edge_list
            if edge.source_node_id in node_ids and edge.target_node_id in node_ids
        ]
        components = _components(node_ids, edge_list)
        possible = len(node_ids) * (len(node_ids) - 1)
        density = len(edge_list) / possible if possible else 0.0
        return GraphMetrics(
            node_count=len(node_list), edge_count=len(edge_list),
            density=round(density, 6), component_count=len(components),
            nodes_by_type=dict(Counter(node.node_type for node in node_list)),
            edges_by_type=dict(Counter(edge.edge_type for edge in edge_list)),
        )

    def create_snapshot(self, frequency: str) -> tuple[GraphSnapshot, bool]:
        now = datetime.now(timezone.utc)
        if frequency == "daily":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + timedelta(days=1)
        elif frequency == "weekly":
            start = (now - timedelta(days=now.weekday())).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            end = start + timedelta(days=7)
        elif frequency == "monthly":
            start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end = (
                start.replace(year=start.year + 1, month=1)
                if start.month == 12
                else start.replace(month=start.month + 1)
            )
        else:
            raise ValueError("Snapshot frequency must be daily, weekly, or monthly")
        metrics = self.calculate_metrics()
        payload = {**metrics.as_dict(), "frequency": frequency}
        snapshot_id, created = self.db.save_graph_snapshot(
            start.isoformat(), end.isoformat(), metrics.node_count,
            metrics.edge_count, json.dumps(payload, sort_keys=True),
        )
        row = next(
            item for item in self.db.get_graph_snapshots()
            if int(item["id"]) == snapshot_id
        )
        snapshot = GraphSnapshot.from_row(row)
        if created:
            for edge_row in self.db.get_graph_edges(min_weight=0, limit=None):
                edge = GraphEdge.from_row(edge_row)
                metadata = {**edge.metadata, "previous_occurrences": edge.occurrence_count}
                self.db.update_graph_edge(
                    edge.id,
                    weight=edge.weight,
                    confidence=edge.confidence,
                    last_seen_at=edge.last_seen_at,
                    metadata_json=json.dumps(metadata, sort_keys=True),
                )
            self._publish(GraphSnapshotCreated(snapshot.id, frequency))
        return snapshot, created

    def snapshots(self, limit: int = 100) -> list[dict[str, Any]]:
        return [GraphSnapshot.from_row(row).as_dict() for row in self.db.get_graph_snapshots(limit)]

    def remove_orphaned_nodes(self) -> int:
        removed = self.db.delete_orphan_graph_nodes()
        logger.info("Graph orphan cleanup complete removed_node_count=%s", removed)
        return removed

    def validate(self) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        nodes = self.db.connection.execute("SELECT * FROM graph_nodes").fetchall()
        edges = self.db.connection.execute("SELECT * FROM graph_edges").fetchall()
        node_ids = {int(row["id"]) for row in nodes}
        for node in nodes:
            if node["node_type"] not in NODE_TYPES:
                issues.append({"type": "invalid_node_type", "id": node["id"]})
            if not 0 <= float(node["weight"]) <= 1:
                issues.append({"type": "invalid_node_weight", "id": node["id"]})
            if not self._entity_exists(str(node["node_type"]), str(node["entity_id"])):
                issues.append({"type": "orphaned_entity_reference", "id": node["id"]})
        seen_edges = set()
        seen_undirected = set()
        for edge in edges:
            if int(edge["source_node_id"]) not in node_ids or int(edge["target_node_id"]) not in node_ids:
                issues.append({"type": "missing_node", "id": edge["id"]})
            if edge["edge_type"] not in EDGE_TYPES:
                issues.append({"type": "invalid_edge_type", "id": edge["id"]})
            if not 0 <= float(edge["weight"]) <= 1:
                issues.append({"type": "invalid_edge_weight", "id": edge["id"]})
            if not 0 <= float(edge["confidence"]) <= 1:
                issues.append({"type": "invalid_confidence", "id": edge["id"]})
            identity = (
                int(edge["source_node_id"]), int(edge["target_node_id"]),
                edge["edge_type"], edge["derivation"],
            )
            if identity in seen_edges:
                issues.append({"type": "duplicate_deterministic_edge", "id": edge["id"]})
            seen_edges.add(identity)
            if edge["edge_type"] in UNDIRECTED_EDGE_TYPES:
                pair = (
                    min(int(edge["source_node_id"]), int(edge["target_node_id"])),
                    max(int(edge["source_node_id"]), int(edge["target_node_id"])),
                    edge["edge_type"], edge["derivation"],
                )
                if pair in seen_undirected:
                    issues.append({"type": "reversed_duplicate", "id": edge["id"]})
                seen_undirected.add(pair)
        for row in self.db.get_graph_snapshots(100000):
            if _timestamp(row["period_start"]) >= _timestamp(row["period_end"]):
                issues.append({"type": "snapshot_date_inconsistency", "id": row["id"]})
        return issues

    def format_summary(self, period_days: int | None = None) -> str:
        report = self.summary(period_days)
        node_types = report["nodes_by_type"]
        narratives = "\n".join(
            f"{index}. {item['label']} - {item['degree']} connections"
            for index, item in enumerate(report["most_connected_narratives"], 1)
        ) or "None"
        tokens = "\n".join(
            f"{index}. {item['label']} - {item['connected_narrative_count']} narratives"
            for index, item in enumerate(report["most_connected_tokens"], 1)
        ) or "None"
        emerging = "\n".join(
            f"- {item['source_label']} <-> {item['target_label']} ({item['classification']})"
            for item in report["emerging_relationships"]
        ) or "None"
        bridges = "\n".join(f"- {item['label']}" for item in report["bridge_nodes"]) or "None"
        return (
            "Narrative Graph Summary\n\n"
            f"Period: Last {report['period_days']} days\n\n"
            f"Nodes: {report['node_count']}\nEdges: {report['edge_count']}\n"
            f"Narratives: {node_types.get('narrative', 0)}\n"
            f"Tokens: {node_types.get('token', 0)}\n"
            f"Unified events: {node_types.get('unified_event', 0)}\n"
            f"Sources: {node_types.get('source', 0)}\n\n"
            f"Most connected narratives:\n{narratives}\n\n"
            f"Most connected tokens:\n{tokens}\n\n"
            f"Emerging relationships:\n{emerging}\n\nBridge nodes:\n{bridges}"
        )

    def _node(
        self, node_type: str, entity_id: object, label: object, timestamp: str,
        *, activity: float = 0, metadata: dict[str, Any] | None = None,
        first_seen: str | None = None,
    ) -> int:
        canonical_id, canonical_label, normalized = normalize_entity(node_type, entity_id)
        display = canonical_label if node_type == "token" else " ".join(str(label).split())
        return self.db.upsert_graph_node(
            node_type=node_type, entity_id=canonical_id, label=display,
            normalized_label=normalized, weight=0, activity_score=max(0, min(100, activity)),
            first_seen_at=first_seen or timestamp, last_seen_at=timestamp,
            metadata_json=json.dumps(metadata or {}, sort_keys=True),
        )

    def _edge(
        self,
        source_node_id: int,
        target_node_id: int,
        edge_type: str,
        timestamp: str,
        evidence_key: str,
        metrics: dict[str, Any],
        *,
        derivation: str = "observed",
        confidence: float = 1.0,
    ) -> int:
        if edge_type not in EDGE_TYPES or derivation not in DERIVATIONS:
            raise ValueError("Invalid graph edge")
        if edge_type in UNDIRECTED_EDGE_TYPES and source_node_id > target_node_id:
            source_node_id, target_node_id = target_node_id, source_node_id
        existing = self.db.get_graph_edge(
            source_node_id, target_node_id, edge_type, derivation
        )
        metadata = _json_object(existing["metadata_json"]) if existing else {}
        evidence = list(metadata.get("evidence", []))
        is_new = evidence_key not in evidence
        if is_new:
            evidence.append(evidence_key)
        for key, value in metrics.items():
            if key in {"source_count", "event_count"} and existing and is_new:
                metadata[key] = int(metadata.get(key, 0)) + int(value)
            elif key in {"hype_score", "momentum_score", "confidence", "priority"}:
                metadata[key] = max(float(metadata.get(key, 0)), float(value))
            else:
                metadata[key] = value
        metadata["evidence"] = evidence[-100:]
        occurrence_count = int(existing["occurrence_count"]) + int(is_new) if existing else 1
        weight = self.weights.edge_weight(
            occurrence_count=occurrence_count, last_seen_at=timestamp,
            source_count=int(metadata.get("source_count", 1)),
            event_count=int(metadata.get("event_count", 1)),
            hype_score=float(metadata.get("hype_score", 0)),
            momentum_score=float(metadata.get("momentum_score", 0)),
            confidence=float(metadata.get("confidence", 0)),
            outcome_success_rate=float(metadata.get("outcome_success_rate", 0)),
            priority=float(metadata.get("priority", 0)),
            ai_confidence=metadata.get("ai_confidence"),
        )
        if existing and not is_new:
            self.db.update_graph_edge(
                int(existing["id"]), weight=weight, confidence=confidence,
                last_seen_at=timestamp, metadata_json=json.dumps(metadata, sort_keys=True),
            )
            return int(existing["id"])
        edge_id = self.db.upsert_graph_edge(
            source_node_id=source_node_id, target_node_id=target_node_id,
            edge_type=edge_type, derivation=derivation, weight=weight,
            occurrence_increment=1, confidence=confidence,
            first_seen_at=timestamp, last_seen_at=timestamp,
            metadata_json=json.dumps(metadata, sort_keys=True),
        )
        logger.info(
            "Graph edge updated graph_edge_id=%s edge_type=%s source_entity_id=%s "
            "target_entity_id=%s occurrence_count=%s weight=%.4f update_reason=%s",
            edge_id, edge_type, source_node_id, target_node_id,
            occurrence_count, weight, evidence_key,
        )
        if self.event_bus is not None:
            score = self.weights.emerging_score(
                occurrence_count=occurrence_count,
                source_count=int(metrics.get("source_count", 1)),
                event_count=int(metrics.get("event_count", 1)),
                hype_score=float(metrics.get("hype_score", 0)),
                momentum_score=float(metrics.get("momentum_score", 0)),
                last_seen_at=timestamp,
            )
            if score >= 75:
                self._publish(EmergingRelationshipDetected(edge_id, score))
        return edge_id

    def _refresh_node_weights(self) -> None:
        nodes = [GraphNode.from_row(row) for row in self.db.get_graph_nodes(limit=None)]
        edges = [GraphEdge.from_row(row) for row in self.db.get_graph_edges(min_weight=0, limit=None)]
        for node in nodes:
            connected = [
                edge for edge in edges
                if edge.source_node_id == node.id or edge.target_node_id == node.id
            ]
            weighted_degree = sum(edge.weight for edge in connected)
            weight = self.weights.node_weight(
                weighted_degree=weighted_degree,
                activity_score=node.activity_score,
                last_seen_at=node.last_seen_at,
            )
            graph_metrics = self._node_metrics(node.id, nodes, edges)
            self.db.upsert_graph_node(
                node_type=node.node_type, entity_id=node.entity_id, label=node.label,
                normalized_label=node.normalized_label, weight=weight,
                activity_score=node.activity_score, first_seen_at=node.first_seen_at,
                last_seen_at=node.last_seen_at,
                metadata_json=json.dumps(
                    {**node.metadata, **graph_metrics}, sort_keys=True
                ),
            )

    def _effective_edge(self, edge: GraphEdge) -> GraphEdge:
        metrics = edge.metadata
        return replace(
            edge,
            weight=self.weights.edge_weight(
                occurrence_count=edge.occurrence_count,
                last_seen_at=edge.last_seen_at,
                source_count=int(metrics.get("source_count", 1)),
                event_count=int(metrics.get("event_count", 1)),
                hype_score=float(metrics.get("hype_score", 0)),
                momentum_score=float(metrics.get("momentum_score", 0)),
                confidence=float(metrics.get("confidence", edge.confidence)),
                outcome_success_rate=float(metrics.get("outcome_success_rate", 0)),
                priority=float(metrics.get("priority", 0)),
                ai_confidence=(
                    float(metrics["ai_confidence"])
                    if edge.derivation == "ai" and "ai_confidence" in metrics
                    else None
                ),
            ),
        )

    def _effective_node(self, node: GraphNode, edges: list[GraphEdge]) -> GraphNode:
        weighted_degree = sum(
            edge.weight for edge in edges
            if node.id in {edge.source_node_id, edge.target_node_id}
        )
        return replace(
            node,
            weight=self.weights.node_weight(
                weighted_degree=weighted_degree,
                activity_score=node.activity_score,
                last_seen_at=node.last_seen_at,
            ),
        )

    def _node_payload(self, node: GraphNode, edges: list[GraphEdge]) -> dict[str, Any]:
        nodes = [GraphNode.from_row(row) for row in self.db.get_graph_nodes(limit=None)]
        return {**node.as_dict(), **self._node_metrics(node.id, nodes, edges)}

    def _node_metrics(
        self, node_id: int, nodes: list[GraphNode], edges: list[GraphEdge],
    ) -> dict[str, Any]:
        connected = [
            edge for edge in edges
            if edge.source_node_id == node_id or edge.target_node_id == node_id
        ]
        in_edges = [edge for edge in connected if edge.target_node_id == node_id]
        out_edges = [edge for edge in connected if edge.source_node_id == node_id]
        node_by_id = {node.id: node for node in nodes}
        neighbor_ids = {
            edge.target_node_id if edge.source_node_id == node_id else edge.source_node_id
            for edge in connected
        }
        neighbors = [node_by_id[item] for item in neighbor_ids if item in node_by_id]
        narrative_count = sum(node.node_type == "narrative" for node in neighbors)
        token_count = sum(node.node_type == "token" for node in neighbors)
        source_count = sum(node.node_type == "source" for node in neighbors)
        event_count = sum(node.node_type == "unified_event" for node in neighbors)
        clusters = self._neighbor_clusters(node_id, neighbor_ids, edges)
        bridge_score = min(
            100.0,
            max(0, len(clusters) - 1) * 25
            + min(30, (narrative_count + token_count) * 5)
            + min(20, source_count * 4),
        )
        return {
            "degree": len(connected),
            "weighted_degree": round(sum(edge.weight for edge in connected), 4),
            "in_degree": len(in_edges),
            "out_degree": len(out_edges),
            "token_centrality": round(token_count / max(1, len(nodes) - 1) * 100, 2),
            "narrative_centrality": round(narrative_count / max(1, len(nodes) - 1) * 100, 2),
            "source_diversity": source_count,
            "event_diversity": event_count,
            "bridge_score": round(bridge_score, 2),
            "connected_clusters": len(clusters),
            "supporting_event_count": event_count,
            "connected_narrative_count": narrative_count,
            "connected_token_count": token_count,
            "emerging_relationship_score": max(
                (self._edge_emerging_score(edge) for edge in connected), default=0.0
            ),
        }

    @staticmethod
    def _neighbor_clusters(node_id: int, neighbor_ids: set[int], edges: list[GraphEdge]) -> list[set[int]]:
        reduced = [
            edge for edge in edges
            if node_id not in {edge.source_node_id, edge.target_node_id}
        ]
        components = _components(neighbor_ids, reduced)
        return [component for component in components if component & neighbor_ids]

    def _edge_emerging_score(self, edge: GraphEdge) -> float:
        metadata = edge.metadata
        return self.weights.emerging_score(
            occurrence_count=edge.occurrence_count,
            source_count=int(metadata.get("source_count", 1)),
            event_count=int(metadata.get("event_count", 1)),
            hype_score=float(metadata.get("hype_score", 0)),
            momentum_score=float(metadata.get("momentum_score", 0)),
            last_seen_at=edge.last_seen_at,
            previous_occurrences=int(metadata.get("previous_occurrences", 0)),
        )

    def _edge_payload(self, edge: GraphEdge) -> dict[str, Any]:
        return {
            **edge.as_dict(),
            "emerging_relationship_score": self._edge_emerging_score(edge),
        }

    def _rebuild_rule_edges(self) -> None:
        for match in self.db.get_rule_matches():
            self.update_rule_match(int(match["rule_id"]), int(match["signal_id"]), publish=False)

    def _rebuild_ai_edges(self) -> None:
        for row in self.db.get_signal_ai_analyses(limit=100000):
            self.update_ai_analysis(int(row["signal_id"]), publish=False)

    def _related_events(self, node: GraphNode, edges: list[GraphEdge]) -> list[dict[str, Any]]:
        event_ids = set()
        for edge in edges:
            other_id = edge.target_node_id if edge.source_node_id == node.id else edge.source_node_id
            other = self.db.get_graph_node_by_id(other_id)
            if other and other["node_type"] == "unified_event":
                event_ids.add(int(other["entity_id"]))
        return [
            dict(event) for event_id in list(event_ids)[:10]
            if (event := self.db.get_unified_event(event_id)) is not None
        ]

    def _related_signals(self, node: GraphNode) -> list[dict[str, Any]]:
        if node.node_type not in {"token", "narrative"}:
            return []
        return [
            dict(row) for row in self.db.get_signals(limit=20)
            if str(row[node.node_type] or "").casefold() == node.label.casefold()
        ]

    def _related_watchlists(self, node: GraphNode) -> list[dict[str, Any]]:
        if node.node_type not in {"token", "narrative"}:
            return []
        return [
            dict(row) for row in self.db.connection.execute(
                """
                SELECT DISTINCT watchlist.* FROM watchlists AS watchlist
                JOIN watchlist_items AS item ON item.watchlist_id = watchlist.id
                WHERE item.item_type = ? AND item.normalized_value = ? COLLATE NOCASE
                """,
                (node.node_type, node.normalized_label),
            ).fetchall()
        ]

    def _related_rules(self, node: GraphNode) -> list[dict[str, Any]]:
        signal_ids = [row["id"] for row in self._related_signals(node)]
        if not signal_ids:
            return []
        placeholders = ",".join("?" for _ in signal_ids)
        return [
            dict(row) for row in self.db.connection.execute(
                f"""
                SELECT DISTINCT rule.* FROM alert_rules AS rule
                JOIN signal_rule_matches AS match ON match.rule_id = rule.id
                WHERE match.signal_id IN ({placeholders})
                """,
                signal_ids,
            ).fetchall()
        ]

    def _related_ai_analyses(self, node: GraphNode) -> list[dict[str, Any]]:
        signal_ids = [int(row["id"]) for row in self._related_signals(node)]
        if not signal_ids:
            return []
        placeholders = ",".join("?" for _ in signal_ids)
        return [
            dict(row) for row in self.db.connection.execute(
                f"""
                SELECT analysis.* FROM signal_ai_analyses AS analysis
                WHERE analysis.signal_id IN ({placeholders})
                ORDER BY analysis.created_at DESC, analysis.id DESC LIMIT 20
                """,
                signal_ids,
            ).fetchall()
        ]

    def _outcome_metrics(self, node: GraphNode) -> dict[str, Any]:
        signals = self._related_signals(node)
        signal_ids = {int(row["id"]) for row in signals}
        outcomes = [
            row for row in self.db.get_signal_outcomes(limit=None)
            if int(row["signal_id"]) in signal_ids
        ]
        successful = sum(row["status"] == "SUCCESS" for row in outcomes)
        return {
            "evaluated_event_count": len(outcomes),
            "successful_outcome_count": successful,
            "success_rate": round(successful / len(outcomes) * 100, 2) if outcomes else None,
            "average_outcome_score": round(
                sum(float(row["hype_change"]) + float(row["momentum_change"]) for row in outcomes)
                / len(outcomes), 2,
            ) if outcomes else None,
        }

    def _node_history(self, node: GraphNode) -> list[dict[str, Any]]:
        if node.node_type == "narrative":
            return [
                dict(row) for row in self.db.connection.execute(
                    """
                    SELECT recorded_at AS timestamp, hype_score AS activity_score,
                           mentions_count
                    FROM narrative_score_history WHERE narrative = ? COLLATE NOCASE
                    ORDER BY recorded_at DESC LIMIT 30
                    """,
                    (node.label,),
                ).fetchall()
            ]
        return []

    def _entity_exists(self, node_type: str, entity_id: str) -> bool:
        if node_type in {"token", "narrative"}:
            return True
        table_map = {
            "unified_event": "unified_events",
            "source": "content_sources",
            "watchlist": "watchlists",
            "rule": "alert_rules",
        }
        table = table_map.get(node_type)
        if not table:
            return False
        return self.db.connection.execute(
            f"SELECT 1 FROM {table} WHERE id = ?", (entity_id,)
        ).fetchone() is not None

    def _publish(self, event: object) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(event)


def _json_strings(value: object) -> list[str]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return list(dict.fromkeys(str(item) for item in parsed if str(item).strip()))


def _json_object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _timestamp(value: object) -> datetime:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def _components(node_ids: set[int], edges: Iterable[GraphEdge]) -> list[set[int]]:
    adjacency: dict[int, set[int]] = {node_id: set() for node_id in node_ids}
    for edge in edges:
        if edge.source_node_id in adjacency and edge.target_node_id in adjacency:
            adjacency[edge.source_node_id].add(edge.target_node_id)
            adjacency[edge.target_node_id].add(edge.source_node_id)
    components = []
    remaining = set(node_ids)
    while remaining:
        start = min(remaining)
        component = set()
        queue = deque([start])
        while queue:
            current = queue.popleft()
            if current in component:
                continue
            component.add(current)
            queue.extend(adjacency.get(current, set()) - component)
        components.append(component)
        remaining -= component
    return components
