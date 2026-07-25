from __future__ import annotations

from app.ai.models import (
    RiskLevel,
    SignalAction,
    SignalAnalysisContext,
    SignalAnalysisPayload,
    SignalAnalysisResult,
)


class MockSignalAnalyzer:
    provider = "mock"
    model = "deterministic-rules-v1"

    def analyze_signal(
        self,
        context: SignalAnalysisContext,
    ) -> SignalAnalysisResult:
        strength = (
            context.hype_score * 0.45
            + context.momentum_score * 0.35
            + context.confidence * 2.0
        )
        if strength >= 78 or context.high_priority_rule:
            action = SignalAction.HIGH_PRIORITY_RESEARCH
        elif strength >= 60:
            action = SignalAction.RESEARCH
        elif strength >= 35:
            action = SignalAction.MONITOR
        else:
            action = SignalAction.IGNORE

        if context.confidence <= 4 or not context.recent_posts:
            risk = RiskLevel.UNKNOWN
        elif context.hype_score >= 80 and context.momentum_score < 45:
            risk = RiskLevel.HIGH
        elif context.hype_score >= 60:
            risk = RiskLevel.MEDIUM
        else:
            risk = RiskLevel.LOW

        name = context.token or context.narrative or "This signal"
        factors = [
            f"Hype score is {context.hype_score:.0f}/100.",
            f"Momentum score is {context.momentum_score:.0f}/100.",
            f"The signal has {context.mention_count} recent mentions.",
        ]
        if context.watchlist_matches:
            factors.append(
                "Matched watchlists: " + ", ".join(context.watchlist_matches) + "."
            )
        risks = []
        if context.confidence < 7:
            risks.append("Signal confidence is below the strong-evidence range.")
        if len(context.recent_posts) < 2:
            risks.append("Supporting source coverage is limited.")
        if not risks:
            risks.append("Narrative attention can fade without further mentions.")

        payload = SignalAnalysisPayload(
            summary=f"{name} has a {strength:.0f}/100 deterministic signal strength.",
            why_it_matters=(
                "Recent attention, importance, and momentum indicate whether the "
                "narrative may be continuing. This is an evidence summary, not a "
                "prediction of token price performance."
            ),
            action=action,
            confidence=max(1, min(10, context.confidence)),
            risk_level=risk,
            supporting_factors=factors[:6],
            risk_factors=risks[:6],
            related_tokens=list(context.related_tokens),
            related_narratives=list(context.related_narratives),
            market_context=(
                "Derived only from stored crypto posts, signal metrics, watchlists, "
                "rules, and outcomes supplied by the tracker."
            ),
            invalidation_conditions=[
                "Mentions and momentum fall materially during the next evaluation window.",
                "New source evidence contradicts the current narrative.",
            ],
        )
        return SignalAnalysisResult.from_payload(
            payload,
            model=self.model,
            provider=self.provider,
        )
