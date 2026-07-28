from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Mapping

from app.config import Config
from app.quality.models import QualityBreakdown


def bounded(value: float) -> float:
    return round(max(0.0, min(100.0, float(value))), 2)


@dataclass(frozen=True)
class QualityScoreCalculator:
    config: Config

    @property
    def weights(self) -> dict[str, float]:
        return {
            "outcome_quality": self.config.quality_outcome_weight,
            "confidence_calibration": self.config.quality_calibration_weight,
            "source_reliability": self.config.quality_source_reliability_weight,
            "evidence_strength": self.config.quality_evidence_weight,
            "source_diversity": self.config.quality_source_diversity_weight,
            "timeliness": self.config.quality_timeliness_weight,
            "rule_precision": self.config.quality_rule_precision_weight,
            "watchlist_relevance": self.config.quality_watchlist_relevance_weight,
            "ai_agreement": self.config.quality_ai_agreement_weight,
        }

    def calculate(self, breakdown: QualityBreakdown, evidence_count: int) -> tuple[float, str]:
        available = {
            name: float(value)
            for name, weight in self.weights.items()
            if weight > 0 and (value := getattr(breakdown, name)) is not None
        }
        weight_total = sum(self.weights[name] for name in available)
        score = bounded(
            sum(value * self.weights[name] for name, value in available.items())
            / weight_total
        ) if weight_total else 0.0
        classification = self.classify(
            score,
            sufficient=(
                evidence_count >= self.config.quality_minimum_evidence
                and len(available) >= 3
            ),
        )
        return score, classification

    def classify(self, score: float, *, sufficient: bool = True) -> str:
        if not sufficient:
            return "insufficient_data"
        if score >= self.config.quality_excellent_threshold:
            return "excellent"
        if score >= self.config.quality_strong_threshold:
            return "strong"
        if score >= self.config.quality_moderate_threshold:
            return "moderate"
        if score >= self.config.quality_weak_threshold:
            return "weak"
        return "unreliable"

    @staticmethod
    def confidence_probability(confidence: int | float) -> float:
        return round(max(0.0, min(1.0, float(confidence) / 10.0)), 4)

    def calibration(self, confidence: int | float, outcome_status: str) -> dict[str, float | bool]:
        probability = self.confidence_probability(confidence)
        actual = {"SUCCESS": 1.0, "NEUTRAL": 0.5, "FAILED": 0.0}[outcome_status]
        error = abs(probability - actual)
        brier = (probability - actual) ** 2
        return {
            "probability": probability,
            "actual": actual,
            "calibration_error": round(error, 4),
            "brier_score": round(brier, 4),
            "score": bounded(100 * (1 - brier)),
            "overconfident": probability - actual >= 0.25,
            "underconfident": actual - probability >= 0.25,
        }

    @staticmethod
    def outcome_quality(statuses: Iterable[str]) -> float | None:
        values = [{"SUCCESS": 100.0, "NEUTRAL": 60.0, "FAILED": 10.0}[item] for item in statuses]
        return bounded(sum(values) / len(values)) if values else None

    @staticmethod
    def evidence_strength(
        *, source_count: int, item_count: int, author_count: int,
        highest_priority: int, conflict_count: int, supporting_factor_count: int,
        matched_entity_count: int, update_count: int,
    ) -> float:
        score = (
            min(25, source_count * 8)
            + min(15, item_count * 4)
            + min(10, author_count * 4)
            + min(15, max(0, highest_priority) * 1.5)
            + min(10, supporting_factor_count * 3)
            + min(15, matched_entity_count * 5)
            + min(10, update_count * 3)
            - min(35, conflict_count * 12)
        )
        return bounded(score)

    @staticmethod
    def source_diversity(source_count: int) -> float | None:
        return bounded(source_count / 4 * 100) if source_count else None

    def source_reliability(
        self, *, evaluated: int, successful: int, neutral: int, failed: int,
        successful_fetches: int, failed_fetches: int, duplicate_ratio: float,
        conflict_rate: float, average_ingestion_minutes: float | None,
        malformed_ratio: float = 0.0,
    ) -> tuple[float, bool]:
        prior_strength = 5.0
        outcome = (successful + 0.5 * neutral + 0.55 * prior_strength) / (
            max(0, evaluated) + prior_strength
        )
        fetch_total = successful_fetches + failed_fetches
        fetch = (successful_fetches + 0.9 * prior_strength) / (fetch_total + prior_strength)
        duplicate_value = 1 - min(1.0, abs(duplicate_ratio - 0.35) / 0.65)
        conflict = 1 - min(1.0, conflict_rate / 100)
        timely = (self.timeliness(average_ingestion_minutes) or 50) / 100
        base_reliability = (
            0.55 * outcome + 0.20 * fetch + 0.10 * duplicate_value
            + 0.10 * conflict + 0.05 * timely
        )
        reliability = 100 * (
            0.95 * base_reliability
            + 0.05 * (1 - min(1.0, malformed_ratio))
        )
        return bounded(reliability), evaluated >= self.config.quality_minimum_sample_size

    def timeliness(self, minutes: float | None) -> float | None:
        if minutes is None or not math.isfinite(minutes):
            return None
        value = max(0.0, minutes)
        excellent = self.config.quality_timeliness_excellent_minutes
        good = self.config.quality_timeliness_good_minutes
        weak = self.config.quality_timeliness_weak_minutes
        if value <= excellent:
            return 100.0
        if value <= good:
            return bounded(100 - (value - excellent) / (good - excellent) * 15)
        if value <= weak:
            return bounded(85 - (value - good) / (weak - good) * 45)
        return bounded(max(10, 40 * math.exp(-(value - weak) / weak)))

    @staticmethod
    def ai_agreement(analyses: list[Mapping[str, object]]) -> float | None:
        by_provider: dict[str, Mapping[str, object]] = {}
        for analysis in analyses:
            by_provider.setdefault(str(analysis["provider"]), analysis)
        if len(by_provider) < 2:
            return None
        ordered = sorted(by_provider.items(), key=lambda item: (item[0] != "mock", item[0]))
        left, right = ordered[0][1], ordered[1][1]
        action = 1.0 if left["action"] == right["action"] else 0.0
        risk = 1.0 if left["risk_level"] == right["risk_level"] else 0.0
        confidence = 1 - min(1.0, abs(float(left["confidence"]) - float(right["confidence"])) / 10)
        tokens = _jaccard(left["related_tokens_json"], right["related_tokens_json"])
        narratives = _jaccard(left["related_narratives_json"], right["related_narratives_json"])
        factors = _jaccard(left["supporting_factors_json"], right["supporting_factors_json"])
        return bounded(100 * (
            0.25 * action + 0.15 * risk + 0.20 * confidence
            + 0.15 * tokens + 0.15 * narratives + 0.10 * factors
        ))

    @staticmethod
    def noise_risk(
        *, outcome_status: str | None, maximum_movement: float | None,
        evidence_strength: float, source_count: int, conflict_count: int,
        watchlist_count: int, ai_confidence: int | None, eligible: bool,
    ) -> tuple[float, str]:
        if outcome_status == "FAILED":
            return bounded(85 + min(15, conflict_count * 5)), "confirmed_noise"
        if outcome_status == "NEUTRAL" and abs(maximum_movement or 0) < 3:
            return 55.0, "probable_noise"
        risk = 0.0
        risk += max(0, 55 - evidence_strength) * 0.7
        risk += 20 if source_count <= 1 else 0
        risk += min(25, conflict_count * 10)
        risk += 10 if watchlist_count == 0 else 0
        risk += 15 if ai_confidence is not None and ai_confidence < 5 else 0
        if outcome_status is None:
            if eligible and risk >= 50:
                return bounded(risk), "probable_noise"
            return bounded(risk), "unevaluated"
        return bounded(risk), "probable_noise" if risk >= 50 else "not_noise"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jaccard(left: object, right: object) -> float:
    def values(raw: object) -> set[str]:
        try:
            parsed = json.loads(str(raw or "[]"))
        except (TypeError, ValueError, json.JSONDecodeError):
            return set()
        return {str(item).casefold() for item in parsed} if isinstance(parsed, list) else set()
    left_set, right_set = values(left), values(right)
    if not left_set and not right_set:
        return 1.0
    return len(left_set & right_set) / max(1, len(left_set | right_set))
