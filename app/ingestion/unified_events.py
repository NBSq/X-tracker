from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

from app.config import Config
from app.db.database import Database
from app.ingestion.deduplication import compare_content, _inside_publication_window
from app.ingestion.models import NormalizedContentItem


class UnifiedEventService:
    def __init__(self, db: Database, config: Config) -> None:
        self.db = db
        self.config = config

    def create(self, content_item_id: int) -> int:
        item = self.db.get_content_item(content_item_id)
        if item is None:
            raise ValueError("Content item does not exist")
        key_input = f"{item['normalized_title']}|{str(item['published_at'] or '')[:10]}"
        event_key = hashlib.sha256(key_input.encode("utf-8")).hexdigest()
        return self.db.create_unified_event(content_item_id, event_key)

    def add_item(
        self,
        event_id: int,
        content_item_id: int,
        similarity_score: float,
        match_reason: str,
    ) -> tuple[dict[str, object], bool, str | None]:
        before = self.db.get_unified_event(event_id)
        if before is None:
            raise ValueError("Unified event does not exist")
        before_values = dict(before)
        created = self.db.add_unified_event_item(
            event_id,
            content_item_id,
            similarity_score,
            match_reason,
        )
        if not created:
            return before_values, False, None
        self.recalculate(event_id)
        after = dict(self.db.get_unified_event(event_id))
        material_reason = self.material_reason(before_values, after, content_item_id)
        if material_reason and self._cooldown_elapsed(before_values):
            self.db.update_unified_event(
                event_id,
                material_version=int(after["material_version"]) + 1,
                last_material_update_at=datetime.now(timezone.utc).isoformat(),
            )
            self.db.save_unified_event_history(
                event_id,
                "material_update",
                {"reason": material_reason},
            )
            after = dict(self.db.get_unified_event(event_id))
        else:
            material_reason = None
            self.db.save_unified_event_history(event_id, "item_added", {})
        return after, True, material_reason

    def recalculate(self, event_id: int) -> None:
        rows = self.db.get_unified_event_items(event_id)
        if not rows:
            return
        primary = select_primary_item(rows)
        sources = {int(row["source_id"]) for row in rows}
        moments = [str(row["published_at"] or row["fetched_at"]) for row in rows]
        tokens = list(dict.fromkeys(
            str(value) for row in rows for value in json.loads(row["tokens_json"] or "[]")
        ))
        narratives = list(dict.fromkeys(
            str(value) for row in rows for value in json.loads(row["narratives_json"] or "[]")
        ))
        conflicts = detect_conflicts(rows)
        self.db.update_unified_event(
            event_id,
            primary_content_item_id=int(primary["id"]),
            title=str(primary["title"]),
            summary=str(primary["body"])[:500],
            token=tokens[0] if tokens else None,
            narrative=narratives[0] if narratives else None,
            tokens_json=json.dumps(tokens, ensure_ascii=False),
            narratives_json=json.dumps(narratives, ensure_ascii=False),
            detected_conflicts_json=json.dumps(conflicts, ensure_ascii=False),
            conflict_count=len(conflicts),
            requires_review=int(bool(conflicts)),
            first_seen_at=min(moments),
            last_seen_at=max(moments),
            source_count=len(sources),
            item_count=len(rows),
            duplicate_count=sum(row["status"] != "accepted" for row in rows),
            highest_source_priority=max(int(row["source_priority"]) for row in rows),
        )

    def material_reason(
        self,
        before: dict[str, object],
        after: dict[str, object],
        content_item_id: int,
    ) -> str | None:
        if not self.config.event_update_notifications:
            return None
        source_delta = int(after["source_count"]) - int(before["source_count"])
        if source_delta >= self.config.event_update_min_new_sources:
            return f"gained {source_delta} new sources"
        item = self.db.get_content_item(content_item_id)
        if item is not None and int(item["source_priority"]) >= 8 and source_delta > 0:
            return "high-priority source added"
        if float(after["hype_score"]) - float(before["hype_score"]) >= self.config.event_update_min_hype_change:
            return "hype increased materially"
        if float(after["momentum_score"]) - float(before["momentum_score"]) >= self.config.event_update_min_momentum_change:
            return "momentum increased materially"
        if float(after["confidence"]) - float(before["confidence"]) >= 1:
            return "confidence increased materially"
        before_entities = set(json.loads(str(before["tokens_json"] or "[]"))) | set(
            json.loads(str(before["narratives_json"] or "[]"))
        )
        after_entities = set(json.loads(str(after["tokens_json"] or "[]"))) | set(
            json.loads(str(after["narratives_json"] or "[]"))
        )
        if after_entities - before_entities:
            return "new token or narrative detected"
        if int(after["conflict_count"]) > int(before["conflict_count"]):
            return "new conflicting details detected"
        return None

    def archive_stale(self, hours: int = 168) -> int:
        return self.db.archive_stale_unified_events(hours)

    def _cooldown_elapsed(self, event: dict[str, object]) -> bool:
        last = event.get("last_material_update_at")
        if not last:
            return True
        try:
            timestamp = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
        except ValueError:
            return True
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - timestamp >= timedelta(
            minutes=self.config.event_update_cooldown_minutes
        )

    def rebuild(self) -> tuple[int, int]:
        rows = list(reversed(self.db.get_content_items(limit=None)))
        processed = 0
        created = 0
        associated: list[tuple[object, int]] = []
        for row in rows:
            if row["unified_event_id"] is not None:
                associated.append((row, int(row["unified_event_id"])))
        for row in rows:
            processed += 1
            if row["unified_event_id"] is not None:
                continue
            event_id = _rebuild_match(row, associated, self.config)
            if event_id is None:
                event_id = self.create(int(row["id"]))
                created += 1
            else:
                self.db.add_unified_event_item(
                    event_id,
                    int(row["id"]),
                    1.0,
                    "historical_rebuild",
                )
                self.recalculate(event_id)
                self.db.save_unified_event_history(
                    event_id, "historical_item_added", {"content_item_id": row["id"]}
                )
            associated.append((row, event_id))
        return processed, created


def _rebuild_match(row, associated, config: Config) -> int | None:
    for candidate, event_id in associated:
        if row["canonical_url"] and row["canonical_url"] == candidate["canonical_url"]:
            return event_id
        if row["content_hash"] == candidate["content_hash"]:
            return event_id
        if row["normalized_title"] and row["normalized_title"] == candidate["normalized_title"]:
            return event_id
    item = NormalizedContentItem(
        external_id=str(row["external_id"]),
        source_id=str(row["source_key"]),
        source_name=str(row["source_name"]),
        source_type=str(row["source_type"]),
        title=str(row["title"]),
        body=str(row["body"]),
        canonical_url=str(row["canonical_url"]),
        author=str(row["author"]),
        published_at=row["published_at"],
        fetched_at=str(row["fetched_at"]),
        tokens=tuple(json.loads(row["tokens_json"] or "[]")),
        narratives=tuple(json.loads(row["narratives_json"] or "[]")),
    )
    for candidate, event_id in associated:
        if config.deduplication_cross_source_only and row["source_id"] == candidate["source_id"]:
            continue
        if not _inside_publication_window(
            row["published_at"], candidate["published_at"],
            config.deduplication_time_window_hours,
        ):
            continue
        similarity = compare_content(item, candidate)
        if similarity.shared_entities < config.deduplication_min_shared_entities:
            continue
        if (
            similarity.title >= config.deduplication_title_similarity_threshold
            or similarity.body >= config.deduplication_body_similarity_threshold
        ):
            return event_id
    return None


def select_primary_item(rows):
    return sorted(
        rows,
        key=lambda row: (
            -int(row["source_priority"]),
            -(len(str(row["title"])) + len(str(row["body"]))),
            -int(bool(row["canonical_url"])),
            str(row["published_at"] or row["fetched_at"]),
            int(row["id"]),
        ),
    )[0]


def detect_conflicts(rows) -> list[str]:
    texts = [f"{row['title']} {row['body']}".casefold() for row in rows]
    conflicts = []
    term_pairs = (
        ("launched", "delayed"),
        ("approved", "rejected"),
        ("live", "postponed"),
        ("confirmed", "denied"),
    )
    joined = " | ".join(texts)
    for positive, negative in term_pairs:
        if any(positive in text for text in texts) and any(negative in text for text in texts):
            conflicts.append(f"Conflicting status terms: {positive} vs {negative}")
    numbers = {
        match
        for text in texts
        for match in re.findall(r"\b\d+(?:\.\d+)?%?\b", text)
    }
    if len(numbers) >= 3:
        conflicts.append("Multiple incompatible numeric claims may require review")
    dates = {
        match
        for text in texts
        for match in re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", text)
    }
    if len(dates) > 1:
        conflicts.append("Different event dates are reported")
    return conflicts
