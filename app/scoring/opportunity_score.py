from dataclasses import dataclass


@dataclass(frozen=True)
class NarrativeOpportunity:
    name: str
    momentum_score: int
    growth_percent: float | None
    recency_days: float
    rank_score: int
    status: str


def build_opportunity(
    name: str,
    momentum_score: int,
    growth_percent: float | None,
    recency_days: float,
) -> NarrativeOpportunity:
    growth = growth_percent or 0.0
    momentum_component = min(max(momentum_score, 0), 100) * 0.65
    growth_component = min(max(growth, 0.0), 200.0) / 200.0 * 20
    recency_component = max(0.0, 1.0 - max(recency_days, 0.0) / 7.0) * 15
    rank_score = round(momentum_component + growth_component + recency_component)

    if momentum_score >= 70 and growth >= 75:
        status = "Emerging"
    elif momentum_score >= 55 and growth >= 20:
        status = "Growing"
    else:
        status = "Watchlist"

    return NarrativeOpportunity(
        name=name,
        momentum_score=momentum_score,
        growth_percent=growth_percent,
        recency_days=recency_days,
        rank_score=rank_score,
        status=status,
    )
