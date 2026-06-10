from dataclasses import dataclass


@dataclass(frozen=True)
class NarrativeMomentum:
    name: str
    score: int


def calculate_momentum_score(
    mentions_count: int,
    average_importance: float,
    growth_percent: float,
    recency_hours: float,
) -> int:
    mention_component = min(max(mentions_count, 0) / 10, 1.0) * 30
    importance_component = min(max(average_importance, 0) / 10, 1.0) * 30
    growth_component = min(max(growth_percent, 0) / 200, 1.0) * 25
    recency_component = max(0.0, 1.0 - max(recency_hours, 0) / 24) * 15
    return round(
        mention_component
        + importance_component
        + growth_component
        + recency_component
    )
