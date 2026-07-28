from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


CLASSIFICATIONS = frozenset(
    {"excellent", "strong", "moderate", "weak", "unreliable", "insufficient_data"}
)
RECOMMENDATION_STATUSES = frozenset({"open", "acknowledged", "resolved", "dismissed"})


@dataclass(frozen=True)
class QualityBreakdown:
    outcome_quality: float | None = None
    confidence_calibration: float | None = None
    source_reliability: float | None = None
    evidence_strength: float | None = None
    source_diversity: float | None = None
    timeliness: float | None = None
    rule_precision: float | None = None
    watchlist_relevance: float | None = None
    ai_agreement: float | None = None
    duplicate_reduction_value: float | None = None
    noise_risk: float = 0.0
    evaluation_coverage: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SignalQualityScore:
    id: int | None
    signal_id: int
    quality_score: float
    classification: str
    breakdown: QualityBreakdown
    evidence_count: int
    calculation_version: int
    calculated_at: str

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "SignalQualityScore":
        raw = _json_object(row["breakdown_json"])
        fields = {
            name: raw.get(name, row[name] if name in row.keys() else None)
            for name in QualityBreakdown.__dataclass_fields__
            if name != "details"
        }
        fields["details"] = raw.get("details", {})
        return cls(
            id=int(row["id"]), signal_id=int(row["signal_id"]),
            quality_score=float(row["quality_score"]),
            classification=str(row["classification"]),
            breakdown=QualityBreakdown(**fields),
            evidence_count=int(row["evidence_count"]),
            calculation_version=int(row["calculation_version"]),
            calculated_at=str(row["calculated_at"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QualityAggregate:
    entity_type: str
    entity_id: str
    period_start: str
    period_end: str
    signal_count: int
    evaluated_count: int
    successful_count: int
    neutral_count: int
    failed_count: int
    average_quality_score: float | None
    median_quality_score: float | None
    precision: float | None
    noise_rate: float | None
    evaluation_coverage: float | None
    average_confidence: float | None
    calibration_error: float | None
    reliability_score: float | None
    calculation_version: int
    metrics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class QualityRecommendation:
    id: int | None
    entity_type: str
    entity_id: str
    recommendation_type: str
    severity: str
    title: str
    description: str
    suggested_action: str
    confidence: float
    minimum_sample_requirement: int
    evidence: dict[str, Any]
    status: str = "open"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _json_object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
