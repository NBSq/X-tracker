from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


RULE_FIELDS = frozenset(
    {
        "token",
        "narrative",
        "hype_score",
        "momentum_score",
        "confidence",
        "mentions",
        "outcome_success_rate",
        "watchlist",
        "watchlist_id",
        "watchlist_priority",
        "matched_watchlist",
    }
)
ALERT_ACTIONS = frozenset(
    {
        "telegram",
        "high_priority",
        "dashboard_highlight",
        "include_in_digest",
        "csv_export_marker",
    }
)
LOGICAL_OPERATORS = frozenset({"AND", "OR", "NOT"})
COMPARISON_OPERATORS = frozenset(
    {"eq", "ne", "gt", "gte", "lt", "lte", "contains", "in"}
)
_OPERATOR_ALIASES = {
    "=": "eq",
    "==": "eq",
    "!=": "ne",
    ">": "gt",
    ">=": "gte",
    "<": "lt",
    "<=": "lte",
}
_ACTION_ALIASES = {
    "telegram_notification": "telegram",
    "high_priority_flag": "high_priority",
    "dashboard": "dashboard_highlight",
    "digest": "include_in_digest",
    "csv": "csv_export_marker",
}
_TEXT_FIELDS = frozenset({"token", "narrative", "watchlist"})
_BOOLEAN_FIELDS = frozenset({"matched_watchlist"})


class RuleValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SignalFacts:
    token: str | None
    narrative: str | None
    hype_score: float
    momentum_score: float
    confidence: int
    mentions: int
    outcome_success_rate: float
    watchlists: tuple[str, ...] = ()
    watchlist_ids: tuple[int, ...] = ()
    watchlist_priority: int = 0
    matched_watchlist: bool = False

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "SignalFacts":
        return cls(
            token=_optional_text(value.get("token")),
            narrative=_optional_text(value.get("narrative")),
            hype_score=_number(value.get("hype_score", 0), "hype_score"),
            momentum_score=_number(
                value.get("momentum_score", 0), "momentum_score"
            ),
            confidence=int(_number(value.get("confidence", 0), "confidence")),
            mentions=int(
                _number(
                    value.get("mentions", value.get("mentions_count", 0)),
                    "mentions",
                )
            ),
            outcome_success_rate=_number(
                value.get("outcome_success_rate", 0),
                "outcome_success_rate",
            ),
            watchlists=_as_text_tuple(
                value.get("watchlists", value.get("watchlist"))
            ),
            watchlist_ids=_as_int_tuple(
                value.get("watchlist_ids", value.get("watchlist_id"))
            ),
            watchlist_priority=int(
                _number(value.get("watchlist_priority", 0), "watchlist_priority")
            ),
            matched_watchlist=bool(value.get("matched_watchlist", False)),
        )

    def value_for(self, field: str) -> Any:
        if field == "watchlist":
            return self.watchlists
        if field == "watchlist_id":
            return self.watchlist_ids
        return getattr(self, field)


@dataclass(frozen=True)
class AlertRule:
    id: int
    name: str
    enabled: bool
    priority: int
    condition: dict[str, Any]
    actions: tuple[str, ...]
    created_at: str
    updated_at: str
    last_triggered: str | None
    trigger_count: int

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "AlertRule":
        condition = json.loads(str(row["condition"]))
        actions = normalize_actions(json.loads(str(row["action"])))
        validate_condition(condition)
        return cls(
            id=int(row["id"]),
            name=str(row["name"]),
            enabled=bool(row["enabled"]),
            priority=int(row["priority"]),
            condition=condition,
            actions=actions,
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
            last_triggered=(
                str(row["last_triggered"])
                if row["last_triggered"] is not None
                else None
            ),
            trigger_count=int(row["trigger_count"]),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": self.enabled,
            "priority": self.priority,
            "condition": self.condition,
            "action": list(self.actions),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_triggered": self.last_triggered,
            "trigger_count": self.trigger_count,
        }


@dataclass(frozen=True)
class RuleEvaluation:
    rule: AlertRule
    matched: bool
    facts: SignalFacts


def normalize_actions(value: str | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    raw_actions = [value] if isinstance(value, str) else list(value)
    if not raw_actions:
        raise RuleValidationError("A rule must define at least one action")
    normalized = []
    for item in raw_actions:
        action = str(item).strip().lower().replace("-", " ").replace(" ", "_")
        action = _ACTION_ALIASES.get(action, action)
        if action not in ALERT_ACTIONS:
            raise RuleValidationError(f"Unsupported rule action: {item}")
        if action not in normalized:
            normalized.append(action)
    return tuple(normalized)


def validate_condition(condition: Any, path: str = "condition") -> None:
    if not isinstance(condition, dict) or not condition:
        raise RuleValidationError(f"{path} must be a non-empty JSON object")

    logical_key = next(
        (str(key).upper() for key in condition if str(key).upper() in LOGICAL_OPERATORS),
        None,
    )
    if logical_key is not None:
        if len(condition) != 1:
            raise RuleValidationError(
                f"{path} logical expressions must contain exactly one operator"
            )
        original_key = next(iter(condition))
        value = condition[original_key]
        if logical_key in {"AND", "OR"}:
            if not isinstance(value, list) or not value:
                raise RuleValidationError(f"{path}.{logical_key} must be a non-empty list")
            for index, child in enumerate(value):
                validate_condition(child, f"{path}.{logical_key}[{index}]")
        else:
            validate_condition(value, f"{path}.NOT")
        return

    required = {"field", "operator", "value"}
    if set(condition) != required:
        raise RuleValidationError(
            f"{path} comparisons require exactly: field, operator, value"
        )
    field = str(condition["field"]).strip().lower()
    if field not in RULE_FIELDS:
        raise RuleValidationError(f"Unsupported rule field: {condition['field']}")
    operator = _normalize_operator(condition["operator"])
    value = condition["value"]
    if field in _BOOLEAN_FIELDS:
        if operator not in {"eq", "ne"} or not isinstance(value, bool):
            raise RuleValidationError(
                f"{path}.value must be true or false and use eq/ne"
            )
    elif field in _TEXT_FIELDS:
        if operator not in {"eq", "ne", "contains", "in"}:
            raise RuleValidationError(
                f"Operator {condition['operator']} is not valid for {field}"
            )
        if operator == "in":
            if not isinstance(value, list) or not value:
                raise RuleValidationError(f"{path}.value must be a non-empty list")
        elif not isinstance(value, str):
            raise RuleValidationError(f"{path}.value must be text")
    else:
        if operator not in {"eq", "ne", "gt", "gte", "lt", "lte", "in"}:
            raise RuleValidationError(
                f"Operator {condition['operator']} is not valid for {field}"
            )
        values = value if operator == "in" and isinstance(value, list) else [value]
        if operator == "in" and (not isinstance(value, list) or not value):
            raise RuleValidationError(f"{path}.value must be a non-empty list")
        for item in values:
            _number(item, f"{path}.value")


def evaluate_condition(condition: dict[str, Any], facts: SignalFacts) -> bool:
    validate_condition(condition)
    key = next(iter(condition))
    logical = str(key).upper()
    if logical == "AND":
        return all(evaluate_condition(item, facts) for item in condition[key])
    if logical == "OR":
        return any(evaluate_condition(item, facts) for item in condition[key])
    if logical == "NOT":
        return not evaluate_condition(condition[key], facts)

    field = str(condition["field"]).strip().lower()
    operator = _normalize_operator(condition["operator"])
    actual = facts.value_for(field)
    expected = condition["value"]
    if field in _BOOLEAN_FIELDS:
        matched = bool(actual) is bool(expected)
        return matched if operator == "eq" else not matched
    if field in _TEXT_FIELDS:
        return _compare_text(actual, operator, expected)
    return _compare_number(actual, operator, expected)


def _compare_text(actual: Any, operator: str, expected: Any) -> bool:
    if isinstance(actual, (tuple, list, set)):
        values = tuple(actual)
        if operator == "ne":
            return all(_compare_text(item, "ne", expected) for item in values)
        return any(_compare_text(item, operator, expected) for item in values)
    actual_text = "" if actual is None else str(actual).casefold()
    if operator == "in":
        expected_values = {str(item).casefold() for item in expected}
        return actual_text in expected_values
    expected_text = str(expected).casefold()
    if operator == "eq":
        return actual_text == expected_text
    if operator == "ne":
        return actual_text != expected_text
    return expected_text in actual_text


def _compare_number(actual: Any, operator: str, expected: Any) -> bool:
    if isinstance(actual, (tuple, list, set)):
        values = tuple(actual)
        if operator == "ne":
            return all(_compare_number(item, "ne", expected) for item in values)
        return any(_compare_number(item, operator, expected) for item in values)
    actual_number = float(actual or 0)
    if operator == "in":
        return actual_number in {float(item) for item in expected}
    expected_number = float(expected)
    comparisons = {
        "eq": actual_number == expected_number,
        "ne": actual_number != expected_number,
        "gt": actual_number > expected_number,
        "gte": actual_number >= expected_number,
        "lt": actual_number < expected_number,
        "lte": actual_number <= expected_number,
    }
    return comparisons[operator]


def _normalize_operator(value: Any) -> str:
    operator = str(value).strip().lower()
    operator = _OPERATOR_ALIASES.get(operator, operator)
    if operator not in COMPARISON_OPERATORS:
        raise RuleValidationError(f"Unsupported comparison operator: {value}")
    return operator


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise RuleValidationError(f"{field} must be numeric")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RuleValidationError(f"{field} must be numeric") from exc


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_text_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _as_int_tuple(value: Any) -> tuple[int, ...]:
    if value is None:
        return ()
    if isinstance(value, (str, int)):
        return (int(value),)
    return tuple(int(item) for item in value)
