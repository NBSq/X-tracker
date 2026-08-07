from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


class SearchValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SavedSearch:
    id: int
    name: str
    description: str
    enabled: bool
    target_type: str
    filters: dict[str, Any]
    sort_by: str
    sort_direction: str
    result_limit: int
    created_at: str
    updated_at: str
    last_run_at: str | None
    run_count: int

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "SavedSearch":
        try:
            filters = json.loads(str(row["filters_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            filters = {}
        return cls(
            id=int(row["id"]), name=str(row["name"]),
            description=str(row["description"] or ""), enabled=bool(row["enabled"]),
            target_type=str(row["target_type"]), filters=filters,
            sort_by=str(row["sort_by"]), sort_direction=str(row["sort_direction"]),
            result_limit=int(row["result_limit"]), created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            last_run_at=str(row["last_run_at"]) if row["last_run_at"] else None,
            run_count=int(row["run_count"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class SearchResult:
    search: SavedSearch
    results: tuple[dict[str, Any], ...]
    total_matches: int
    summary: dict[str, Any]
    executed_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "search": self.search.as_dict(), "results": list(self.results),
            "total_matches": self.total_matches, "summary": self.summary,
            "executed_at": self.executed_at,
        }
