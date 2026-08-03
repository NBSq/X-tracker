from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import replace

import json
import logging
from collections.abc import Mapping
from typing import Any

from app.alerts.telegram import TelegramAlerter
from app.db.database import Database
from app.events.bus import EventBus
from app.events.models import RuleTriggered, SignalCreated
from app.rules.models import (
    AlertRule,
    RuleEvaluation,
    RuleValidationError,
    SignalFacts,
    condition_uses_ai,
    condition_uses_quality,
    evaluate_condition,
    normalize_actions,
    validate_condition,
)
from app.observability.metrics import metrics
from app.observability.timing import timed


logger = logging.getLogger("x_narrative_tracker")


class RuleService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_rules(self, enabled: bool | None = None) -> list[AlertRule]:
        rows = self.db.get_alert_rules(enabled)
        if not isinstance(rows, (list, tuple)):
            return []
        return [AlertRule.from_row(row) for row in rows]

    def get_rule(self, rule_id: int) -> AlertRule | None:
        row = self.db.get_alert_rule(rule_id)
        return AlertRule.from_row(row) if row is not None else None

    def create_rule(
        self,
        name: str,
        condition: dict[str, Any],
        actions: str | list[str] | tuple[str, ...],
        *,
        enabled: bool = True,
        priority: int = 0,
    ) -> AlertRule:
        normalized_name = _rule_name(name)
        validate_condition(condition)
        normalized_actions = normalize_actions(actions)
        rule_id = self.db.create_alert_rule(
            normalized_name,
            enabled,
            priority,
            condition,
            normalized_actions,
        )
        rule = self.get_rule(rule_id)
        if rule is None:
            raise RuntimeError("Created rule could not be loaded")
        return rule

    def update_rule(self, rule_id: int, **changes: Any) -> AlertRule:
        existing = self.get_rule(rule_id)
        if existing is None:
            raise KeyError(rule_id)
        allowed = {"name", "enabled", "priority", "condition", "action", "actions"}
        unknown = set(changes) - allowed
        if unknown:
            raise RuleValidationError(f"Unsupported rule fields: {', '.join(sorted(unknown))}")
        values: dict[str, Any] = {}
        if "name" in changes:
            values["name"] = _rule_name(changes["name"])
        if "enabled" in changes:
            values["enabled"] = bool(changes["enabled"])
        if "priority" in changes:
            values["priority"] = int(changes["priority"])
        if "condition" in changes:
            validate_condition(changes["condition"])
            values["condition"] = changes["condition"]
        action_value = changes.get("actions", changes.get("action"))
        if action_value is not None:
            values["action"] = normalize_actions(action_value)
        if values:
            self.db.update_alert_rule(rule_id, **values)
        updated = self.get_rule(rule_id)
        if updated is None:
            raise RuntimeError("Updated rule could not be loaded")
        return updated

    def delete_rule(self, rule_id: int) -> bool:
        return self.db.delete_alert_rule(rule_id)

    def set_enabled(self, rule_id: int, enabled: bool) -> AlertRule:
        return self.update_rule(rule_id, enabled=enabled)

    def test_rule(
        self,
        rule_id: int,
        facts: SignalFacts | Mapping[str, Any],
    ) -> RuleEvaluation:
        rule = self.get_rule(rule_id)
        if rule is None:
            raise KeyError(rule_id)
        signal_facts = (
            facts if isinstance(facts, SignalFacts) else SignalFacts.from_mapping(facts)
        )
        return RuleEvaluation(
            rule=rule,
            matched=evaluate_condition(rule.condition, signal_facts),
            facts=signal_facts,
        )


class RuleEngine:
    """Evaluate enabled rules whenever a SignalCreated event is published."""

    def __init__(
        self,
        db: Database,
        telegram: TelegramAlerter | None = None,
        rule_scope: str = "all",
        event_bus: EventBus | None = None,
    ) -> None:
        self.db = db
        self.telegram = telegram
        self.rules = RuleService(db)
        self.rule_scope = rule_scope
        self.event_bus = event_bus

    def __call__(self, event: SignalCreated) -> None:
        enabled_rules = self.rules.list_rules(enabled=True)
        if not enabled_rules:
            return
        signal_id = self.db.find_signal_history_id(event.history_record())
        if signal_id is None:
            logger.warning("Rule evaluation skipped: signal history row was not found")
            return
        self.evaluate_saved_signal(signal_id)

    @timed("rule_evaluation")
    def evaluate_saved_signal(self, signal_id: int) -> None:
        enabled_rules = self.rules.list_rules(enabled=True)
        if not enabled_rules:
            return
        signal = self.db.get_signal(signal_id)
        if signal is None:
            return
        success_rate = self.db.get_entity_outcome_success_rate(
            signal["token"],
            signal["narrative"],
        )
        watchlist_context = self.db.get_signal_watchlist_context(signal_id)
        analysis = self.db.get_signal_ai_analysis(signal_id)
        graph_metrics = _signal_graph_metrics(
            self.db, signal["token"], signal["narrative"]
        )
        quality = self.db.get_signal_quality_score(signal_id)
        quality_metrics = {
            "signal_quality_score": float(quality["quality_score"]) if quality else 0.0,
            "quality_classification": str(quality["classification"]) if quality else None,
            "source_reliability": float(quality["source_reliability"] or 0) if quality else 0.0,
            "evidence_strength": float(quality["evidence_strength"] or 0) if quality else 0.0,
            "confidence_calibration": float(quality["confidence_calibration"] or 0) if quality else 0.0,
            "noise_risk": float(quality["noise_risk"] or 0) if quality else 0.0,
            "evaluation_coverage": float(quality["evaluation_coverage"] or 0) if quality else 0.0,
        }
        unified_event = self.db.get_signal_unified_event(signal_id)
        event_age_minutes = 0.0
        if unified_event is not None:
            try:
                first_seen = datetime.fromisoformat(
                    str(unified_event["first_seen_at"]).replace("Z", "+00:00")
                )
                if first_seen.tzinfo is None:
                    first_seen = first_seen.replace(tzinfo=timezone.utc)
                event_age_minutes = max(
                    0.0,
                    (datetime.now(timezone.utc) - first_seen).total_seconds() / 60,
                )
            except ValueError:
                pass
        facts = SignalFacts(
            token=signal["token"],
            narrative=signal["narrative"],
            hype_score=float(signal["hype_score"]),
            momentum_score=float(signal["momentum_score"]),
            confidence=int(signal["confidence"]),
            mentions=int(signal["mentions_count"] or 0),
            outcome_success_rate=success_rate,
            watchlists=tuple(watchlist_context["names"]),
            watchlist_ids=tuple(watchlist_context["ids"]),
            watchlist_priority=int(watchlist_context["highest_priority"]),
            matched_watchlist=bool(watchlist_context["matched_any"]),
            ai_action=str(analysis["action"]) if analysis is not None else None,
            ai_confidence=int(analysis["confidence"]) if analysis is not None else 0,
            ai_risk_level=(
                str(analysis["risk_level"]) if analysis is not None else None
            ),
            openai_analysis_available=(
                analysis is not None and str(analysis["provider"]) == "openai"
            ),
            ai_fallback_used=(
                bool(analysis["fallback_used"]) if analysis is not None else False
            ),
            source_count=int(unified_event["source_count"]) if unified_event else 0,
            item_count=int(unified_event["item_count"]) if unified_event else 0,
            source_priority=(
                int(unified_event["highest_source_priority"]) if unified_event else 0
            ),
            event_age_minutes=event_age_minutes,
            materially_updated=(
                int(unified_event["material_version"]) > 1 if unified_event else False
            ),
            duplicate_count=(
                int(unified_event["duplicate_count"]) if unified_event else 0
            ),
            conflict_count=(
                int(unified_event["conflict_count"]) if unified_event else 0
            ),
            requires_review=bool(unified_event["requires_review"]) if unified_event else False,
            **graph_metrics,
            **quality_metrics,
        )
        for rule in enabled_rules:
            uses_ai = condition_uses_ai(rule.condition)
            uses_quality = condition_uses_quality(rule.condition)
            if self.rule_scope == "non_ai" and (uses_ai or uses_quality):
                continue
            if self.rule_scope == "ai" and not uses_ai:
                continue
            if self.rule_scope == "quality" and not uses_quality:
                continue
            metrics.increment("rule_evaluations_total")
            rule_facts = replace(facts, rule_priority=rule.priority)
            if not evaluate_condition(rule.condition, rule_facts):
                continue
            created = self.db.save_rule_match(signal_id, rule.id, rule.actions)
            if not created:
                continue
            if self.event_bus is not None:
                self.event_bus.publish(RuleTriggered(rule.id, signal_id))
            logger.info("Smart alert rule matched: %s", rule.name)
            if "telegram" in rule.actions and self.telegram is not None:
                try:
                    self.telegram.send_rule_alert(rule.name, rule.priority, rule_facts)
                except Exception:
                    logger.exception("Telegram smart rule alert failed for %s", rule.name)


def _signal_graph_metrics(
    db: Database,
    token: str | None,
    narrative: str | None,
) -> dict[str, float | int]:
    values: list[dict] = []
    for node_type, entity_id in (("token", token), ("narrative", narrative)):
        if not entity_id or not db.has_table("graph_nodes"):
            continue
        from app.graph.models import normalize_entity

        canonical_id, _, _ = normalize_entity(node_type, entity_id)
        row = db.get_graph_node(node_type, canonical_id)
        if row is None:
            continue
        try:
            values.append(json.loads(str(row["metadata_json"] or "{}")))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
    fields = {
        "node_degree": 0,
        "weighted_degree": 0.0,
        "bridge_score": 0.0,
        "emerging_relationship_score": 0.0,
        "source_diversity": 0,
        "connected_narrative_count": 0,
        "connected_token_count": 0,
    }
    for field in fields:
        fields[field] = max((item.get(field, 0) for item in values), default=0)
    return fields


def _rule_name(value: Any) -> str:
    name = str(value).strip()
    if not name:
        raise RuleValidationError("Rule name cannot be empty")
    if len(name) > 120:
        raise RuleValidationError("Rule name cannot exceed 120 characters")
    return name
