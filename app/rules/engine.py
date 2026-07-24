from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.alerts.telegram import TelegramAlerter
from app.db.database import Database
from app.events.models import SignalCreated
from app.rules.models import (
    AlertRule,
    RuleEvaluation,
    RuleValidationError,
    SignalFacts,
    evaluate_condition,
    normalize_actions,
    validate_condition,
)


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
    ) -> None:
        self.db = db
        self.telegram = telegram
        self.rules = RuleService(db)

    def __call__(self, event: SignalCreated) -> None:
        enabled_rules = self.rules.list_rules(enabled=True)
        if not enabled_rules:
            return
        signal_id = self.db.find_signal_history_id(event.history_record())
        if signal_id is None:
            logger.warning("Rule evaluation skipped: signal history row was not found")
            return
        success_rate = self.db.get_entity_outcome_success_rate(
            event.token,
            event.narrative,
        )
        watchlist_context = self.db.get_signal_watchlist_context(signal_id)
        facts = SignalFacts(
            token=event.token,
            narrative=event.narrative,
            hype_score=event.hype_score,
            momentum_score=event.momentum_score,
            confidence=event.confidence,
            mentions=event.mentions_count,
            outcome_success_rate=success_rate,
            watchlists=tuple(watchlist_context["names"]),
            watchlist_ids=tuple(watchlist_context["ids"]),
            watchlist_priority=int(watchlist_context["highest_priority"]),
            matched_watchlist=bool(watchlist_context["matched_any"]),
        )
        for rule in enabled_rules:
            if not evaluate_condition(rule.condition, facts):
                continue
            created = self.db.save_rule_match(signal_id, rule.id, rule.actions)
            if not created:
                continue
            logger.info("Smart alert rule matched: %s", rule.name)
            if "telegram" in rule.actions and self.telegram is not None:
                try:
                    self.telegram.send_rule_alert(rule.name, rule.priority, facts)
                except Exception:
                    logger.exception("Telegram smart rule alert failed for %s", rule.name)


def _rule_name(value: Any) -> str:
    name = str(value).strip()
    if not name:
        raise RuleValidationError("Rule name cannot be empty")
    if len(name) > 120:
        raise RuleValidationError("Rule name cannot exceed 120 characters")
    return name
