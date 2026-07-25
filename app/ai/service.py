from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from app.ai.base import SignalAnalysisUnavailable, SignalProviderError
from app.ai.mock_analyzer import MockSignalAnalyzer
from app.ai.models import (
    PROMPT_VERSION,
    SignalAnalysisContext,
    SignalAnalysisResult,
    SourceEvidence,
)
from app.ai.openai_analyzer import OpenAISignalAnalyzer
from app.config import Config
from app.db.database import Database
from app.events.bus import EventBus
from app.events.models import (
    AIAnalysisCompleted,
    AIAnalysisFailed,
    AIAnalysisFallbackUsed,
    AIAnalysisRequested,
)


logger = logging.getLogger("x_narrative_tracker")
_LOCKS_GUARD = threading.Lock()
_ANALYSIS_LOCKS: dict[tuple[str, int], threading.Lock] = {}


class SignalReasoningService:
    """Selects a provider, applies safeguards, and persists signal reasoning."""

    def __init__(
        self,
        db: Database,
        config: Config,
        *,
        event_bus: EventBus | None = None,
        openai_analyzer: OpenAISignalAnalyzer | None = None,
        mock_analyzer: MockSignalAnalyzer | None = None,
        force_mock: bool = False,
    ) -> None:
        self.db = db
        self.config = config
        self.event_bus = event_bus
        self.mock_analyzer = mock_analyzer or MockSignalAnalyzer()
        self.openai_analyzer = None if force_mock else openai_analyzer
        self.force_mock = force_mock

    def analyze_signal(
        self,
        signal_id: int,
        *,
        force: bool = False,
    ) -> SignalAnalysisResult:
        lock_key = (str(self.db.path.resolve()), signal_id)
        with _LOCKS_GUARD:
            lock = _ANALYSIS_LOCKS.setdefault(lock_key, threading.Lock())
        with lock:
            return self._analyze_signal_locked(signal_id, force=force)

    def _analyze_signal_locked(
        self,
        signal_id: int,
        *,
        force: bool,
    ) -> SignalAnalysisResult:
        signal = self.db.get_signal(signal_id)
        if signal is None:
            raise ValueError(f"Signal {signal_id} does not exist")
        context = self.build_context(signal_id)
        provider = self._select_provider(context, force=force)
        existing = self.db.get_signal_ai_analysis(signal_id, provider)
        if existing is not None:
            return result_from_row(existing)

        self._publish(
            AIAnalysisRequested(
                signal_id=signal_id,
                provider=provider,
                requested_at=datetime.now(timezone.utc),
            )
        )
        if provider == "mock":
            return self._run_mock(context)
        if self.openai_analyzer is None:
            return self._fallback_or_raise(context, "missing_api_key")
        return self._run_openai(context)

    def build_context(self, signal_id: int) -> SignalAnalysisContext:
        signal = self.db.get_signal(signal_id)
        if signal is None:
            raise ValueError(f"Signal {signal_id} does not exist")

        posts_by_id: dict[str, object] = {}
        for kind, name in (("token", signal["token"]), ("narrative", signal["narrative"])):
            if not name:
                continue
            for post in self.db.get_signal_posts(kind, str(name), 24 * 60, 3):
                posts_by_id.setdefault(str(post["post_id"]), post)
        posts = sorted(
            posts_by_id.values(),
            key=lambda row: float(row["importance"]),
            reverse=True,
        )[:3]

        tokens: list[str] = []
        narratives: list[str] = []
        for post in posts:
            tokens.extend(_json_strings(post["tokens_json"]))
            narratives.extend(_json_strings(post["narratives_json"]))
        own_token = str(signal["token"]) if signal["token"] else None
        own_narrative = str(signal["narrative"]) if signal["narrative"] else None
        related_tokens = tuple(item for item in _unique(tokens) if item != own_token)
        related_narratives = tuple(
            item for item in _unique(narratives) if item != own_narrative
        )

        watchlists = self.db.get_signal_watchlist_context(signal_id)
        rule_matches = self.db.get_rule_matches(signal_id)
        outcomes = self.db.get_entity_recent_outcomes(own_token, own_narrative)
        unified_event = self.db.get_signal_unified_event(signal_id)
        event_items = (
            self.db.get_unified_event_items(int(unified_event["id"]))
            if unified_event is not None
            else []
        )
        event_evidence = tuple(
            SourceEvidence(
                post_id=str(item["external_id"]),
                source=str(item["source_name"]),
                text=f"{item['title']}. {item['body']}"[:2500],
            )
            for item in event_items[:3]
        )
        conflicts = (
            tuple(_json_strings(unified_event["detected_conflicts_json"]))
            if unified_event is not None
            else ()
        )
        return SignalAnalysisContext(
            signal_id=signal_id,
            signal_type=str(signal["signal_type"]),
            token=own_token,
            narrative=own_narrative,
            hype_score=float(signal["hype_score"]),
            momentum_score=float(signal["momentum_score"]),
            mention_count=int(signal["mentions_count"] or 0),
            confidence=int(signal["confidence"]),
            recent_posts=event_evidence or tuple(
                SourceEvidence(
                    post_id=str(post["post_id"]),
                    source=str(post["username"]),
                    text=str(post["text"]),
                )
                for post in posts
            ),
            source_names=(
                tuple(_unique(str(item["source_name"]) for item in event_items))
                if event_items
                else tuple(_unique(str(post["username"]) for post in posts))
            ),
            watchlist_matches=tuple(str(item) for item in watchlists["names"]),
            triggered_rules=tuple(str(row["rule_name"]) for row in rule_matches),
            high_priority_rule=any(bool(row["high_priority"]) for row in rule_matches),
            historical_metrics={
                "outcome_success_rate": round(
                    self.db.get_entity_outcome_success_rate(
                        own_token,
                        own_narrative,
                    ),
                    2,
                )
            },
            recent_outcomes=tuple(
                {
                    "status": str(row["status"]),
                    "evaluation_window_hours": int(row["evaluation_window_hours"]),
                    "hype_change": float(row["hype_change"]),
                    "momentum_change": float(row["momentum_change"]),
                    "mentions_change": int(row["mentions_change"]),
                }
                for row in outcomes
            ),
            related_tokens=related_tokens,
            related_narratives=related_narratives,
            unified_event_id=(int(unified_event["id"]) if unified_event else None),
            unified_event_version=(
                int(unified_event["material_version"]) if unified_event else 0
            ),
            source_count=int(unified_event["source_count"]) if unified_event else 0,
            item_count=int(unified_event["item_count"]) if unified_event else 0,
            publication_timeline=tuple(
                str(item["published_at"] or item["fetched_at"])
                for item in reversed(event_items)
            ),
            detected_conflicts=conflicts,
            requires_review=bool(unified_event["requires_review"]) if unified_event else False,
        )

    def status(self) -> dict[str, object]:
        summary = self.db.get_ai_usage_summary()
        requests = int(summary["requests"] or 0)
        successful = int(summary["successful"] or 0)
        cache_hits = int(summary["cache_hits"] or 0)
        return {
            "configured_provider": self.config.ai_provider,
            "openai_configured": bool(self.config.openai_api_key),
            "model": self.config.openai_model,
            "fallback_enabled": self.config.openai_fallback_to_mock,
            "daily_request_limit": self.config.openai_daily_request_limit,
            "openai_requests_today": self.db.count_openai_requests_today(),
            "analyses": len(self.db.get_signal_ai_analyses(limit=100000)),
            "requests": requests,
            "successful_requests": successful,
            "failed_requests": requests - successful,
            "cache_entries_active": self.db.get_active_ai_cache_count(),
            "cache_hits": cache_hits,
            "cache_hit_rate": round(cache_hits / requests * 100.0, 1)
            if requests
            else 0.0,
            "fallbacks": int(summary["fallbacks"] or 0),
            "average_latency_ms": round(float(summary["average_latency_ms"] or 0.0), 1),
            "model_distribution": self._distribution("model"),
            "action_distribution": self._distribution("action"),
            "risk_distribution": self._distribution("risk_level"),
        }

    def _distribution(self, field: str) -> dict[str, int]:
        return {
            str(row["name"]): int(row["count"])
            for row in self.db.get_ai_analysis_distribution(field)
        }

    def _select_provider(
        self,
        context: SignalAnalysisContext,
        *,
        force: bool,
    ) -> str:
        if self.force_mock or self.config.ai_provider == "mock":
            return "mock"
        eligible = force or context.high_priority_rule or bool(context.watchlist_matches)
        eligible = eligible or (
            context.hype_score >= self.config.openai_min_hype_score
            and context.momentum_score >= self.config.openai_min_momentum_score
            and context.confidence >= self.config.openai_min_confidence
        )
        if not eligible:
            return "mock"
        if self.openai_analyzer is not None or self.config.ai_provider == "openai":
            return "openai"
        if self.config.openai_fallback_to_mock:
            return "mock"
        raise SignalAnalysisUnavailable("OpenAI signal reasoning is not configured")

    def _run_mock(
        self,
        context: SignalAnalysisContext,
        *,
        fallback_used: bool = False,
        error_type: str | None = None,
    ) -> SignalAnalysisResult:
        started = time.perf_counter()
        result = self.mock_analyzer.analyze_signal(context).model_copy(
            update={"fallback_used": fallback_used}
        )
        self._persist_result(context.signal_id, result)
        self.db.save_ai_usage(
            signal_id=context.signal_id,
            provider="mock",
            model=result.model,
            success=True,
            fallback_used=fallback_used,
            input_size_estimate=len(context.model_dump_json()),
            output_size_estimate=len(result.model_dump_json()),
            latency_ms=int((time.perf_counter() - started) * 1000),
            error_type=error_type,
        )
        self._publish(AIAnalysisCompleted(context.signal_id, result))
        return result

    def _run_openai(self, context: SignalAnalysisContext) -> SignalAnalysisResult:
        if self.openai_analyzer is None:
            raise SignalAnalysisUnavailable("OpenAI signal reasoning is not configured")
        cache_key = _cache_key(context, self.openai_analyzer.model)
        cached = self.db.get_ai_cache(cache_key)
        if cached is not None:
            result = SignalAnalysisResult.model_validate_json(cached["result_json"])
            result = result.model_copy(update={"cached": True})
            self._persist_result(context.signal_id, result)
            self.db.save_ai_usage(
                signal_id=context.signal_id,
                provider="openai",
                model=result.model,
                success=True,
                cached=True,
                input_size_estimate=len(context.model_dump_json()),
                output_size_estimate=len(result.model_dump_json()),
            )
            self._publish(AIAnalysisCompleted(context.signal_id, result))
            return result

        if self.db.count_openai_requests_today() >= self.config.openai_daily_request_limit:
            return self._fallback_or_raise(context, "local_daily_limit")

        last_error: SignalProviderError | None = None
        for attempt in range(self.config.openai_max_retries + 1):
            if self.db.count_openai_requests_today() >= self.config.openai_daily_request_limit:
                return self._fallback_or_raise(context, "local_daily_limit")
            started = time.perf_counter()
            try:
                result = self.openai_analyzer.analyze_signal(context)
                result = _normalize_result(result, context)
                latency = int((time.perf_counter() - started) * 1000)
                usage = self.openai_analyzer.last_usage
                self.db.save_ai_usage(
                    signal_id=context.signal_id,
                    provider="openai",
                    model=result.model,
                    success=True,
                    input_size_estimate=len(context.model_dump_json()),
                    output_size_estimate=len(result.model_dump_json()),
                    latency_ms=latency,
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                )
                expires = datetime.now(timezone.utc) + timedelta(
                    hours=self.config.openai_cache_ttl_hours
                )
                self.db.save_ai_cache(
                    cache_key,
                    result.provider,
                    result.model,
                    result.prompt_version,
                    result.model_dump(mode="json"),
                    expires.isoformat(),
                )
                self._persist_result(context.signal_id, result)
                self._publish(AIAnalysisCompleted(context.signal_id, result))
                return result
            except SignalProviderError as exc:
                last_error = exc
                logger.warning(
                    "OpenAI signal reasoning attempt failed signal=%s "
                    "error_type=%s attempt=%s",
                    context.signal_id,
                    exc.error_type,
                    attempt + 1,
                )
                self.db.save_ai_usage(
                    signal_id=context.signal_id,
                    provider="openai",
                    model=self.openai_analyzer.model,
                    success=False,
                    input_size_estimate=len(context.model_dump_json()),
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    error_type=exc.error_type,
                )
                if not exc.transient or attempt >= self.config.openai_max_retries:
                    break
        assert last_error is not None
        self._publish(
            AIAnalysisFailed(
                context.signal_id,
                "openai",
                last_error.error_type,
                str(last_error),
            )
        )
        return self._fallback_or_raise(context, last_error.error_type)

    def _fallback_or_raise(
        self,
        context: SignalAnalysisContext,
        reason: str,
    ) -> SignalAnalysisResult:
        if not self.config.openai_fallback_to_mock:
            raise SignalAnalysisUnavailable(
                f"OpenAI signal analysis unavailable ({reason})"
            )
        if reason == "local_daily_limit":
            self.db.save_ai_usage(
                signal_id=context.signal_id,
                provider="openai",
                model=self.config.openai_model,
                success=False,
                fallback_used=True,
                error_type=reason,
                input_size_estimate=len(context.model_dump_json()),
            )
        self._publish(AIAnalysisFallbackUsed(context.signal_id, reason))
        return self._run_mock(context, fallback_used=True, error_type=reason)

    def _persist_result(self, signal_id: int, result: SignalAnalysisResult) -> None:
        self.db.save_signal_ai_analysis(signal_id, result.model_dump(mode="json"))
        logger.info(
            "Signal %s reasoning complete provider=%s model=%s cached=%s fallback=%s",
            signal_id,
            result.provider,
            result.model,
            result.cached,
            result.fallback_used,
        )

    def _publish(self, event: object) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(event)


def result_from_row(row) -> SignalAnalysisResult:
    return SignalAnalysisResult(
        summary=str(row["summary"]),
        why_it_matters=str(row["why_it_matters"]),
        action=str(row["action"]),
        confidence=int(row["confidence"]),
        risk_level=str(row["risk_level"]),
        supporting_factors=_json_strings(row["supporting_factors_json"]),
        risk_factors=_json_strings(row["risk_factors_json"]),
        related_tokens=_json_strings(row["related_tokens_json"]),
        related_narratives=_json_strings(row["related_narratives_json"]),
        market_context=str(row["market_context"]),
        invalidation_conditions=_json_strings(row["invalidation_conditions_json"]),
        model=str(row["model"]),
        provider=str(row["provider"]),
        generated_at=str(row["created_at"]),
        prompt_version=str(row["prompt_version"]),
        cached=bool(row["cached"]),
        fallback_used=bool(row["fallback_used"]),
    )


def _cache_key(context: SignalAnalysisContext, model: str) -> str:
    context_data = context.model_dump(mode="json")
    context_data.pop("signal_id", None)
    if context.unified_event_id is not None:
        context_data = {
            "unified_event_id": context.unified_event_id,
            "unified_event_version": context.unified_event_version,
            "signal_type": context.signal_type,
            "token": context.token,
            "narrative": context.narrative,
            "hype_score": context.hype_score,
            "momentum_score": context.momentum_score,
            "confidence": context.confidence,
        }
    payload = {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "context": context_data,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _json_strings(value: object) -> list[str]:
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _unique(values) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))


def _normalize_result(
    result: SignalAnalysisResult,
    context: SignalAnalysisContext,
) -> SignalAnalysisResult:
    allowed_tokens = _canonical_values(
        [context.token, *context.related_tokens],
        uppercase=True,
    )
    allowed_narratives = _canonical_values(
        [context.narrative, *context.related_narratives]
    )
    return result.model_copy(
        update={
            "summary": " ".join(result.summary.split()),
            "why_it_matters": " ".join(result.why_it_matters.split()),
            "supporting_factors": _unique(item.strip() for item in result.supporting_factors),
            "risk_factors": _unique(item.strip() for item in result.risk_factors),
            "related_tokens": _allowed_values(result.related_tokens, allowed_tokens),
            "related_narratives": _allowed_values(
                result.related_narratives,
                allowed_narratives,
            ),
            "market_context": " ".join(result.market_context.split()),
            "invalidation_conditions": _unique(
                item.strip() for item in result.invalidation_conditions
            ),
        }
    )


def _canonical_values(values, *, uppercase: bool = False) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if value is None:
            continue
        cleaned = " ".join(str(value).strip().lstrip("$").split())
        if cleaned:
            canonical = cleaned.upper() if uppercase else cleaned
            result.setdefault(canonical.casefold(), canonical)
    return result


def _allowed_values(values, allowed: dict[str, str]) -> list[str]:
    normalized = []
    for value in values:
        key = " ".join(str(value).strip().lstrip("$").split()).casefold()
        canonical = allowed.get(key)
        if canonical and canonical not in normalized:
            normalized.append(canonical)
    return normalized
