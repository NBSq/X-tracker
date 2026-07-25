from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_PARAMETERS = frozenset(
    {
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "ref",
        "source",
        "utm_campaign",
        "utm_content",
        "utm_medium",
        "utm_source",
        "utm_term",
    }
)
SOURCE_TYPES = frozenset({"rss", "atom", "feed", "local_json"})


class SourceConfigurationError(ValueError):
    pass


@dataclass(frozen=True)
class SourceDefinition:
    source_key: str
    name: str
    source_type: str
    url: str
    enabled: bool = True
    priority: int = 5
    categories: tuple[str, ...] = ()
    fetch_interval_seconds: int = 300

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> "SourceDefinition":
        source_key = str(value.get("id", value.get("source_key", ""))).strip()
        name = str(value.get("name", "")).strip()
        source_type = str(value.get("type", value.get("source_type", "rss"))).lower()
        url = str(value.get("url", "")).strip()
        if not source_key or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,80}", source_key):
            raise SourceConfigurationError("source id must use letters, numbers, ._- only")
        if not name:
            raise SourceConfigurationError(f"source {source_key} requires a name")
        if source_type not in SOURCE_TYPES:
            raise SourceConfigurationError(
                f"source {source_key} has unsupported type: {source_type}"
            )
        if not url:
            raise SourceConfigurationError(f"source {source_key} requires a URL or path")
        priority = int(value.get("priority", 5))
        interval = int(value.get("fetch_interval_seconds", 300))
        if not 0 <= priority <= 10:
            raise SourceConfigurationError(f"source {source_key} priority must be 0-10")
        if interval <= 0:
            raise SourceConfigurationError(
                f"source {source_key} fetch interval must be positive"
            )
        categories = tuple(
            dict.fromkeys(
                str(item).strip()
                for item in value.get("categories", [])
                if str(item).strip()
            )
        )
        return cls(
            source_key=source_key,
            name=name,
            source_type=source_type,
            url=url,
            enabled=bool(value.get("enabled", True)),
            priority=priority,
            categories=categories,
            fetch_interval_seconds=interval,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.source_key,
            "name": self.name,
            "type": self.source_type,
            "url": self.url,
            "enabled": self.enabled,
            "priority": self.priority,
            "categories": list(self.categories),
            "fetch_interval_seconds": self.fetch_interval_seconds,
        }


@dataclass(frozen=True)
class NormalizedContentItem:
    external_id: str
    source_id: str
    source_name: str
    source_type: str
    title: str
    body: str
    canonical_url: str
    author: str
    published_at: str | None
    fetched_at: str
    language: str = "unknown"
    tokens: tuple[str, ...] = ()
    narratives: tuple[str, ...] = ()
    content_hash: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.external_id or not self.source_id:
            raise ValueError("normalized content requires external_id and source_id")
        if not self.title and not self.body:
            raise ValueError("normalized content requires a title or body")
        object.__setattr__(self, "title", clean_text(self.title))
        object.__setattr__(self, "body", clean_text(self.body))
        object.__setattr__(self, "canonical_url", canonicalize_url(self.canonical_url))
        object.__setattr__(self, "tokens", tuple(_unique(self.tokens, upper=True)))
        object.__setattr__(self, "narratives", tuple(_unique(self.narratives)))
        if not self.content_hash:
            object.__setattr__(
                self,
                "content_hash",
                content_fingerprint(self.title, self.body),
            )

    @property
    def normalized_title(self) -> str:
        return normalize_title(self.title)

    def to_post(self):
        from app.sources.x_client import XPost

        text = f"{self.title}. {self.body}" if self.title and self.body else self.title or self.body
        return XPost(
            id=self.external_id,
            username=self.author or self.source_name,
            text=text[:5000],
            created_at=self.published_at,
            url=self.canonical_url,
        )


@dataclass(frozen=True)
class DeduplicationMatch:
    matched: bool
    reason: str | None = None
    content_item_id: int | None = None
    unified_event_id: int | None = None
    similarity_score: float = 0.0
    exact: bool = False


@dataclass(frozen=True)
class IngestionResult:
    fetched_count: int
    accepted_count: int
    duplicate_count: int
    new_event_count: int
    posts: tuple[Any, ...]


def canonicalize_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return raw
    if not parsed.scheme or not parsed.netloc:
        return raw.split("#", 1)[0]
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return raw.split("#", 1)[0]
    port = parsed.port
    netloc = hostname
    if port and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        netloc = f"{hostname}:{port}"
    query = urlencode(
        sorted(
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if key.lower() not in TRACKING_PARAMETERS
            and not key.lower().startswith("utm_")
        ),
        doseq=True,
    )
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), netloc, path, query, ""))


def normalize_title(value: str) -> str:
    text = clean_text(value).casefold()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def clean_text(value: str) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", str(value or ""))
    return " ".join(text.split())


def content_fingerprint(title: str, body: str) -> str:
    value = f"{normalize_title(title)}\n{normalize_title(body)}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def metadata_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _unique(values, upper: bool = False) -> list[str]:
    result = []
    seen = set()
    for value in values:
        text = clean_text(str(value)).lstrip("$")
        text = text.upper() if upper else text
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            result.append(text)
    return result
