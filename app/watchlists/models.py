from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


WATCHLIST_ITEM_TYPES = frozenset({"token", "narrative"})


class WatchlistValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Watchlist:
    id: int
    name: str
    description: str
    enabled: bool
    priority: int
    minimum_hype_score: float
    minimum_momentum_score: float
    minimum_confidence: int
    telegram_enabled: bool
    include_in_digest: bool
    dashboard_highlight: bool
    case_insensitive: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "Watchlist":
        return cls(
            id=int(row["id"]),
            name=str(row["name"]),
            description=str(row["description"] or ""),
            enabled=bool(row["enabled"]),
            priority=int(row["priority"]),
            minimum_hype_score=float(row["minimum_hype_score"]),
            minimum_momentum_score=float(row["minimum_momentum_score"]),
            minimum_confidence=int(row["minimum_confidence"]),
            telegram_enabled=bool(row["telegram_enabled"]),
            include_in_digest=bool(row["include_in_digest"]),
            dashboard_highlight=bool(row["dashboard_highlight"]),
            case_insensitive=bool(row["case_insensitive"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WatchlistItem:
    id: int
    watchlist_id: int
    item_type: str
    item_value: str
    normalized_value: str
    created_at: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "WatchlistItem":
        return cls(
            id=int(row["id"]),
            watchlist_id=int(row["watchlist_id"]),
            item_type=str(row["item_type"]),
            item_value=str(row["item_value"]),
            normalized_value=str(row["normalized_value"]),
            created_at=str(row["created_at"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WatchlistMatch:
    watchlist: Watchlist
    items: tuple[WatchlistItem, ...]


@dataclass(frozen=True)
class WatchlistReport:
    watchlist: Watchlist
    items: tuple[WatchlistItem, ...]
    signals_count: int
    evaluated_count: int
    successful_count: int
    neutral_count: int
    failed_count: int
    success_rate: float | None
    average_hype_score: float | None
    average_momentum_score: float | None
    last_matched_at: str | None
    latest_matches: tuple[dict[str, Any], ...]
    related_rules: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
