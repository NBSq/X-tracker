from __future__ import annotations

from dataclasses import dataclass

from app.db.database import Database
from app.events.bus import EventBus
from app.events.models import SignalEvaluated
from app.scoring.hype_score import normalize_hype_score
from app.scoring.momentum_score import calculate_momentum_score


@dataclass(frozen=True)
class OutcomeThresholds:
    success: float = 10.0
    failure: float = -10.0

    def __post_init__(self) -> None:
        if self.failure >= self.success:
            raise ValueError("Outcome failure threshold must be below success threshold")


def classify_outcome(
    score_change: float,
    mentions_change: int,
    momentum_change: float,
    original_mentions: int,
    thresholds: OutcomeThresholds,
) -> tuple[str, float]:
    mention_base = max(original_mentions, 1)
    mention_change_percent = max(
        -100.0,
        min(100.0, mentions_change / mention_base * 100.0),
    )
    change_index = round(
        score_change * 0.4
        + momentum_change * 0.4
        + mention_change_percent * 0.2,
        2,
    )
    if change_index >= thresholds.success:
        return "SUCCESS", change_index
    if change_index <= thresholds.failure:
        return "FAILED", change_index
    return "NEUTRAL", change_index


def evaluate_pending_signals(
    db: Database,
    hours_after: int,
    thresholds: OutcomeThresholds,
    event_bus: EventBus | None = None,
) -> int:
    evaluated = 0
    for signal in db.get_pending_signal_outcomes(hours_after):
        metrics = db.get_current_signal_metrics(
            token=signal["token"],
            narrative=signal["narrative"],
            lookback_hours=hours_after,
        )
        mentions = int(metrics["mentions_count"] or 0)
        importance = float(metrics["average_importance"] or 0.0)
        recency = float(metrics["recency_hours"] or hours_after)
        current_hype = normalize_hype_score(mentions * importance)
        current_momentum = calculate_momentum_score(
            mentions_count=mentions,
            average_importance=importance,
            growth_percent=0.0,
            recency_hours=recency,
        )
        original_mentions = (
            int(signal["mentions_count"])
            if signal["mentions_count"] is not None
            else 1
        )
        score_change = round(current_hype - float(signal["hype_score"]), 2)
        mentions_change = mentions - original_mentions
        momentum_change = round(
            current_momentum - float(signal["momentum_score"]),
            2,
        )
        status, change_index = classify_outcome(
            score_change,
            mentions_change,
            momentum_change,
            original_mentions,
            thresholds,
        )
        baseline_note = (
            "Original mentions inferred for legacy signal. "
            if signal["mentions_count"] is None
            else ""
        )
        event = SignalEvaluated(
            signal_id=int(signal["id"]),
            hours_after=hours_after,
            status=status,
            score_change=score_change,
            mentions_change=mentions_change,
            momentum_change=momentum_change,
            notes=f"{baseline_note}Weighted change index: {change_index:.2f}",
        )
        if event_bus is None:
            db.save_signal_outcome(
                signal_id=event.signal_id,
                hours_after=event.hours_after,
                status=event.status,
                score_change=event.score_change,
                mentions_change=event.mentions_change,
                momentum_change=event.momentum_change,
                notes=event.notes,
            )
        else:
            event_bus.publish(event)
        evaluated += 1
    return evaluated
