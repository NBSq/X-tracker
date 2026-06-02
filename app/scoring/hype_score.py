from dataclasses import dataclass


@dataclass(frozen=True)
class HypeSignal:
    kind: str
    name: str
    mentions_count: int
    average_importance: float
    hype_score: float


def calculate_hype_score(mentions_count: int, average_importance: float) -> float:
    return mentions_count * average_importance


def build_hype_signal(row) -> HypeSignal:
    mentions_count = int(row["mentions_count"])
    average_importance = float(row["average_importance"])
    return HypeSignal(
        kind=str(row["kind"]),
        name=str(row["name"]),
        mentions_count=mentions_count,
        average_importance=average_importance,
        hype_score=calculate_hype_score(mentions_count, average_importance),
    )
