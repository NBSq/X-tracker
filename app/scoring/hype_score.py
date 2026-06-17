from dataclasses import dataclass
from math import exp


@dataclass(frozen=True)
class HypeSignal:
    kind: str
    name: str
    mentions_count: int
    average_importance: float
    hype_score: float

    @property
    def display_hype_score(self) -> int:
        return normalize_hype_score(self.hype_score)

    @property
    def hype_label(self) -> str:
        return interpret_display_hype_score(self.display_hype_score)


@dataclass(frozen=True)
class HypeCandidate:
    signal: HypeSignal
    post_ids: frozenset[str]


def calculate_hype_score(mentions_count: int, average_importance: float) -> float:
    return mentions_count * average_importance


def normalize_hype_score(raw_hype_score: float) -> int:
    score = round(100 * (1 - exp(-max(raw_hype_score, 0.0) / 45)))
    return max(0, min(100, score))


def interpret_display_hype_score(display_hype_score: int) -> str:
    if display_hype_score <= 20:
        return "Low"
    if display_hype_score <= 40:
        return "Moderate"
    if display_hype_score <= 60:
        return "Strong"
    if display_hype_score <= 80:
        return "High"
    return "Extreme"


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


def candidate_overlap(left: HypeCandidate, right: HypeCandidate) -> float:
    smaller_size = min(len(left.post_ids), len(right.post_ids))
    if smaller_size == 0:
        return 0.0
    return len(left.post_ids & right.post_ids) / smaller_size


def should_merge_candidates(
    left: HypeCandidate,
    right: HypeCandidate,
    minimum_overlap: float = 2 / 3,
) -> bool:
    if left.signal.kind == right.signal.kind:
        return False
    return candidate_overlap(left, right) >= minimum_overlap
