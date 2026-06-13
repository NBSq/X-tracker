from dataclasses import dataclass


@dataclass(frozen=True)
class HypeSignal:
    kind: str
    name: str
    mentions_count: int
    average_importance: float
    hype_score: float


@dataclass(frozen=True)
class HypeCandidate:
    signal: HypeSignal
    post_ids: frozenset[str]


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
