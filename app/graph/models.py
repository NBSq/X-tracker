from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


NODE_TYPES = frozenset(
    {"narrative", "token", "unified_event", "source", "watchlist", "rule"}
)
EDGE_TYPES = frozenset(
    {
        "narrative_mentions_token",
        "narrative_related_to_narrative",
        "event_contains_narrative",
        "event_mentions_token",
        "source_reports_event",
        "watchlist_tracks_token",
        "watchlist_tracks_narrative",
        "rule_triggered_by_event",
        "rule_matches_watchlist",
        "token_co_occurs_with_token",
    }
)
UNDIRECTED_EDGE_TYPES = frozenset(
    {"narrative_related_to_narrative", "token_co_occurs_with_token"}
)
DERIVATIONS = frozenset({"observed", "ai"})

TOKEN_ALIASES = {
    "bitcoin": "BTC",
    "bitcoin token symbol": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "binance coin": "BNB",
    "ripple": "XRP",
    "dogecoin": "DOGE",
}


def normalize_entity(node_type: str, value: object) -> tuple[str, str, str]:
    if node_type not in NODE_TYPES:
        raise ValueError(f"Unsupported graph node type: {node_type}")
    label = " ".join(str(value or "").strip().split())
    if not label:
        raise ValueError("Graph entity label cannot be empty")
    if node_type == "token":
        candidate = label.lstrip("$").casefold()
        canonical = TOKEN_ALIASES.get(candidate, candidate.upper())
        canonical = re.sub(r"[^A-Z0-9._-]", "", canonical)
        if not canonical:
            raise ValueError("Token symbol cannot be normalized")
        return canonical, canonical, canonical.casefold()
    if node_type in {"unified_event", "source", "watchlist", "rule"}:
        entity_id = label
        return entity_id, label, label.casefold()
    normalized = label.casefold()
    return normalized, label, normalized


@dataclass(frozen=True)
class GraphNode:
    id: int
    node_type: str
    entity_id: str
    label: str
    normalized_label: str
    weight: float
    activity_score: float
    first_seen_at: str
    last_seen_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "GraphNode":
        return cls(
            id=int(row["id"]),
            node_type=str(row["node_type"]),
            entity_id=str(row["entity_id"]),
            label=str(row["label"]),
            normalized_label=str(row["normalized_label"]),
            weight=float(row["weight"]),
            activity_score=float(row["activity_score"]),
            first_seen_at=str(row["first_seen_at"]),
            last_seen_at=str(row["last_seen_at"]),
            metadata=_json_object(row["metadata_json"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GraphEdge:
    id: int
    source_node_id: int
    target_node_id: int
    edge_type: str
    derivation: str
    weight: float
    occurrence_count: int
    confidence: float
    first_seen_at: str
    last_seen_at: str
    metadata: dict[str, Any] = field(default_factory=dict)
    source_label: str | None = None
    target_label: str | None = None
    source_type: str | None = None
    target_type: str | None = None

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "GraphEdge":
        keys = set(row.keys())
        return cls(
            id=int(row["id"]),
            source_node_id=int(row["source_node_id"]),
            target_node_id=int(row["target_node_id"]),
            edge_type=str(row["edge_type"]),
            derivation=str(row["derivation"]),
            weight=float(row["weight"]),
            occurrence_count=int(row["occurrence_count"]),
            confidence=float(row["confidence"]),
            first_seen_at=str(row["first_seen_at"]),
            last_seen_at=str(row["last_seen_at"]),
            metadata=_json_object(row["metadata_json"]),
            source_label=str(row["source_label"]) if "source_label" in keys else None,
            target_label=str(row["target_label"]) if "target_label" in keys else None,
            source_type=str(row["source_type"]) if "source_type" in keys else None,
            target_type=str(row["target_type"]) if "target_type" in keys else None,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GraphSnapshot:
    id: int
    period_start: str
    period_end: str
    node_count: int
    edge_count: int
    metrics: dict[str, Any]
    created_at: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "GraphSnapshot":
        return cls(
            id=int(row["id"]),
            period_start=str(row["period_start"]),
            period_end=str(row["period_end"]),
            node_count=int(row["node_count"]),
            edge_count=int(row["edge_count"]),
            metrics=_json_object(row["metrics_json"]),
            created_at=str(row["created_at"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GraphMetrics:
    node_count: int
    edge_count: int
    density: float
    component_count: int
    nodes_by_type: dict[str, int]
    edges_by_type: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _json_object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
