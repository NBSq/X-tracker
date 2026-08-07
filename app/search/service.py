from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from datetime import date, datetime, timezone
from typing import Any

from app.config import Config
from app.db.database import Database
from app.events import EventBus, SavedSearchExecuted
from app.graph.service import GraphService
from app.observability.metrics import metrics
from app.search.models import SavedSearch, SearchResult, SearchValidationError


logger = logging.getLogger("x_narrative_tracker.search")
TARGETS = frozenset({
    "signals", "unified_events", "narratives", "tokens",
    "graph_relationships", "quality_signals",
})
COMMON_SCORE_FILTERS = frozenset({
    "hype_min", "hype_max", "momentum_min", "momentum_max",
    "confidence_min", "confidence_max", "date_from", "date_to",
})
TARGET_FILTERS = {
    "signals": COMMON_SCORE_FILTERS | {
        "token", "narrative", "watchlist", "rule", "source",
        "source_count_min", "item_count_min", "quality_min", "quality_max",
        "quality_classification", "minimum_evidence_count", "ai_provider",
        "ai_action", "ai_risk_level", "outcome",
    },
    "quality_signals": COMMON_SCORE_FILTERS | {
        "token", "narrative", "watchlist", "rule", "source",
        "source_count_min", "item_count_min", "quality_min", "quality_max",
        "quality_classification", "minimum_evidence_count", "ai_provider",
        "ai_action", "ai_risk_level", "outcome",
    },
    "unified_events": COMMON_SCORE_FILTERS | {
        "token", "narrative", "source", "source_count_min", "item_count_min",
        "conflict_status",
    },
    "narratives": COMMON_SCORE_FILTERS | {"narrative"},
    "tokens": COMMON_SCORE_FILTERS | {"token"},
    "graph_relationships": {
        "token", "narrative", "date_from", "date_to", "graph_emerging_status",
        "emerging_score_min", "bridge_score_min",
    },
}
TARGET_SORTS = {
    "signals": frozenset({
        "created_at", "hype_score", "momentum_score", "confidence", "mentions",
        "quality_score", "source_count", "evidence_count",
    }),
    "quality_signals": frozenset({
        "created_at", "hype_score", "momentum_score", "confidence", "mentions",
        "quality_score", "source_count", "evidence_count",
    }),
    "unified_events": frozenset({
        "created_at", "hype_score", "momentum_score", "confidence",
        "source_count", "item_count",
    }),
    "narratives": frozenset({
        "name", "created_at", "mentions", "hype_score", "momentum_score", "confidence",
    }),
    "tokens": frozenset({
        "name", "created_at", "mentions", "hype_score", "momentum_score", "confidence",
    }),
    "graph_relationships": frozenset({
        "emerging_score", "bridge_score", "weight", "occurrences", "created_at",
    }),
}
DEFAULT_SORT = {
    "signals": "created_at", "quality_signals": "quality_score",
    "unified_events": "created_at", "narratives": "hype_score",
    "tokens": "hype_score", "graph_relationships": "emerging_score",
}
SCORE_FILTERS = frozenset({
    "hype_min", "hype_max", "momentum_min", "momentum_max", "quality_min",
    "quality_max", "emerging_score_min", "bridge_score_min",
})
COUNT_FILTERS = frozenset({"source_count_min", "item_count_min", "minimum_evidence_count"})


class SearchService:
    def __init__(
        self, db: Database, config: Config, event_bus: EventBus | None = None,
        clock=lambda: datetime.now(timezone.utc),
    ) -> None:
        self.db = db
        self.config = config
        self.event_bus = event_bus
        self.clock = clock

    def list(self, enabled: bool | None = None) -> list[SavedSearch]:
        return [SavedSearch.from_row(row) for row in self.db.get_saved_searches(enabled)]

    def get(self, search_id: int) -> SavedSearch | None:
        row = self.db.get_saved_search(search_id)
        return SavedSearch.from_row(row) if row else None

    def create(self, payload: dict[str, Any]) -> SavedSearch:
        values = self.validate_definition(payload)
        try:
            search_id = self.db.create_saved_search(values)
        except sqlite3.IntegrityError as exc:
            raise SearchValidationError("A saved search with this name already exists") from exc
        return self._required(search_id)

    def update(self, search_id: int, payload: dict[str, Any]) -> SavedSearch:
        current = self._required(search_id)
        merged = {**current.as_dict(), **payload}
        values = self.validate_definition(merged)
        try:
            if not self.db.update_saved_search(search_id, values):
                raise KeyError(search_id)
        except sqlite3.IntegrityError as exc:
            raise SearchValidationError("A saved search with this name already exists") from exc
        return self._required(search_id)

    def delete(self, search_id: int) -> None:
        if not self.db.delete_saved_search(search_id):
            raise KeyError(search_id)

    def set_enabled(self, search_id: int, enabled: bool) -> SavedSearch:
        self._required(search_id)
        self.db.update_saved_search(search_id, {"enabled": enabled})
        return self._required(search_id)

    def duplicate(self, search_id: int, name: str | None = None) -> SavedSearch:
        current = self._required(search_id)
        payload = current.as_dict()
        payload["name"] = name or f"{current.name} copy"
        payload.pop("id", None)
        return self.create(payload)

    def preview(self, search_id: int, limit: int | None = None) -> SearchResult:
        return self._execute(self._required(search_id), limit=limit, record=False)

    def run(self, search_id: int, limit: int | None = None) -> SearchResult:
        search = self._required(search_id)
        if not search.enabled:
            raise SearchValidationError("Saved search is disabled")
        return self._execute(search, limit=limit, record=True)

    def validate_definition(self, payload: dict[str, Any]) -> dict[str, Any]:
        name = " ".join(str(payload.get("name", "")).split())
        if not name or len(name) > 120:
            raise SearchValidationError("name must contain 1 to 120 characters")
        target = str(payload.get("target_type", "")).strip()
        if target not in TARGETS:
            raise SearchValidationError("Unsupported saved search target type")
        filters = self.normalize_filters(target, payload.get("filters", {}))
        sort_by = str(payload.get("sort_by") or DEFAULT_SORT[target]).strip()
        if sort_by not in TARGET_SORTS[target]:
            raise SearchValidationError(f"Unsupported sort field for {target}: {sort_by}")
        direction = str(payload.get("sort_direction", "desc")).lower().strip()
        if direction not in {"asc", "desc"}:
            raise SearchValidationError("sort_direction must be asc or desc")
        try:
            result_limit = int(payload.get("result_limit", self.config.report_max_results))
        except (TypeError, ValueError) as exc:
            raise SearchValidationError("result_limit must be an integer") from exc
        if not 1 <= result_limit <= self.config.report_max_results:
            raise SearchValidationError(
                f"result_limit must be between 1 and {self.config.report_max_results}"
            )
        return {
            "name": name, "description": str(payload.get("description", "")).strip()[:1000],
            "enabled": bool(payload.get("enabled", True)), "target_type": target,
            "filters_json": json.dumps(filters, sort_keys=True, ensure_ascii=False),
            "sort_by": sort_by, "sort_direction": direction, "result_limit": result_limit,
        }

    def normalize_filters(self, target: str, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise SearchValidationError("filters must be an object")
        unsupported = set(raw) - TARGET_FILTERS[target]
        if unsupported:
            raise SearchValidationError(f"Unsupported filter: {sorted(unsupported)[0]}")
        result: dict[str, Any] = {}
        for key, value in raw.items():
            if value is None or value == "":
                continue
            if key in SCORE_FILTERS:
                result[key] = self._number(key, value, 0, 100)
            elif key in {"confidence_min", "confidence_max"}:
                result[key] = self._number(key, value, 0, 10)
            elif key in COUNT_FILTERS:
                result[key] = int(self._number(key, value, 0, 1_000_000))
            elif key in {"date_from", "date_to"}:
                try:
                    result[key] = date.fromisoformat(str(value)).isoformat()
                except ValueError as exc:
                    raise SearchValidationError(f"{key} must use YYYY-MM-DD") from exc
            elif key in {"watchlist", "rule", "source"} and isinstance(value, list):
                normalized_values = list(dict.fromkeys(
                    " ".join(str(item).split()) for item in value
                    if " ".join(str(item).split())
                ))
                if not normalized_values or len(normalized_values) > 20:
                    raise SearchValidationError(f"{key} must contain 1 to 20 values")
                if any(len(item) > 200 for item in normalized_values):
                    raise SearchValidationError(f"Invalid {key} filter")
                result[key] = normalized_values
            else:
                normalized = " ".join(str(value).split())
                if not normalized or len(normalized) > 200:
                    raise SearchValidationError(f"Invalid {key} filter")
                result[key] = normalized
        self._validate_ranges(result)
        enums = {
            "outcome": {"SUCCESS", "NEUTRAL", "FAILED"},
            "quality_classification": {
                "excellent", "strong", "moderate", "weak", "unreliable", "insufficient_data",
            },
            "ai_action": {"watch", "ignore", "research"},
            "ai_risk_level": {"low", "medium", "high"},
            "conflict_status": {"has_conflicts", "requires_review", "clear"},
            "graph_emerging_status": {"emerging", "growing", "stable", "declining", "inactive", "new"},
        }
        for key, choices in enums.items():
            if key in result and result[key] not in choices:
                raise SearchValidationError(f"Invalid {key} filter")
        return result

    def _execute(self, search: SavedSearch, *, limit: int | None, record: bool) -> SearchResult:
        started = time.perf_counter()
        effective_limit = min(limit or search.result_limit, search.result_limit, self.config.report_max_results)
        try:
            rows, total = self._target_rows(search, effective_limit)
            now = self.clock().isoformat()
            payloads = tuple(self._safe_row(row) for row in rows)
            result = SearchResult(
                search, payloads, total,
                self._summary(payloads, total, search.target_type), now,
            )
            if record:
                self.db.record_saved_search_run(search.id, now)
                refreshed = self._required(search.id)
                result = SearchResult(refreshed, payloads, total, result.summary, now)
                self._publish(SavedSearchExecuted(search.id, search.target_type, len(rows), total, now))
            if record:
                metrics.increment("saved_search_runs_total")
            metrics.record_success("saved_search")
            logger.info(
                "Saved search executed search_id=%s target_type=%s result_count=%s total_matches=%s duration_ms=%s",
                search.id, search.target_type, len(rows), total,
                int((time.perf_counter() - started) * 1000),
                extra={"event": "saved_search_executed", "component": "saved_search"},
            )
            return result
        except Exception as exc:
            metrics.increment("saved_search_failures_total")
            metrics.record_error("saved_search", type(exc).__name__)
            raise

    def _target_rows(self, search: SavedSearch, limit: int):
        if search.target_type in {"signals", "quality_signals"}:
            return self.db.search_signal_records(
                search.filters, search.sort_by, search.sort_direction, limit,
                quality_only=search.target_type == "quality_signals",
            )
        if search.target_type == "unified_events":
            return self.db.search_unified_event_records(
                search.filters, search.sort_by, search.sort_direction, limit,
            )
        if search.target_type in {"narratives", "tokens"}:
            return self.db.search_entity_records(
                search.target_type, search.filters, search.sort_by,
                search.sort_direction, limit,
            )
        return self._graph_rows(search, limit)

    def _graph_rows(self, search: SavedSearch, limit: int):
        graph = GraphService(self.db, self.config)
        rows = graph.emerging(limit=self.config.graph_max_nodes)
        bridges = {
            (item["node_type"], str(item["entity_id"])): float(item["bridge_score"])
            for item in graph.bridges(limit=self.config.graph_max_nodes)
        }
        filters = search.filters
        result = []
        for row in rows:
            item = dict(row)
            item["bridge_score"] = max(
                bridges.get((str(item.get("source_type")), str(item.get("source_entity_id"))), 0),
                bridges.get((str(item.get("target_type")), str(item.get("target_entity_id"))), 0),
            )
            labels = {str(item.get("source_label", "")).casefold(), str(item.get("target_label", "")).casefold()}
            if "token" in filters and filters["token"].casefold() not in labels:
                continue
            if "narrative" in filters and filters["narrative"].casefold() not in labels:
                continue
            if "graph_emerging_status" in filters and item.get("classification") != filters["graph_emerging_status"]:
                continue
            if float(item.get("emerging_relationship_score", 0)) < float(filters.get("emerging_score_min", 0)):
                continue
            if item["bridge_score"] < float(filters.get("bridge_score_min", 0)):
                continue
            if "date_from" in filters and str(item.get("last_seen_at", ""))[:10] < filters["date_from"]:
                continue
            if "date_to" in filters and str(item.get("first_seen_at", ""))[:10] > filters["date_to"]:
                continue
            result.append(item)
        sort_keys = {
            "emerging_score": "emerging_relationship_score", "bridge_score": "bridge_score",
            "weight": "weight", "occurrences": "occurrence_count", "created_at": "last_seen_at",
        }
        result.sort(key=lambda item: item.get(sort_keys[search.sort_by]) or 0,
                    reverse=search.sort_direction == "desc")
        return result[:limit], len(result)

    @staticmethod
    def _summary(
        rows: tuple[dict[str, Any], ...], total: int, target_type: str,
    ) -> dict[str, Any]:
        def average(*keys: str):
            values = [float(row[key]) for row in rows for key in keys if row.get(key) is not None]
            return round(sum(values) / len(values), 2) if values else None
        outcomes = [str(row.get("outcome_status")) for row in rows if row.get("outcome_status")]
        tokens = [str(row["token"]) for row in rows if row.get("token")]
        narratives = [str(row["narrative"]) for row in rows if row.get("narrative")]
        if target_type == "tokens":
            tokens.extend(str(row["name"]) for row in rows if row.get("name"))
        elif target_type == "narratives":
            narratives.extend(str(row["name"]) for row in rows if row.get("name"))
        return {
            "match_count": total, "average_quality": average("quality_score"),
            "average_hype": average("hype_score"), "average_momentum": average("momentum_score"),
            "source_diversity": sum(int(row.get("source_count") or 0) for row in rows),
            "evaluated_outcome_count": len(outcomes),
            "success_rate": round(100 * outcomes.count("SUCCESS") / len(outcomes), 1) if outcomes else None,
            "strongest_tokens": list(dict.fromkeys(tokens))[:5],
            "strongest_narratives": list(dict.fromkeys(narratives))[:5],
            "noise_rate": average("noise_risk"),
            "evaluation_coverage": average("evaluation_coverage"),
        }

    @staticmethod
    def _safe_row(row: Any) -> dict[str, Any]:
        result = dict(row)
        for key, value in tuple(result.items()):
            if isinstance(value, bytes):
                result[key] = value.decode("utf-8", errors="replace")
        return result

    @staticmethod
    def _number(name: str, value: Any, minimum: float, maximum: float) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise SearchValidationError(f"{name} must be numeric") from exc
        if not minimum <= number <= maximum:
            raise SearchValidationError(f"{name} must be between {minimum:g} and {maximum:g}")
        return number

    @staticmethod
    def _validate_ranges(filters: dict[str, Any]) -> None:
        for prefix in ("hype", "momentum", "confidence", "quality"):
            if f"{prefix}_min" in filters and f"{prefix}_max" in filters:
                if filters[f"{prefix}_min"] > filters[f"{prefix}_max"]:
                    raise SearchValidationError(f"{prefix}_min cannot exceed {prefix}_max")
        if "date_from" in filters and "date_to" in filters:
            start = date.fromisoformat(filters["date_from"])
            end = date.fromisoformat(filters["date_to"])
            if start > end:
                raise SearchValidationError("date_from cannot be after date_to")
            if (end - start).days > 3660:
                raise SearchValidationError("date range cannot exceed 10 years")

    def _required(self, search_id: int) -> SavedSearch:
        search = self.get(search_id)
        if search is None:
            raise KeyError(search_id)
        return search

    def _publish(self, event: object) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(event)
