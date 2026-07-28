from __future__ import annotations

import json
import logging
import statistics
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from app.config import Config
from app.db.database import Database
from app.events.bus import EventBus
from app.events.models import (
    QualityAggregateUpdated,
    QualityDegradationDetected,
    QualityImprovementDetected,
    QualityRecommendationCreated,
    SignalQualityCalculated,
)
from app.graph.models import normalize_entity
from app.quality.calculator import QualityScoreCalculator, bounded, utc_now
from app.quality.models import (
    CLASSIFICATIONS,
    QualityAggregate,
    QualityBreakdown,
    QualityRecommendation,
    SignalQualityScore,
)


logger = logging.getLogger("x_narrative_tracker")
ENTITY_TYPES = frozenset(
    {"overall", "signal_type", "source", "unified_event", "rule", "watchlist",
     "ai_provider", "ai_model", "narrative", "token", "graph_node"}
)


class SignalQualityService:
    def __init__(
        self, db: Database, config: Config, event_bus: EventBus | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.event_bus = event_bus
        self.calculator = QualityScoreCalculator(config)

    def calculate_signal(
        self, signal_id: int, *, version: int | None = None, publish: bool = True,
    ) -> SignalQualityScore:
        started = time.perf_counter()
        calculation_version = version or self.config.quality_calculation_version
        signal = self.db.get_signal_quality_context(signal_id)
        if signal is None:
            raise KeyError(signal_id)
        outcomes = self.db.get_signal_outcomes(limit=None, signal_id=signal_id)
        sources = self.db.get_signal_quality_sources(signal_id)
        rules = self.db.get_rule_matches(signal_id)
        watchlists = self.db.get_signal_watchlists(signal_id)
        analyses = self.db.get_signal_ai_analyses_for_signal(signal_id)
        latest_outcome = outcomes[0] if outcomes else None
        statuses = [str(row["status"]) for row in outcomes]
        source_reliability_values = [
            self.source_reliability(int(source["id"]))["reliability_score"]
            for source in sources
        ]
        source_reliability = (
            round(sum(source_reliability_values) / len(source_reliability_values), 2)
            if source_reliability_values else None
        )
        authors = {str(row["author"]).strip().casefold() for row in sources if str(row["author"] or "").strip()}
        supporting_count = sum(
            len(_json_list(row["supporting_factors_json"])) for row in analyses
        )
        matched_entities = int(bool(signal["token"])) + int(bool(signal["narrative"]))
        evidence_strength = self.calculator.evidence_strength(
            source_count=int(signal["source_count"] or len(sources) or 0),
            item_count=int(signal["item_count"] or 0),
            author_count=len(authors),
            highest_priority=int(signal["highest_source_priority"] or 0),
            conflict_count=int(signal["conflict_count"] or 0),
            supporting_factor_count=supporting_count,
            matched_entity_count=matched_entities,
            update_count=max(0, int(signal["material_version"] or 1) - 1),
        )
        calibration = (
            self.calculator.calibration(int(signal["confidence"]), str(latest_outcome["status"]))
            if latest_outcome else None
        )
        rule_precision = self._association_precision(
            {int(row["rule_id"]) for row in rules}, "rule"
        ) if rules else None
        watchlist_relevance = self._watchlist_relevance(watchlists, latest_outcome)
        timeliness, timing_details = self._signal_timeliness(signal, sources)
        eligible = self._eligible(signal["timestamp"])
        coverage = 100.0 if outcomes else 0.0 if eligible else None
        maximum_movement = (
            max(
                abs(float(latest_outcome["hype_change"])),
                abs(float(latest_outcome["momentum_change"])),
                abs(float(latest_outcome["mentions_change"])),
            ) if latest_outcome else None
        )
        noise_risk, noise_state = self.calculator.noise_risk(
            outcome_status=str(latest_outcome["status"]) if latest_outcome else None,
            maximum_movement=maximum_movement,
            evidence_strength=evidence_strength,
            source_count=int(signal["source_count"] or 0),
            conflict_count=int(signal["conflict_count"] or 0),
            watchlist_count=len(watchlists),
            ai_confidence=max((int(row["confidence"]) for row in analyses), default=None),
            eligible=eligible,
        )
        duplicate_value = self._duplicate_reduction_value(signal)
        breakdown = QualityBreakdown(
            outcome_quality=self.calculator.outcome_quality(statuses),
            confidence_calibration=float(calibration["score"]) if calibration else None,
            source_reliability=source_reliability,
            evidence_strength=evidence_strength,
            source_diversity=self.calculator.source_diversity(
                int(signal["source_count"] or len(sources) or 0)
            ),
            timeliness=timeliness,
            rule_precision=rule_precision,
            watchlist_relevance=watchlist_relevance,
            ai_agreement=self.calculator.ai_agreement([dict(row) for row in analyses]),
            duplicate_reduction_value=duplicate_value,
            noise_risk=noise_risk,
            evaluation_coverage=coverage,
            details={
                "outcome_count": len(outcomes), "source_count": len(sources),
                "author_count": len(authors), "rule_count": len(rules),
                "watchlist_count": len(watchlists), "analysis_count": len(analyses),
                "noise_state": noise_state, "eligible_for_evaluation": eligible,
                "confidence_probability": (
                    self.calculator.confidence_probability(signal["confidence"])
                ),
                "calibration": calibration, "timing": timing_details,
                "correlation_notice": "Quality measures tracker evidence, not investment performance.",
            },
        )
        evidence_count = (
            int(signal["item_count"] or 0) + len(outcomes) + len(rules)
            + len(watchlists) + len(analyses)
        )
        score, classification = self.calculator.calculate(breakdown, evidence_count)
        calculated_at = utc_now()
        values = {
            "signal_id": signal_id, "quality_score": score,
            "classification": classification,
            **{name: getattr(breakdown, name) for name in (
                "outcome_quality", "confidence_calibration", "source_reliability",
                "evidence_strength", "source_diversity", "timeliness",
                "rule_precision", "watchlist_relevance", "ai_agreement",
                "noise_risk", "evaluation_coverage",
            )},
            "evidence_count": evidence_count,
            "calculation_version": calculation_version,
            "breakdown_json": json.dumps(breakdown.as_dict(), sort_keys=True),
            "calculated_at": calculated_at,
        }
        quality_id = self.db.save_signal_quality_score(values)
        result = SignalQualityScore(
            quality_id, signal_id, score, classification, breakdown,
            evidence_count, calculation_version, calculated_at,
        )
        self._update_graph_quality(signal, result)
        logger.info(
            "Signal quality calculated signal_id=%s quality_score=%.2f "
            "classification=%s calculation_version=%s evidence_count=%s duration_ms=%s",
            signal_id, score, classification, calculation_version, evidence_count,
            int((time.perf_counter() - started) * 1000),
        )
        if publish:
            self._publish(SignalQualityCalculated(
                signal_id, score, classification, calculation_version
            ))
        return result

    def calculate_missing(self, *, version: int | None = None) -> int:
        calculation_version = version or self.config.quality_calculation_version
        created = 0
        for signal in reversed(self.db.get_signals(limit=None)):
            if self.db.get_signal_quality_score(int(signal["id"]), calculation_version) is None:
                self.calculate_signal(int(signal["id"]), version=calculation_version)
                created += 1
        return created

    def recalculate(
        self,
        *,
        signal_id: int | None = None,
        entity_type: str | None = None,
        entity_id: str | None = None,
        period_days: int = 30,
        version: int | None = None,
    ) -> dict[str, int]:
        ids = [signal_id] if signal_id else self._signal_ids(entity_type, entity_id)
        if not ids:
            ids = [int(row["id"]) for row in self.db.get_signals(limit=None)]
        cutoff = datetime.now(timezone.utc) - timedelta(days=period_days)
        processed = 0
        for identifier in dict.fromkeys(ids):
            row = self.db.get_signal(int(identifier))
            if row is not None and _timestamp(row["timestamp"]) >= cutoff:
                self.calculate_signal(int(identifier), version=version)
                processed += 1
        aggregates = 0
        if entity_type and entity_id:
            self.aggregate(entity_type, entity_id, period_days=period_days, version=version)
            aggregates = 1
        return {"signals": processed, "aggregates": aggregates}

    def aggregate(
        self,
        entity_type: str,
        entity_id: str,
        *,
        period_days: int = 30,
        version: int | None = None,
        period_end: datetime | None = None,
        publish: bool = True,
    ) -> QualityAggregate:
        if entity_type not in ENTITY_TYPES:
            raise ValueError("Unsupported quality entity type")
        calculation_version = version or self.config.quality_calculation_version
        end = (period_end or datetime.now(timezone.utc)).replace(microsecond=0)
        start = end - timedelta(days=period_days)
        ids = self._signal_ids(entity_type, entity_id)
        score_rows = self.db.get_signal_quality_scores(
            limit=None, calculation_version=calculation_version, signal_ids=ids
        )
        score_rows = [row for row in score_rows if start <= _timestamp(row["timestamp"]) < end]
        signals = [self.db.get_signal(int(row["signal_id"])) for row in score_rows]
        outcomes = []
        for row in score_rows:
            current = self.db.get_signal_outcomes(limit=1, signal_id=int(row["signal_id"]))
            if current:
                outcomes.append(current[0])
        statuses = Counter(str(row["status"]) for row in outcomes)
        qualities = [float(row["quality_score"]) for row in score_rows]
        evaluated = len(outcomes)
        decisive = statuses["SUCCESS"] + statuses["FAILED"]
        eligible = sum(self._eligible(row["timestamp"], now=end) for row in signals if row is not None)
        calibration_errors = []
        for row in score_rows:
            breakdown = _json_object(row["breakdown_json"])
            calibration = breakdown.get("details", {}).get("calibration")
            if calibration:
                calibration_errors.append(float(calibration["calibration_error"]))
        aggregate = QualityAggregate(
            entity_type=entity_type, entity_id=str(entity_id),
            period_start=start.isoformat(), period_end=end.isoformat(),
            signal_count=len(score_rows), evaluated_count=evaluated,
            successful_count=statuses["SUCCESS"], neutral_count=statuses["NEUTRAL"],
            failed_count=statuses["FAILED"],
            average_quality_score=round(statistics.mean(qualities), 2) if qualities else None,
            median_quality_score=round(statistics.median(qualities), 2) if qualities else None,
            precision=round(statuses["SUCCESS"] / decisive * 100, 2) if decisive else None,
            noise_rate=round(sum(float(row["noise_risk"]) >= 60 for row in score_rows) / len(score_rows) * 100, 2) if score_rows else None,
            evaluation_coverage=round(evaluated / eligible * 100, 2) if eligible else None,
            average_confidence=round(statistics.mean(float(row["confidence"]) for row in score_rows), 2) if score_rows else None,
            calibration_error=round(statistics.mean(calibration_errors), 4) if calibration_errors else None,
            reliability_score=self._aggregate_reliability(entity_type, entity_id),
            calculation_version=calculation_version,
            metrics=self._aggregate_metrics(entity_type, entity_id, score_rows),
        )
        values = aggregate.as_dict()
        values["metrics_json"] = json.dumps(values.pop("metrics"), sort_keys=True)
        values["calculated_at"] = utc_now()
        self.db.save_quality_aggregate(values)
        logger.debug(
            "Quality aggregate updated entity_type=%s entity_id=%s quality_score=%s "
            "calculation_version=%s signal_count=%s evaluated_count=%s",
            entity_type, entity_id, aggregate.average_quality_score,
            calculation_version, aggregate.signal_count, aggregate.evaluated_count,
        )
        if publish:
            self._publish(QualityAggregateUpdated(
                entity_type, str(entity_id), aggregate.average_quality_score,
                calculation_version,
            ))
        return aggregate

    def entity_report(
        self, entity_type: str, *, period_days: int = 30, minimum_sample: int = 0,
    ) -> list[dict[str, Any]]:
        rows = []
        for entity_id, label in self._entities(entity_type):
            aggregate = self.aggregate(entity_type, entity_id, period_days=period_days)
            if aggregate.signal_count < minimum_sample:
                continue
            rows.append({
                **aggregate.as_dict(), "label": label,
                "sample_sufficient": aggregate.evaluated_count >= self.config.quality_minimum_sample_size,
            })
        rows.sort(
            key=lambda row: (
                row["sample_sufficient"], row["average_quality_score"] or -1,
                row["evaluated_count"],
            ), reverse=True,
        )
        return rows

    def ai_report(self, period_days: int = 30) -> list[dict[str, Any]]:
        results = []
        for entity_type in ("ai_provider", "ai_model"):
            for row in self.entity_report(entity_type, period_days=period_days):
                analyses = self._analyses_for(entity_type, row["entity_id"])
                row.update({
                    "provider_or_model": row["label"],
                    "fallback_rate": _rate(sum(bool(item["fallback_used"]) for item in analyses), len(analyses)),
                    "cache_hit_rate": _rate(sum(bool(item["cached"]) for item in analyses), len(analyses)),
                    "average_latency_ms": round(
                        statistics.mean(float(item.get("latency_ms") or 0) for item in analyses), 2
                    ) if analyses else None,
                    "ranking_eligible": row["evaluated_count"] >= self.config.quality_minimum_sample_size,
                })
                results.append(row)
        return results

    def summary(self, period_days: int = 30) -> dict[str, Any]:
        self.calculate_missing()
        current = self.aggregate("overall", "all", period_days=period_days)
        comparison = self.compare_periods("overall", "all", period_days)
        return {
            **current.as_dict(),
            "classification": self.calculator.classify(
                current.average_quality_score or 0,
                sufficient=current.signal_count >= self.config.quality_minimum_evidence,
            ),
            "eligible_count": sum(
                self._eligible(row["timestamp"]) for row in self.db.get_signals(limit=None)
            ),
            "comparison": comparison,
            "quality_distribution": dict(Counter(
                row["classification"] for row in self.db.get_signal_quality_scores(
                    limit=None, calculation_version=self.config.quality_calculation_version
                )
            )),
            "quality_trend": self.history("overall", "all", periods=8, period_days=period_days),
            "top_sources": self.entity_report("source", period_days=period_days)[:5],
            "top_rules": self.entity_report("rule", period_days=period_days)[:5],
            "ai_comparison": self.ai_report(period_days),
            "open_recommendations": len(self.db.get_quality_recommendations("open")),
        }

    def compare_periods(self, entity_type: str, entity_id: str, period_days: int = 30) -> dict[str, Any]:
        end = datetime.now(timezone.utc)
        current = self.aggregate(entity_type, entity_id, period_days=period_days, period_end=end, publish=False)
        previous = self.aggregate(
            entity_type, entity_id, period_days=period_days,
            period_end=end - timedelta(days=period_days), publish=False,
        )
        fields = {
            "quality_score": (current.average_quality_score, previous.average_quality_score, False),
            "precision": (current.precision, previous.precision, False),
            "noise_rate": (current.noise_rate, previous.noise_rate, True),
            "evaluation_coverage": (current.evaluation_coverage, previous.evaluation_coverage, False),
            "calibration_error": (current.calibration_error, previous.calibration_error, True),
            "source_reliability": (current.reliability_score, previous.reliability_score, False),
            "rule_quality": (
                current.metrics.get("average_rule_precision"),
                previous.metrics.get("average_rule_precision"), False,
            ),
            "ai_quality": (
                current.metrics.get("average_ai_agreement"),
                previous.metrics.get("average_ai_agreement"), False,
            ),
        }
        result: dict[str, Any] = {}
        overall_change = None
        for name, (current_value, previous_value, inverse) in fields.items():
            if current_value is None or previous_value is None:
                result[name] = {"current": current_value, "previous": previous_value, "change": None, "classification": "insufficient_data"}
                continue
            change = round(float(current_value) - float(previous_value), 2)
            effective = -change if inverse else change
            classification = (
                "improved" if effective >= self.config.quality_change_significance
                else "degraded" if effective <= -self.config.quality_change_significance
                else "stable"
            )
            result[name] = {"current": current_value, "previous": previous_value, "change": change, "classification": classification}
            if name == "quality_score":
                overall_change = change
        if overall_change is not None:
            event = (
                QualityImprovementDetected(entity_type, entity_id, overall_change)
                if overall_change >= self.config.quality_change_significance
                else QualityDegradationDetected(entity_type, entity_id, overall_change)
                if overall_change <= -self.config.quality_change_significance else None
            )
            if event:
                self._publish(event)
        return result

    def history(
        self, entity_type: str, entity_id: str, *, periods: int = 8, period_days: int = 7,
    ) -> list[dict[str, Any]]:
        end = datetime.now(timezone.utc)
        values = []
        for offset in reversed(range(periods)):
            period_end = end - timedelta(days=period_days * offset)
            values.append(self.aggregate(
                entity_type, entity_id, period_days=period_days,
                period_end=period_end, publish=False,
            ).as_dict())
        return values

    def generate_recommendations(self, period_days: int = 30) -> list[dict[str, Any]]:
        period_key = f"{period_days}d:{datetime.now(timezone.utc).date().isoformat()}"
        candidates: list[QualityRecommendation] = []
        entity_groups = (
            ("overall", [{**self.aggregate("overall", "all", period_days=period_days).as_dict(), "entity_id": "all"}]),
            ("source", self.entity_report("source", period_days=period_days)),
            ("rule", self.entity_report("rule", period_days=period_days)),
            ("watchlist", self.entity_report("watchlist", period_days=period_days)),
            ("ai_provider", [row for row in self.ai_report(period_days) if row["entity_type"] == "ai_provider"]),
        )
        for entity_type, rows in entity_groups:
            for row in rows:
                entity_id = str(row["entity_id"])
                if (row.get("evaluation_coverage") or 0) < 60:
                    candidates.append(self._recommendation(
                        entity_type, entity_id, "collect_outcome_data", "medium",
                        "Collect more outcome data",
                        "Evaluation coverage is too low for reliable quality comparisons.",
                        "Allow eligible signals to complete configured outcome windows.",
                        {"evaluation_coverage": row.get("evaluation_coverage"), "signal_count": row.get("signal_count")},
                    ))
                if row.get("noise_rate") is not None and row["noise_rate"] >= 40:
                    candidates.append(self._recommendation(
                        entity_type, entity_id, "reduce_noise", "high",
                        "Review noisy signal generation",
                        "A large share of signals has confirmed or probable noise indicators.",
                        "Review confidence, evidence, source-count, and cooldown thresholds.",
                        {"noise_rate": row["noise_rate"], "evaluated_count": row.get("evaluated_count")},
                    ))
                if row.get("calibration_error") is not None and row["calibration_error"] >= 0.25:
                    candidates.append(self._recommendation(
                        entity_type, entity_id, "review_overconfidence", "medium",
                        "Review confidence calibration",
                        "Saved confidence differs materially from evaluated outcomes.",
                        "Review confidence thresholds without rewriting historical values.",
                        {"calibration_error": row["calibration_error"]},
                    ))
                if entity_type == "source" and row.get("reliability_score") is not None and row["reliability_score"] < 45:
                    candidates.append(self._recommendation(
                        entity_type, entity_id, "investigate_source", "high",
                        "Investigate source reliability",
                        "Outcome, fetch, conflict, duplicate, and timeliness evidence produced low reliability.",
                        "Review source health, conflicts, and source priority.",
                        {"reliability_score": row["reliability_score"]},
                    ))
                if entity_type == "watchlist" and row.get("signal_count", 0) >= 20 and (row.get("average_quality_score") or 0) < 55:
                    candidates.append(self._recommendation(
                        entity_type, entity_id, "split_broad_watchlist", "medium",
                        "Split an overly broad watchlist",
                        "The watchlist matches frequently but produces weak average quality.",
                        "Separate unrelated tokens or narratives into focused watchlists.",
                        {"signal_count": row["signal_count"], "average_quality": row.get("average_quality_score")},
                    ))
        for candidate in candidates:
            values = candidate.as_dict()
            values["evidence_json"] = json.dumps(values.pop("evidence"), sort_keys=True)
            values["period_key"] = period_key
            values.pop("id", None)
            values.pop("status", None)
            recommendation_id, created = self.db.save_quality_recommendation(values)
            if created:
                logger.info(
                    "Quality recommendation created entity_type=%s entity_id=%s "
                    "recommendation_type=%s severity=%s recommendation_id=%s",
                    candidate.entity_type, candidate.entity_id,
                    candidate.recommendation_type, candidate.severity,
                    recommendation_id,
                )
                self._publish(QualityRecommendationCreated(
                    recommendation_id, candidate.entity_type,
                    candidate.entity_id, candidate.severity,
                ))
        return [dict(row) for row in self.db.get_quality_recommendations()]

    def recommendations(self, status: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.get_quality_recommendations(status, limit)]

    def update_recommendation(self, recommendation_id: int, status: str) -> bool:
        return self.db.update_quality_recommendation_status(recommendation_id, status)

    def validate(self) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        version = self.config.quality_calculation_version
        scored = {int(row["signal_id"]) for row in self.db.get_signal_quality_scores(limit=None, calculation_version=version)}
        for signal in self.db.get_signals(limit=None):
            if self._eligible(signal["timestamp"]) and int(signal["id"]) not in scored:
                issues.append({"type": "missing_eligible_score", "signal_id": signal["id"]})
        duplicates = self.db.connection.execute(
            """SELECT signal_id, calculation_version, COUNT(*) AS count
            FROM signal_quality_scores GROUP BY signal_id, calculation_version HAVING COUNT(*) > 1"""
        ).fetchall()
        issues.extend({"type": "duplicate_score", "signal_id": row["signal_id"]} for row in duplicates)
        for row in self.db.get_signal_quality_scores(limit=None):
            if not 0 <= float(row["quality_score"]) <= 100:
                issues.append({"type": "score_out_of_range", "id": row["id"]})
            if row["classification"] not in CLASSIFICATIONS:
                issues.append({"type": "invalid_classification", "id": row["id"]})
            breakdown = _json_object(row["breakdown_json"])
            if not breakdown or "details" not in breakdown:
                issues.append({"type": "missing_breakdown", "id": row["id"]})
            else:
                required = set(QualityBreakdown.__dataclass_fields__)
                missing = sorted(required - set(breakdown))
                if missing:
                    issues.append({
                        "type": "missing_breakdown_values", "id": row["id"],
                        "fields": missing,
                    })
        for row in self.db.get_quality_aggregates(limit=5000):
            if int(row["evaluated_count"]) != int(row["successful_count"]) + int(row["neutral_count"]) + int(row["failed_count"]):
                issues.append({"type": "aggregate_count_inconsistency", "id": row["id"]})
            if _timestamp(row["period_start"]) >= _timestamp(row["period_end"]):
                issues.append({"type": "invalid_period_range", "id": row["id"]})
            for field in ("precision", "noise_rate", "evaluation_coverage", "reliability_score"):
                if row[field] is not None and not 0 <= float(row[field]) <= 100:
                    issues.append({"type": f"{field}_out_of_range", "id": row["id"]})
            if _timestamp(row["calculated_at"]) < _timestamp(row["period_end"]) - timedelta(days=1):
                issues.append({"type": "stale_aggregate", "id": row["id"]})
        known_entities = {
            entity_type: {entity_id for entity_id, _ in self._entities(entity_type)}
            for entity_type in ENTITY_TYPES if entity_type != "overall"
        }
        for row in self.db.get_quality_recommendations(limit=5000):
            entity_type = str(row["entity_type"])
            if entity_type == "overall":
                valid = str(row["entity_id"]) == "all"
            else:
                valid = str(row["entity_id"]) in known_entities.get(entity_type, set())
            if not valid:
                issues.append({
                    "type": "recommendation_missing_entity", "id": row["id"],
                    "entity_type": entity_type, "entity_id": row["entity_id"],
                })
        return issues

    def source_reliability(self, source_id: int) -> dict[str, Any]:
        row = self.db.get_source_quality_statistics(source_id)
        if row is None:
            raise KeyError(source_id)
        fetched = int(row["total_items_fetched"] or 0)
        duplicate_ratio = int(row["total_items_deduplicated"] or 0) / fetched if fetched else 0
        content_count = int(row["content_item_count"] or 0)
        malformed_ratio = int(row["malformed_count"] or 0) / content_count if content_count else 0
        score, sufficient = self.calculator.source_reliability(
            evaluated=int(row["evaluated_count"] or 0),
            successful=int(row["successful_count"] or 0),
            neutral=int(row["neutral_count"] or 0), failed=int(row["failed_count"] or 0),
            successful_fetches=int(row["successful_fetches"] or 0),
            failed_fetches=int(row["failed_fetches"] or 0),
            duplicate_ratio=duplicate_ratio,
            conflict_rate=float(row["conflict_rate"] or 0),
            average_ingestion_minutes=(
                float(row["average_ingestion_minutes"])
                if row["average_ingestion_minutes"] is not None else None
            ),
            malformed_ratio=malformed_ratio,
        )
        return {
            **dict(row), "reliability_score": score,
            "sample_sufficient": sufficient, "duplicate_ratio": round(duplicate_ratio, 4),
            "malformed_ratio": round(malformed_ratio, 4),
        }

    def _signal_timeliness(self, signal: Mapping[str, Any], sources: list[Mapping[str, Any]]) -> tuple[float | None, dict[str, Any]]:
        ingestion = []
        for source in sources:
            if source["published_at"]:
                ingestion.append(max(0, (_timestamp(source["fetched_at"]) - _timestamp(source["published_at"])).total_seconds() / 60))
        alert_delay = None
        if signal["event_first_seen_at"]:
            alert_delay = max(0, (_timestamp(signal["timestamp"]) - _timestamp(signal["event_first_seen_at"])).total_seconds() / 60)
        values = [*ingestion, *([alert_delay] if alert_delay is not None else [])]
        minutes = statistics.mean(values) if values else None
        return self.calculator.timeliness(minutes), {
            "average_ingestion_delay_minutes": round(statistics.mean(ingestion), 2) if ingestion else None,
            "alert_delay_minutes": round(alert_delay, 2) if alert_delay is not None else None,
        }

    @staticmethod
    def _duplicate_reduction_value(signal: Mapping[str, Any]) -> float | None:
        items = int(signal["item_count"] or 0)
        duplicates = int(signal["duplicate_count"] or 0)
        if not items and not duplicates:
            return None
        ratio = duplicates / max(1, items + duplicates)
        return bounded(ratio * 100)

    def _association_precision(self, identifiers: set[int], entity_type: str) -> float | None:
        statuses = []
        if entity_type == "rule":
            matches = [row for row in self.db.get_rule_matches() if int(row["rule_id"]) in identifiers]
            signal_ids = {int(row["signal_id"]) for row in matches}
        else:
            signal_ids = set()
            for identifier in identifiers:
                signal_ids.update(self._signal_ids(entity_type, str(identifier)))
        for signal_id in signal_ids:
            outcomes = self.db.get_signal_outcomes(limit=1, signal_id=signal_id)
            if outcomes:
                statuses.append(str(outcomes[0]["status"]))
        decisive = sum(status in {"SUCCESS", "FAILED"} for status in statuses)
        return round(sum(status == "SUCCESS" for status in statuses) / decisive * 100, 2) if decisive else None

    @staticmethod
    def _watchlist_relevance(watchlists: list[Mapping[str, Any]], outcome: Mapping[str, Any] | None) -> float | None:
        if not watchlists:
            return None
        base = min(80, 45 + len(watchlists) * 10 + max(int(row["priority"]) for row in watchlists) * 0.25)
        if outcome:
            base += {"SUCCESS": 20, "NEUTRAL": 5, "FAILED": -20}[str(outcome["status"])]
        return bounded(base)

    def _eligible(self, timestamp: object, *, now: datetime | None = None) -> bool:
        current = now or datetime.now(timezone.utc)
        return _timestamp(timestamp) <= current - timedelta(hours=min(self.config.outcome_evaluation_windows))

    def _signal_ids(self, entity_type: str | None, entity_id: str | None) -> list[int]:
        signals = self.db.get_signals(limit=None)
        if not entity_type or entity_type == "overall":
            return [int(row["id"]) for row in signals]
        if entity_type == "signal_type":
            return [int(row["id"]) for row in signals if str(row["signal_type"]) == str(entity_id)]
        if entity_type == "unified_event":
            return [
                int(row["id"]) for row in signals
                if str(row["unified_event_id"] or "") == str(entity_id)
            ]
        if entity_type in {"token", "narrative"}:
            return [int(row["id"]) for row in signals if str(row[entity_type] or "").casefold() == str(entity_id).casefold()]
        if entity_type == "rule":
            return [int(row["signal_id"]) for row in self.db.get_rule_matches() if str(row["rule_id"]) == str(entity_id)]
        if entity_type == "watchlist":
            return [int(row["id"]) for row in self.db.get_watchlist_signals(int(entity_id), limit=None)]
        if entity_type == "source":
            result = []
            for signal in signals:
                if any(str(row["id"]) == str(entity_id) for row in self.db.get_signal_quality_sources(int(signal["id"]))):
                    result.append(int(signal["id"]))
            return result
        if entity_type in {"ai_provider", "ai_model"}:
            field = "provider" if entity_type == "ai_provider" else "model"
            return [int(row["signal_id"]) for row in self.db.get_signal_ai_analyses(limit=100000) if str(row[field]) == str(entity_id)]
        if entity_type == "graph_node":
            node_type, _, value = str(entity_id).partition(":")
            return self._signal_ids(node_type, value) if node_type in {"token", "narrative"} else []
        raise ValueError("Unsupported quality entity type")

    def signal_ids(self, entity_type: str | None, entity_id: str | None) -> list[int]:
        """Return signals associated with a supported quality entity."""
        return self._signal_ids(entity_type, entity_id)

    def _entities(self, entity_type: str) -> list[tuple[str, str]]:
        if entity_type == "overall":
            return [("all", "All signals")]
        if entity_type == "signal_type":
            return sorted({(str(row["signal_type"]), str(row["signal_type"])) for row in self.db.get_signals(limit=None)})
        if entity_type in {"token", "narrative"}:
            return sorted({(str(row[entity_type]), str(row[entity_type])) for row in self.db.get_signals(limit=None) if row[entity_type]}, key=lambda item: item[1].casefold())
        if entity_type == "source":
            return [(str(row["id"]), str(row["name"])) for row in self.db.get_content_sources()]
        if entity_type == "unified_event":
            return [
                (str(row["id"]), str(row["title"]))
                for row in self.db.get_unified_events(limit=None)
            ]
        if entity_type == "rule":
            return [(str(row["id"]), str(row["name"])) for row in self.db.get_alert_rules()]
        if entity_type == "watchlist":
            return [(str(row["id"]), str(row["name"])) for row in self.db.get_watchlists()]
        if entity_type in {"ai_provider", "ai_model"}:
            field = "provider" if entity_type == "ai_provider" else "model"
            return sorted({(str(row[field]), str(row[field])) for row in self.db.get_signal_ai_analyses(limit=100000)})
        if entity_type == "graph_node":
            return [(f"{row['node_type']}:{row['entity_id']}", str(row["label"])) for row in self.db.get_graph_nodes(limit=None) if row["node_type"] in {"token", "narrative"}]
        raise ValueError("Unsupported quality entity type")

    def _aggregate_reliability(self, entity_type: str, entity_id: str) -> float | None:
        if entity_type == "source":
            return float(self.source_reliability(int(entity_id))["reliability_score"])
        return None

    def _aggregate_metrics(self, entity_type: str, entity_id: str, rows: list[Mapping[str, Any]]) -> dict[str, Any]:
        signal_ids = {int(row["signal_id"]) for row in rows}
        source_ids, rule_ids, watchlist_ids = set(), set(), set()
        tokens, narratives = set(), set()
        for row in rows:
            signal_id = int(row["signal_id"])
            source_ids.update(int(item["id"]) for item in self.db.get_signal_quality_sources(signal_id))
            rule_ids.update(int(item["rule_id"]) for item in self.db.get_rule_matches(signal_id))
            watchlist_ids.update(int(item["id"]) for item in self.db.get_signal_watchlists(signal_id))
            if row["token"]: tokens.add(str(row["token"]))
            if row["narrative"]: narratives.add(str(row["narrative"]))
        graph = self._graph_metrics(entity_type, entity_id)
        breakdowns = [_json_object(row["breakdown_json"]) for row in rows]
        rule_precision_values = [
            float(item["rule_precision"]) for item in breakdowns
            if item.get("rule_precision") is not None
        ]
        ai_agreement_values = [
            float(item["ai_agreement"]) for item in breakdowns
            if item.get("ai_agreement") is not None
        ]
        calibration_values = [
            item.get("details", {}).get("calibration") for item in breakdowns
            if item.get("details", {}).get("calibration")
        ]
        metrics = {
            "source_diversity": len(source_ids), "rule_diversity": len(rule_ids),
            "watchlist_coverage": _rate(sum(bool(self.db.get_signal_watchlists(identifier)) for identifier in signal_ids), len(signal_ids)),
            "token_diversity": len(tokens), "narrative_diversity": len(narratives),
            "graph_centrality": graph.get("weighted_degree"),
            "emerging_relationship_count": graph.get("emerging_relationship_count", 0),
            "correlation_notice": "Entity quality describes tracker signals, not expected investment returns.",
            "average_rule_precision": (
                round(statistics.mean(rule_precision_values), 2)
                if rule_precision_values else None
            ),
            "average_ai_agreement": (
                round(statistics.mean(ai_agreement_values), 2)
                if ai_agreement_values else None
            ),
            "overconfidence_rate": _rate(
                sum(bool(item["overconfident"]) for item in calibration_values),
                len(calibration_values),
            ),
            "underconfidence_rate": _rate(
                sum(bool(item["underconfident"]) for item in calibration_values),
                len(calibration_values),
            ),
        }
        if rows:
            metrics.update({
                "average_hype": round(statistics.mean(float(row["hype_score"]) for row in rows), 2),
                "average_momentum": round(statistics.mean(float(row["momentum_score"]) for row in rows), 2),
                "average_success_confidence": _mean_or_none(
                    float(row["confidence"]) for row in rows
                    if row["outcome_status"] == "SUCCESS"
                ),
                "average_failed_confidence": _mean_or_none(
                    float(row["confidence"]) for row in rows
                    if row["outcome_status"] == "FAILED"
                ),
            })
        if entity_type == "rule":
            overlap = sum(
                len(self.db.get_rule_matches(int(row["signal_id"]))) > 1 for row in rows
            )
            metrics.update({
                "duplicate_alert_prevention_count": None,
                "watchlist_overlap": _rate(
                    sum(bool(self.db.get_signal_watchlists(int(row["signal_id"]))) for row in rows),
                    len(rows),
                ),
                "other_rule_overlap": _rate(overlap, len(rows)),
            })
        if entity_type == "watchlist":
            overlap = sum(
                len(self.db.get_signal_watchlists(int(row["signal_id"]))) > 1 for row in rows
            )
            metrics["rule_overlap"] = _rate(
                sum(bool(self.db.get_rule_matches(int(row["signal_id"]))) for row in rows),
                len(rows),
            )
            metrics["other_watchlist_overlap"] = _rate(overlap, len(rows))
        return metrics

    def _graph_metrics(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        if entity_type not in {"token", "narrative", "graph_node"} or not self.db.has_table("graph_nodes"):
            return {}
        node_type, value = (entity_type, entity_id)
        if entity_type == "graph_node":
            node_type, _, value = entity_id.partition(":")
        canonical, _, _ = normalize_entity(node_type, value)
        node = self.db.get_graph_node(node_type, canonical)
        if node is None:
            return {}
        metadata = _json_object(node["metadata_json"])
        edges = self.db.get_graph_edges(node_id=int(node["id"]), min_weight=0, limit=None)
        return {
            **metadata,
            "emerging_relationship_count": sum(
                float(_json_object(row["metadata_json"]).get("previous_occurrences", row["occurrence_count"]))
                < float(row["occurrence_count"]) for row in edges
            ),
        }

    def _update_graph_quality(self, signal: Mapping[str, Any], score: SignalQualityScore) -> None:
        if not self.db.has_table("graph_nodes"):
            return
        for node_type in ("token", "narrative"):
            if not signal[node_type]:
                continue
            canonical, _, _ = normalize_entity(node_type, signal[node_type])
            node = self.db.get_graph_node(node_type, canonical)
            if node is None:
                continue
            metadata = _json_object(node["metadata_json"])
            ids = self._signal_ids(node_type, str(signal[node_type]))
            scores = self.db.get_signal_quality_scores(limit=None, signal_ids=ids)
            average = statistics.mean(float(row["quality_score"]) for row in scores) if scores else score.quality_score
            noise = _rate(sum(float(row["noise_risk"]) >= 60 for row in scores), len(scores))
            trend = round(score.quality_score - float(metadata.get("average_signal_quality", score.quality_score)), 2)
            edges = self.db.get_graph_edges(node_id=int(node["id"]), min_weight=0, limit=None)
            metadata.update({
                "average_signal_quality": round(average, 2), "quality_trend": trend,
                "noise_rate": noise, "evaluated_event_count": sum(row["evaluation_coverage"] == 100 for row in scores),
                "reliability_adjusted_weight": round(float(node["weight"]) * average / 100, 6),
            })
            self.db.upsert_graph_node(
                node_type=node_type, entity_id=str(node["entity_id"]), label=str(node["label"]),
                normalized_label=str(node["normalized_label"]), weight=float(node["weight"]),
                activity_score=float(node["activity_score"]), first_seen_at=str(node["first_seen_at"]),
                last_seen_at=str(node["last_seen_at"]), metadata_json=json.dumps(metadata, sort_keys=True),
            )
            for edge in edges:
                edge_metadata = _json_object(edge["metadata_json"])
                edge_metadata["average_signal_quality"] = round(average, 2)
                edge_metadata["reliability_adjusted_weight"] = round(float(edge["weight"]) * average / 100, 6)
                edge_metadata["quality_trend"] = trend
                edge_metadata["evaluated_event_count"] = sum(
                    row["evaluation_coverage"] == 100 for row in scores
                )
                edge_metadata["noise_rate"] = noise
                self.db.update_graph_edge(
                    int(edge["id"]), weight=float(edge["weight"]), confidence=float(edge["confidence"]),
                    last_seen_at=str(edge["last_seen_at"]), metadata_json=json.dumps(edge_metadata, sort_keys=True),
                )

    def _analyses_for(self, entity_type: str, entity_id: str) -> list[dict[str, Any]]:
        field = "provider" if entity_type == "ai_provider" else "model"
        return [dict(row) for row in self.db.get_signal_ai_analyses(limit=100000) if str(row[field]) == entity_id]

    def _recommendation(
        self, entity_type: str, entity_id: str, kind: str, severity: str,
        title: str, description: str, action: str, evidence: dict[str, Any],
    ) -> QualityRecommendation:
        return QualityRecommendation(
            None, entity_type, entity_id, kind, severity, title, description,
            action, confidence=80.0,
            minimum_sample_requirement=self.config.quality_minimum_sample_size,
            evidence=evidence,
        )

    def _publish(self, event: object) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(event)


def format_quality_summary(report: Mapping[str, Any]) -> str:
    comparison = report.get("comparison", {}).get("quality_score", {})
    change = comparison.get("change")
    return (
        "Signal Quality Summary\n\n"
        f"Period: Last {int((_timestamp(report['period_end']) - _timestamp(report['period_start'])).days)} days\n\n"
        f"Signals: {report['signal_count']}\n"
        f"Eligible for evaluation: {report['eligible_count']}\n"
        f"Evaluated: {report['evaluated_count']}\n"
        f"Evaluation coverage: {_display(report.get('evaluation_coverage'), '%')}\n\n"
        f"Average quality: {_display(report.get('average_quality_score'))}\n"
        f"Median quality: {_display(report.get('median_quality_score'))}\n"
        f"Classification: {str(report['classification']).replace('_', ' ').title()}\n"
        f"Period change: {_display(change)}\n\n"
        f"Successful: {report['successful_count']}\nNeutral: {report['neutral_count']}\n"
        f"Failed: {report['failed_count']}\n\n"
        f"Precision: {_display(report.get('precision'), '%')}\n"
        f"Noise rate: {_display(report.get('noise_rate'), '%')}\n"
        f"Calibration error: {_display(report.get('calibration_error'))}\n"
        f"Open recommendations: {report['open_recommendations']}"
    )


def _timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _json_list(value: object) -> list[Any]:
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _json_object(value: object) -> dict[str, Any]:
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator * 100, 2) if denominator else None


def _mean_or_none(values: Iterable[float]) -> float | None:
    items = list(values)
    return round(statistics.mean(items), 2) if items else None


def _display(value: object, suffix: str = "") -> str:
    return "collecting history" if value is None else f"{float(value):.2f}{suffix}"
