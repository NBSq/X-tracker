from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone


def bounded(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return round(max(minimum, min(maximum, float(value))), 6)


@dataclass(frozen=True)
class GraphWeightCalculator:
    half_life_days: float = 14.0

    def recency_decay(self, last_seen_at: str, now: datetime | None = None) -> float:
        current = now or datetime.now(timezone.utc)
        try:
            seen = datetime.fromisoformat(str(last_seen_at).replace("Z", "+00:00"))
        except ValueError:
            return 1.0
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (current - seen).total_seconds() / 86400)
        return bounded(0.5 ** (age_days / self.half_life_days))

    def edge_weight(
        self,
        *,
        occurrence_count: int,
        last_seen_at: str,
        source_count: int = 1,
        event_count: int = 1,
        hype_score: float = 0,
        momentum_score: float = 0,
        confidence: float = 0,
        outcome_success_rate: float = 0,
        priority: float = 0,
        ai_confidence: float | None = None,
        now: datetime | None = None,
    ) -> float:
        evidence = min(1.0, math.log1p(max(0, occurrence_count)) / math.log(11))
        diversity = min(1.0, (max(0, source_count) + max(0, event_count)) / 8)
        quality_values = (
            bounded(hype_score / 100),
            bounded(momentum_score / 100),
            bounded(confidence / 10),
            bounded(outcome_success_rate / 100),
            bounded(priority / 100),
        )
        quality = sum(quality_values) / len(quality_values)
        observed = 0.45 * evidence + 0.20 * diversity + 0.35 * quality
        if ai_confidence is not None:
            observed = 0.65 * observed + 0.35 * bounded(ai_confidence)
        return bounded(observed * self.recency_decay(last_seen_at, now))

    def node_weight(
        self,
        *,
        weighted_degree: float,
        activity_score: float,
        last_seen_at: str,
        now: datetime | None = None,
    ) -> float:
        connectivity = 1 - math.exp(-max(0.0, weighted_degree) / 3)
        base = 0.65 * connectivity + 0.35 * bounded(activity_score / 100)
        return bounded(base * self.recency_decay(last_seen_at, now))

    def emerging_score(
        self,
        *,
        occurrence_count: int,
        source_count: int,
        event_count: int,
        hype_score: float,
        momentum_score: float,
        last_seen_at: str,
        previous_occurrences: int = 0,
        now: datetime | None = None,
    ) -> float:
        growth = (
            (occurrence_count - previous_occurrences) / max(1, previous_occurrences)
            if previous_occurrences
            else min(1.0, occurrence_count / 3)
        )
        score = 100 * (
            0.30 * bounded(growth)
            + 0.20 * min(1.0, source_count / 4)
            + 0.15 * min(1.0, event_count / 4)
            + 0.15 * bounded(hype_score / 100)
            + 0.10 * bounded(momentum_score / 100)
            + 0.10 * self.recency_decay(last_seen_at, now)
        )
        return round(max(0.0, min(100.0, score)), 2)


def classify_relationship(
    score: float,
    decay: float,
    occurrence_count: int,
    previous_occurrences: int = 0,
) -> str:
    if decay < 0.15:
        return "inactive"
    if previous_occurrences > occurrence_count:
        return "weakening"
    if score >= 75:
        return "accelerating"
    if score >= 55 and occurrence_count >= 2:
        return "emerging"
    if decay < 0.45:
        return "weakening"
    return "stable"
