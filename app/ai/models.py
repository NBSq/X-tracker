from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


PROMPT_VERSION = "signal-reasoning-v1"


class SignalAction(str, Enum):
    IGNORE = "ignore"
    MONITOR = "monitor"
    RESEARCH = "research"
    HIGH_PRIORITY_RESEARCH = "high_priority_research"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class SourceEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    post_id: str
    source: str
    text: str


class SignalAnalysisContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    signal_id: int
    signal_type: str
    token: str | None = None
    narrative: str | None = None
    hype_score: float = Field(ge=0, le=100)
    momentum_score: float = Field(ge=0, le=100)
    mention_count: int = Field(ge=0)
    confidence: int = Field(ge=0, le=10)
    recent_posts: tuple[SourceEvidence, ...] = ()
    source_names: tuple[str, ...] = ()
    watchlist_matches: tuple[str, ...] = ()
    triggered_rules: tuple[str, ...] = ()
    high_priority_rule: bool = False
    historical_metrics: dict[str, float | int | str | None] = Field(
        default_factory=dict
    )
    recent_outcomes: tuple[dict[str, float | int | str | None], ...] = ()
    related_tokens: tuple[str, ...] = ()
    related_narratives: tuple[str, ...] = ()


class SignalAnalysisPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=600)
    why_it_matters: str = Field(min_length=1, max_length=1000)
    action: SignalAction
    confidence: int = Field(ge=1, le=10)
    risk_level: RiskLevel
    supporting_factors: list[str] = Field(default_factory=list, max_length=6)
    risk_factors: list[str] = Field(default_factory=list, max_length=6)
    related_tokens: list[str] = Field(default_factory=list, max_length=12)
    related_narratives: list[str] = Field(default_factory=list, max_length=12)
    market_context: str = Field(default="", max_length=1000)
    invalidation_conditions: list[str] = Field(default_factory=list, max_length=6)


class SignalAnalysisResult(SignalAnalysisPayload):
    model_config = ConfigDict(extra="forbid", frozen=True)

    model: str
    provider: str
    generated_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    prompt_version: str = PROMPT_VERSION
    cached: bool = False
    fallback_used: bool = False

    @classmethod
    def from_payload(
        cls,
        payload: SignalAnalysisPayload,
        *,
        model: str,
        provider: str,
        cached: bool = False,
        fallback_used: bool = False,
    ) -> "SignalAnalysisResult":
        return cls(
            **payload.model_dump(),
            model=model,
            provider=provider,
            cached=cached,
            fallback_used=fallback_used,
        )
