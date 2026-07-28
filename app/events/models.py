from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.scoring.hype_score import normalize_hype_score

if TYPE_CHECKING:
    from app.ai.models import SignalAnalysisResult
    from app.alerts.telegram import HypeAlert
    from app.sources.x_client import XPost


@dataclass(frozen=True)
class RSSFetched:
    posts: tuple[XPost, ...]
    feed_count: int
    fetched_at: datetime

    @classmethod
    def create(cls, posts: list[XPost], feed_count: int) -> "RSSFetched":
        return cls(tuple(posts), feed_count, datetime.now(timezone.utc))


@dataclass(frozen=True)
class SignalCreated:
    alert: HypeAlert
    signal_type: str
    token: str | None
    narrative: str | None
    hype_score: float
    momentum_score: float
    confidence: int
    action: str
    mentions_count: int

    @classmethod
    def from_alert(cls, alert: HypeAlert) -> "SignalCreated":
        signals = [alert.signal]
        if alert.merged_signal is not None:
            signals.append(alert.merged_signal)
        token = next((item.name for item in signals if item.kind == "token"), None)
        narrative = next(
            (item.name for item in signals if item.kind == "narrative"),
            None,
        )
        signal_type = (
            "token + narrative"
            if token and narrative
            else "token"
            if token
            else "narrative"
        )
        raw_hype = (
            alert.merged_hype_score
            if alert.merged_hype_score is not None
            else alert.signal.hype_score
        )
        mentions_count = (
            alert.baseline_mentions_count
            if alert.baseline_mentions_count is not None
            else alert.signal.mentions_count
        )
        return cls(
            alert=alert,
            signal_type=signal_type,
            token=token,
            narrative=narrative,
            hype_score=normalize_hype_score(raw_hype),
            momentum_score=max((item.score for item in alert.momentum), default=0),
            confidence=alert.insight.confidence,
            action=alert.insight.action,
            mentions_count=mentions_count,
        )

    def history_record(self) -> dict[str, str | float | int | None]:
        return {
            "signal_type": self.signal_type,
            "token": self.token,
            "narrative": self.narrative,
            "hype_score": self.hype_score,
            "momentum_score": self.momentum_score,
            "confidence": self.confidence,
            "action": self.action,
            "mentions_count": self.mentions_count,
        }


@dataclass(frozen=True)
class WatchlistMatched:
    signal_id: int
    watchlist_ids: tuple[int, ...]
    watchlist_names: tuple[str, ...]
    matched_tokens: tuple[str, ...]
    matched_narratives: tuple[str, ...]
    highest_priority: int


@dataclass(frozen=True)
class SignalEvaluationRequested:
    evaluation_window_hours: int


@dataclass(frozen=True)
class SignalEvaluated:
    signal_id: int
    hours_after: int
    status: str
    score_change: float
    mentions_change: int
    momentum_change: float
    notes: str
    outcome_id: int | None = None
    evaluated_at: str | None = None
    original_hype_score: float = 0.0
    current_hype_score: float = 0.0
    original_momentum_score: float = 0.0
    current_momentum_score: float = 0.0
    original_mentions: int = 0
    current_mentions: int = 0

    @property
    def evaluation_window_hours(self) -> int:
        return self.hours_after

    @property
    def hype_change(self) -> float:
        return self.score_change


@dataclass(frozen=True)
class PerformanceUpdated:
    signals_generated: int
    signals_evaluated: int
    success_rate: float


@dataclass(frozen=True)
class NarrativeDetected:
    post_id: str
    narrative: str
    importance: int
    detected_at: datetime

    @classmethod
    def create(
        cls,
        post_id: str,
        narrative: str,
        importance: int,
    ) -> "NarrativeDetected":
        return cls(post_id, narrative, importance, datetime.now(timezone.utc))


@dataclass(frozen=True)
class AIAnalysisRequested:
    signal_id: int
    provider: str
    requested_at: datetime


@dataclass(frozen=True)
class AIAnalysisCompleted:
    signal_id: int
    result: SignalAnalysisResult


@dataclass(frozen=True)
class AIAnalysisFailed:
    signal_id: int
    provider: str
    error_type: str
    message: str


@dataclass(frozen=True)
class AIAnalysisFallbackUsed:
    signal_id: int
    reason: str


@dataclass(frozen=True)
class ContentFetched:
    source_id: int
    source_key: str
    item_count: int
    duration_ms: int


@dataclass(frozen=True)
class ContentAccepted:
    content_item_id: int
    unified_event_id: int
    source_key: str


@dataclass(frozen=True)
class ContentDeduplicated:
    content_item_id: int
    unified_event_id: int
    source_key: str
    match_reason: str
    similarity_score: float


@dataclass(frozen=True)
class UnifiedEventCreated:
    unified_event_id: int
    primary_content_item_id: int


@dataclass(frozen=True)
class UnifiedEventUpdated:
    unified_event_id: int
    source_count: int
    item_count: int
    new_source_count: int


@dataclass(frozen=True)
class UnifiedEventMateriallyChanged:
    unified_event_id: int
    previous_source_count: int
    current_source_count: int
    previous_hype_score: float
    current_hype_score: float
    previous_momentum_score: float
    current_momentum_score: float
    reason: str


@dataclass(frozen=True)
class SourceFetchFailed:
    source_id: int
    source_key: str
    error_type: str
    consecutive_failures: int


@dataclass(frozen=True)
class SourceRecovered:
    source_id: int
    source_key: str


@dataclass(frozen=True)
class RuleTriggered:
    rule_id: int
    signal_id: int


@dataclass(frozen=True)
class GraphUpdated:
    update_reason: str
    node_count: int
    edge_count: int


@dataclass(frozen=True)
class EmergingRelationshipDetected:
    graph_edge_id: int
    emerging_score: float


@dataclass(frozen=True)
class GraphSnapshotCreated:
    snapshot_id: int
    frequency: str


@dataclass(frozen=True)
class SignalQualityCalculated:
    signal_id: int
    quality_score: float
    classification: str
    calculation_version: int


@dataclass(frozen=True)
class QualityAggregateUpdated:
    entity_type: str
    entity_id: str
    average_quality_score: float | None
    calculation_version: int


@dataclass(frozen=True)
class QualityDegradationDetected:
    entity_type: str
    entity_id: str
    change: float


@dataclass(frozen=True)
class QualityImprovementDetected:
    entity_type: str
    entity_id: str
    change: float


@dataclass(frozen=True)
class QualityRecommendationCreated:
    recommendation_id: int
    entity_type: str
    entity_id: str
    severity: str
