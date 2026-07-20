from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from app.db.database import Database
from app.events.bus import EventBus
from app.events.models import SignalEvaluationRequested, SignalEvaluated
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
    hype_change: float,
    mentions_change: int,
    momentum_change: float,
    original_mentions: int,
    thresholds: OutcomeThresholds,
) -> tuple[str, float]:
    """Classify absolute metric changes with deterministic success precedence."""
    del original_mentions  # Retained for compatibility with the original public helper.
    changes = (float(hype_change), float(mentions_change), float(momentum_change))
    if any(change >= thresholds.success for change in changes):
        return "SUCCESS", max(changes)
    if any(change <= thresholds.failure for change in changes):
        return "FAILED", min(changes)
    return "NEUTRAL", max(changes, key=abs)


class OutcomeEvaluator:
    def __init__(
        self,
        db: Database,
        thresholds: OutcomeThresholds,
        event_bus: EventBus | None = None,
    ) -> None:
        self.db = db
        self.thresholds = thresholds
        self.event_bus = event_bus

    def evaluate_due(self, evaluation_windows: Iterable[int]) -> int:
        evaluated = 0
        for window in dict.fromkeys(evaluation_windows):
            if window <= 0:
                raise ValueError("Evaluation windows must be positive")
            if self.event_bus is not None:
                self.event_bus.publish(SignalEvaluationRequested(window))
            evaluated += self.evaluate_window(window)
        return evaluated

    def evaluate_window(self, evaluation_window_hours: int) -> int:
        evaluated = 0
        for signal in self.db.get_pending_signal_outcomes(evaluation_window_hours):
            event = self._evaluate_signal(signal, evaluation_window_hours)
            outcome_id = self.db.save_signal_outcome(
                signal_id=event.signal_id,
                hours_after=event.hours_after,
                status=event.status,
                score_change=event.score_change,
                mentions_change=event.mentions_change,
                momentum_change=event.momentum_change,
                notes=event.notes,
                evaluation_window_hours=event.evaluation_window_hours,
                original_hype_score=event.original_hype_score,
                current_hype_score=event.current_hype_score,
                original_momentum_score=event.original_momentum_score,
                current_momentum_score=event.current_momentum_score,
                original_mentions=event.original_mentions,
                current_mentions=event.current_mentions,
            )
            if outcome_id is None:
                continue
            persisted_event = SignalEvaluated(
                signal_id=event.signal_id,
                hours_after=event.hours_after,
                status=event.status,
                score_change=event.score_change,
                mentions_change=event.mentions_change,
                momentum_change=event.momentum_change,
                notes=event.notes,
                outcome_id=outcome_id,
                evaluated_at=datetime.now(timezone.utc).isoformat(),
                original_hype_score=event.original_hype_score,
                current_hype_score=event.current_hype_score,
                original_momentum_score=event.original_momentum_score,
                current_momentum_score=event.current_momentum_score,
                original_mentions=event.original_mentions,
                current_mentions=event.current_mentions,
            )
            if self.event_bus is not None:
                self.event_bus.publish(persisted_event)
            evaluated += 1
        return evaluated

    def _evaluate_signal(self, signal, evaluation_window_hours: int) -> SignalEvaluated:
        metrics = self.db.get_current_signal_metrics(
            token=signal["token"],
            narrative=signal["narrative"],
            lookback_hours=evaluation_window_hours,
        )
        current_mentions = int(metrics["mentions_count"] or 0)
        average_importance = float(metrics["average_importance"] or 0.0)
        recency_hours = float(
            metrics["recency_hours"]
            if metrics["recency_hours"] is not None
            else evaluation_window_hours
        )
        current_hype = float(
            normalize_hype_score(current_mentions * average_importance)
        )
        current_momentum = float(
            calculate_momentum_score(
                mentions_count=current_mentions,
                average_importance=average_importance,
                growth_percent=0.0,
                recency_hours=recency_hours,
            )
        )
        original_hype = float(signal["hype_score"])
        original_momentum = float(signal["momentum_score"])
        original_mentions = int(signal["mentions_count"] or 0)
        hype_change = round(current_hype - original_hype, 2)
        momentum_change = round(current_momentum - original_momentum, 2)
        mentions_change = current_mentions - original_mentions
        status, threshold_change = classify_outcome(
            hype_change,
            mentions_change,
            momentum_change,
            original_mentions,
            self.thresholds,
        )
        notes = [
            (
                "Rule: SUCCESS if any metric change is at or above "
                f"{self.thresholds.success:g}; otherwise FAILED if any change is "
                f"at or below {self.thresholds.failure:g}; otherwise NEUTRAL."
            ),
            f"Decisive change: {threshold_change:+.2f}.",
        ]
        if current_mentions == 0:
            notes.append("No current mentions found; current metrics were treated as zero.")
        if signal["mentions_count"] is None:
            notes.append("Original mentions were unavailable for this legacy signal.")
        return SignalEvaluated(
            signal_id=int(signal["id"]),
            hours_after=evaluation_window_hours,
            status=status,
            score_change=hype_change,
            mentions_change=mentions_change,
            momentum_change=momentum_change,
            notes=" ".join(notes),
            original_hype_score=original_hype,
            current_hype_score=current_hype,
            original_momentum_score=original_momentum,
            current_momentum_score=current_momentum,
            original_mentions=original_mentions,
            current_mentions=current_mentions,
        )


def evaluate_pending_signals(
    db: Database,
    hours_after: int,
    thresholds: OutcomeThresholds,
    event_bus: EventBus | None = None,
) -> int:
    """Backward-compatible single-window evaluator entry point."""
    return OutcomeEvaluator(db, thresholds, event_bus).evaluate_due([hours_after])
