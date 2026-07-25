from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Protocol

from app.ingestion.models import (
    NormalizedContentItem,
    SourceConfigurationError,
    SourceDefinition,
    clean_text,
    utc_now,
)
from app.sources.rss_client import RSSClient, RSSFeed


logger = logging.getLogger("x_narrative_tracker")


class ContentSource(Protocol):
    definition: SourceDefinition

    def fetch(self, limit: int) -> list[NormalizedContentItem]: ...
    def normalize(self, raw_item) -> NormalizedContentItem: ...
    def validate(self) -> None: ...
    def get_source_metadata(self) -> dict[str, object]: ...


class FeedContentSource:
    def __init__(
        self,
        definition: SourceDefinition,
        *,
        timeout_seconds: int = 20,
        max_retries: int = 2,
        client: RSSClient | None = None,
    ) -> None:
        self.definition = definition
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.client = client or RSSClient(timeout_seconds=timeout_seconds)
        self.validate()

    def validate(self) -> None:
        if self.definition.source_type not in {"rss", "atom", "feed"}:
            raise SourceConfigurationError("FeedContentSource requires a feed source")

    def fetch(self, limit: int) -> list[NormalizedContentItem]:
        last_error = None
        for attempt in range(self.max_retries + 1):
            try:
                posts = self.client.fetch_recent_posts(
                    [RSSFeed(self.definition.name, self.definition.url)],
                    limit,
                    raise_errors=True,
                )
                return [self.normalize(post) for post in posts]
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
        assert last_error is not None
        raise last_error

    def normalize(self, post) -> NormalizedContentItem:
        title, separator, body = str(post.text).partition(". ")
        if not separator:
            title, body = str(post.text), ""
        return NormalizedContentItem(
            external_id=str(post.id),
            source_id=self.definition.source_key,
            source_name=self.definition.name,
            source_type=self.definition.source_type,
            title=title,
            body=body,
            canonical_url=str(post.url),
            author=str(post.username),
            published_at=post.created_at,
            fetched_at=utc_now(),
            metadata={
                "categories": list(self.definition.categories),
                "source_priority": self.definition.priority,
            },
        )

    def get_source_metadata(self) -> dict[str, object]:
        return self.definition.as_dict()


class LocalJSONContentSource:
    def __init__(self, definition: SourceDefinition) -> None:
        self.definition = definition
        self.validate()

    def validate(self) -> None:
        if self.definition.source_type != "local_json":
            raise SourceConfigurationError("LocalJSONContentSource requires local_json")

    def fetch(self, limit: int) -> list[NormalizedContentItem]:
        path = Path(self.definition.url)
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        values = payload.get("items", payload.get("posts", payload if isinstance(payload, list) else []))
        return [self.normalize(value) for value in values[:limit]]

    def normalize(self, value) -> NormalizedContentItem:
        if not isinstance(value, dict):
            raise ValueError("local JSON content items must be objects")
        external_id = str(value.get("external_id", value.get("id", ""))).strip()
        title = clean_text(str(value.get("title", "")))
        body = clean_text(str(value.get("body", value.get("text", ""))))
        return NormalizedContentItem(
            external_id=external_id,
            source_id=self.definition.source_key,
            source_name=self.definition.name,
            source_type="local_json",
            title=title or body[:160],
            body=body,
            canonical_url=str(value.get("canonical_url", value.get("url", ""))),
            author=str(value.get("author", value.get("username", self.definition.name))),
            published_at=value.get("published_at", value.get("created_at")),
            fetched_at=utc_now(),
            language=str(value.get("language", "unknown")),
            tokens=tuple(value.get("tokens", [])),
            narratives=tuple(value.get("narratives", [])),
            metadata=dict(value.get("metadata", {})),
        )

    def get_source_metadata(self) -> dict[str, object]:
        return self.definition.as_dict()


def load_source_definitions(path: Path) -> list[SourceDefinition]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Content source configuration not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Content source configuration contains invalid JSON: {path}") from exc
    if not isinstance(payload, list):
        raise RuntimeError("Content source configuration must be a JSON array")
    definitions = []
    seen = set()
    for index, value in enumerate(payload, start=1):
        try:
            definition = SourceDefinition.from_mapping(value)
            if definition.source_key in seen:
                raise SourceConfigurationError("duplicate source id")
            seen.add(definition.source_key)
            definitions.append(definition)
        except (SourceConfigurationError, TypeError, ValueError) as exc:
            logger.warning("Source configuration disabled index=%s error=%s", index, exc)
    return definitions


def create_content_source(
    definition: SourceDefinition,
    *,
    timeout_seconds: int = 20,
    max_retries: int = 2,
) -> ContentSource:
    if definition.source_type == "local_json":
        return LocalJSONContentSource(definition)
    return FeedContentSource(
        definition,
        timeout_seconds=timeout_seconds,
        max_retries=max_retries,
    )
