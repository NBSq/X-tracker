from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class ScheduledReportValidationError(ValueError):
    pass


@dataclass(frozen=True)
class ScheduledReport:
    id: int
    name: str
    saved_search_id: int
    saved_search_name: str
    enabled: bool
    schedule_type: str
    schedule_value: str
    timezone: str
    delivery_type: str
    destination: str | None
    include_summary: bool
    include_top_results: bool
    include_csv: bool
    max_results: int
    last_run_at: str | None
    next_run_at: str
    last_status: str | None
    last_error_type: str | None
    total_runs: int
    successful_runs: int
    failed_runs: int
    created_at: str
    updated_at: str
    success_rate: float

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "ScheduledReport":
        keys = set(row.keys())
        return cls(
            id=int(row["id"]), name=str(row["name"]),
            saved_search_id=int(row["saved_search_id"]),
            saved_search_name=str(row["saved_search_name"]) if "saved_search_name" in keys else "",
            enabled=bool(row["enabled"]), schedule_type=str(row["schedule_type"]),
            schedule_value=str(row["schedule_value"]), timezone=str(row["timezone"]),
            delivery_type=str(row["delivery_type"]),
            destination=str(row["destination"]) if row["destination"] else None,
            include_summary=bool(row["include_summary"]),
            include_top_results=bool(row["include_top_results"]),
            include_csv=bool(row["include_csv"]), max_results=int(row["max_results"]),
            last_run_at=str(row["last_run_at"]) if row["last_run_at"] else None,
            next_run_at=str(row["next_run_at"]),
            last_status=str(row["last_status"]) if row["last_status"] else None,
            last_error_type=str(row["last_error_type"]) if row["last_error_type"] else None,
            total_runs=int(row["total_runs"]), successful_runs=int(row["successful_runs"]),
            failed_runs=int(row["failed_runs"]), created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            success_rate=float(row["success_rate"]) if "success_rate" in keys else 0.0,
        )

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()
