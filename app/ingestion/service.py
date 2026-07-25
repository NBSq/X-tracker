from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone

from app.config import Config
from app.db.database import Database
from app.events import (
    ContentAccepted,
    ContentDeduplicated,
    ContentFetched,
    EventBus,
    SourceFetchFailed,
    SourceRecovered,
    UnifiedEventCreated,
    UnifiedEventMateriallyChanged,
    UnifiedEventUpdated,
)
from app.ingestion.deduplication import DeduplicationService
from app.ingestion.models import IngestionResult, SourceDefinition
from app.ingestion.sources import create_content_source, load_source_definitions
from app.ingestion.unified_events import UnifiedEventService


logger = logging.getLogger("x_narrative_tracker")


class MultiSourceIngestionService:
    def __init__(
        self,
        db: Database,
        config: Config,
        event_bus: EventBus | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.event_bus = event_bus
        self.deduplication = DeduplicationService(db, config)
        self.events = UnifiedEventService(db, config)

    def sync_configured_sources(self) -> list[SourceDefinition]:
        definitions = load_source_definitions(self.config.content_sources_path)
        for definition in definitions:
            if self.db.get_content_source(definition.source_key) is None:
                self.db.upsert_content_source(definition)
        return definitions

    def fetch_all(self) -> IngestionResult:
        if not self.config.source_enabled:
            logger.info("Multi-source ingestion is disabled")
            return IngestionResult(0, 0, 0, 0, ())
        totals = [0, 0, 0, 0]
        posts = []
        for definition in self.sync_configured_sources():
            row = self.db.get_content_source(definition.source_key)
            if row is None or not row["enabled"] or not self._is_due(row):
                continue
            result = self.fetch_source(definition.source_key)
            counts = (
                result.fetched_count,
                result.accepted_count,
                result.duplicate_count,
                result.new_event_count,
            )
            totals = [left + right for left, right in zip(totals, counts)]
            posts.extend(result.posts)
        return IngestionResult(*totals, tuple(posts))

    def _is_due(self, row) -> bool:
        last_fetch = row["last_fetch_at"]
        if not last_fetch:
            return True
        try:
            fetched_at = datetime.fromisoformat(str(last_fetch).replace("Z", "+00:00"))
        except ValueError:
            return True
        if fetched_at.tzinfo is None:
            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
        interval = (
            self.config.source_failure_backoff_seconds
            if int(row["consecutive_failures"] or 0)
            else int(row["fetch_interval_seconds"] or self.config.source_default_interval_seconds)
        )
        return datetime.now(timezone.utc) >= fetched_at + timedelta(seconds=interval)

    def fetch_source(self, identifier: int | str) -> IngestionResult:
        row = self.db.get_content_source(identifier)
        if row is None:
            self.sync_configured_sources()
            row = self.db.get_content_source(identifier)
        if row is None:
            raise KeyError(identifier)
        definition = SourceDefinition.from_mapping(
            {
                "id": row["source_key"],
                "name": row["name"],
                "type": row["source_type"],
                "url": row["url"],
                "enabled": bool(row["enabled"]),
                "priority": row["priority"],
                "categories": json.loads(row["categories_json"] or "[]"),
                "fetch_interval_seconds": row["fetch_interval_seconds"],
            }
        )
        source = create_content_source(
            definition,
            timeout_seconds=self.config.source_fetch_timeout_seconds,
            max_retries=self.config.source_max_retries,
        )
        started = time.perf_counter()
        try:
            items = source.fetch(self.config.source_max_items_per_fetch)
            posts = []
            accepted = duplicates = new_events = 0
            for item in items:
                post, is_duplicate, is_new = self.ingest_item(int(row["id"]), item)
                duplicates += int(is_duplicate)
                accepted += int(not is_duplicate)
                new_events += int(is_new)
                if post is not None:
                    posts.append(post)
            duration = int((time.perf_counter() - started) * 1000)
            recovered = self.db.record_source_fetch(
                int(row["id"]), success=True, item_count=len(items),
                accepted_count=accepted, duplicate_count=duplicates,
                duration_ms=duration,
            )
            self._publish(ContentFetched(int(row["id"]), definition.source_key, len(items), duration))
            if recovered and self.config.source_recovery_notifications:
                self._publish(SourceRecovered(int(row["id"]), definition.source_key))
            logger.info(
                "Source fetch complete source_id=%s source_type=%s item_count=%s "
                "accepted_count=%s duplicate_count=%s duration_ms=%s",
                definition.source_key, definition.source_type, len(items), accepted,
                duplicates, duration,
            )
            return IngestionResult(len(items), accepted, duplicates, new_events, tuple(posts))
        except Exception as exc:
            duration = int((time.perf_counter() - started) * 1000)
            error_type = _error_type(exc)
            self.db.record_source_fetch(
                int(row["id"]), success=False, item_count=0, accepted_count=0,
                duplicate_count=0, duration_ms=duration, error_type=error_type,
            )
            current = self.db.get_content_source(int(row["id"]))
            failures = int(current["consecutive_failures"] or 0)
            self._publish(
                SourceFetchFailed(int(row["id"]), definition.source_key, error_type, failures)
            )
            logger.warning(
                "Source fetch failed source_id=%s source_type=%s duration_ms=%s "
                "error_type=%s",
                definition.source_key, definition.source_type, duration, error_type,
            )
            return IngestionResult(0, 0, 0, 0, ())

    def ingest_item(self, source_id: int, item):
        match = self.deduplication.match(source_id, item)
        if match.matched:
            if match.reason == "same_external_id" and match.content_item_id is not None:
                event_id = match.unified_event_id
                if event_id is None:
                    event_id = self.events.create(match.content_item_id)
                self._publish(
                    ContentDeduplicated(
                        match.content_item_id,
                        event_id,
                        item.source_id,
                        match.reason,
                        match.similarity_score,
                    )
                )
                return None, True, False
            status = "exact_duplicate" if match.exact else "near_duplicate"
            item_id = self.db.save_content_item(
                source_id,
                item,
                status=status,
                duplicate_reason=match.reason,
                duplicate_of_content_item_id=match.content_item_id,
            )
            event_id = match.unified_event_id
            if event_id is None and match.content_item_id is not None:
                event_id = self.events.create(match.content_item_id)
            if event_id is None:
                event_id = self.events.create(item_id)
            before = dict(self.db.get_unified_event(event_id))
            after, created, material_reason = self.events.add_item(
                event_id, item_id, match.similarity_score, match.reason or status
            )
            self._publish(
                ContentDeduplicated(
                    item_id, event_id, item.source_id,
                    match.reason or status, match.similarity_score,
                )
            )
            logger.info(
                "Content deduplicated source_id=%s unified_event_id=%s "
                "similarity_score=%.4f match_reason=%s",
                item.source_id, event_id, match.similarity_score,
                match.reason or status,
            )
            if created:
                self._publish(
                    UnifiedEventUpdated(
                        event_id,
                        int(after["source_count"]),
                        int(after["item_count"]),
                        int(after["source_count"]) - int(before["source_count"]),
                    )
                )
            if material_reason:
                self._publish(
                    UnifiedEventMateriallyChanged(
                        event_id,
                        int(before["source_count"]),
                        int(after["source_count"]),
                        float(before["hype_score"]),
                        float(after["hype_score"]),
                        float(before["momentum_score"]),
                        float(after["momentum_score"]),
                        material_reason,
                    )
                )
            return None, True, False

        item_id = self.db.save_content_item(source_id, item, status="accepted")
        event_id = self.events.create(item_id)
        self._publish(ContentAccepted(item_id, event_id, item.source_id))
        self._publish(UnifiedEventCreated(event_id, item_id))
        logger.info(
            "Content accepted source_id=%s unified_event_id=%s match_reason=unique",
            item.source_id, event_id,
        )
        return item.to_post(), False, True

    def _publish(self, event: object) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(event)


def format_deduplication_report(db: Database, days: int = 30) -> str:
    stats = db.get_deduplication_stats(days)
    raw = int(stats["raw_items"] or 0)
    exact = int(stats["exact_duplicates"] or 0)
    near = int(stats["near_duplicates"] or 0)
    reduction = (exact + near) / raw * 100.0 if raw else 0.0
    top = db.get_top_duplicate_sources(days)
    lines = [
        "Deduplication Report", "", f"Period: Last {days} days", "",
        f"Raw items fetched: {raw}",
        f"Accepted items: {int(stats['accepted_items'] or 0)}",
        f"Exact duplicates: {exact}", f"Near duplicates: {near}",
        f"Unified events: {int(stats['unified_events'] or 0)}", "",
        f"Duplicate reduction: {reduction:.1f}%",
        f"Average sources per event: {float(stats['average_sources'] or 0):.1f}",
        f"Maximum sources in one event: {int(stats['maximum_sources'] or 0)}",
        "", "Top duplicate sources:",
    ]
    lines.extend(
        f"{index}. {row['name']} - {row['duplicate_count']}"
        for index, row in enumerate(top, start=1)
    )
    if not top:
        lines.append("None")
    return "\n".join(lines)


def _error_type(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code in {401, 403}:
        return "authentication"
    if status_code == 429:
        return "rate_limit"
    if isinstance(status_code, int) and status_code >= 500:
        return "upstream_server"
    if "timeout" in name:
        return "timeout"
    if "parse" in name or "xml" in name or "json" in name:
        return "malformed_response"
    if "connection" in name or "network" in name:
        return "network"
    return "unexpected"
