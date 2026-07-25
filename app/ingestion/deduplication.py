from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from app.config import Config
from app.db.database import Database
from app.ingestion.models import DeduplicationMatch, NormalizedContentItem, normalize_title


COMMON_TOKENS = frozenset(
    "BTC ETH SOL BNB XRP DOGE ADA AVAX LINK TON ARB OP SUI APT INJ SEI TAO FET "
    "RNDR NEAR TIA JUP WIF PEPE BONK USDT USDC".split()
)
STOP_WORDS = frozenset(
    "a an and are as at be by for from has in into is it its of on or that the "
    "this to was will with after amid says".split()
)


@dataclass(frozen=True)
class Similarity:
    title: float
    body: float
    shared_entities: int

    @property
    def score(self) -> float:
        return round(max(self.title, self.body), 4)


class DeduplicationService:
    def __init__(self, db: Database, config: Config) -> None:
        self.db = db
        self.config = config

    def match(self, source_id: int, item: NormalizedContentItem) -> DeduplicationMatch:
        exact, reason = self.db.find_exact_content_duplicate(source_id, item)
        if exact is not None:
            event_id = self.db.get_unified_event_for_content(int(exact["id"]))
            return DeduplicationMatch(
                matched=True,
                reason=reason,
                content_item_id=int(exact["id"]),
                unified_event_id=event_id,
                similarity_score=1.0,
                exact=True,
            )
        if not self.config.deduplication_enabled:
            return DeduplicationMatch(False)

        best: DeduplicationMatch | None = None
        for candidate in self.db.get_near_duplicate_candidates(
            item.fetched_at,
            self.config.deduplication_time_window_hours,
        ):
            if (
                self.config.deduplication_cross_source_only
                and int(candidate["source_id"]) == source_id
            ):
                continue
            if not _inside_publication_window(
                item.published_at,
                candidate["published_at"],
                self.config.deduplication_time_window_hours,
            ):
                continue
            similarity = compare_content(item, candidate)
            if (
                similarity.shared_entities
                < self.config.deduplication_min_shared_entities
            ):
                continue
            title_match = (
                similarity.title
                >= self.config.deduplication_title_similarity_threshold
            )
            body_match = (
                similarity.body
                >= self.config.deduplication_body_similarity_threshold
            )
            if not title_match and not body_match:
                continue
            event_id = candidate["unified_event_id"]
            if event_id is None:
                continue
            reason = "near_title_similarity" if title_match else "near_body_similarity"
            match = DeduplicationMatch(
                True,
                reason,
                int(candidate["id"]),
                int(event_id),
                similarity.score,
                False,
            )
            if best is None or match.similarity_score > best.similarity_score:
                best = match
        return best or DeduplicationMatch(False)


def compare_content(item: NormalizedContentItem, candidate) -> Similarity:
    title = jaccard_similarity(item.title, str(candidate["title"]))
    body = jaccard_similarity(item.body, str(candidate["body"]))
    item_entities = infer_entities(
        item.title + " " + item.body,
        item.tokens,
        item.narratives,
    )
    candidate_entities = infer_entities(
        str(candidate["title"]) + " " + str(candidate["body"]),
        json.loads(candidate["tokens_json"] or "[]"),
        json.loads(candidate["narratives_json"] or "[]"),
    )
    return Similarity(title, body, len(item_entities & candidate_entities))


def jaccard_similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def infer_entities(text: str, tokens=(), narratives=()) -> set[str]:
    entities = {str(item).casefold() for item in (*tokens, *narratives)}
    words = set(re.findall(r"\b[A-Za-z][A-Za-z0-9]{1,14}\b", text))
    entities.update(word.upper().casefold() for word in words if word.upper() in COMMON_TOKENS)
    lowered = text.casefold()
    narratives_map = {
        "ai agents": ("ai agent", "artificial intelligence"),
        "rwa": ("real world asset", "tokenized treasury"),
        "memecoins": ("memecoin", "meme coin"),
        "etfs": (" etf", "blackrock"),
        "regulation": ("regulation", " sec ", "mica"),
        "stablecoins": ("stablecoin", "usdt", "usdc"),
    }
    for narrative, keywords in narratives_map.items():
        if any(keyword in f" {lowered} " for keyword in keywords):
            entities.add(narrative)
    return entities


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in normalize_title(value).split()
        if len(token) > 1 and token not in STOP_WORDS
    }


def _inside_publication_window(left, right, hours: int) -> bool:
    if not left or not right:
        return True
    try:
        left_dt = datetime.fromisoformat(str(left).replace("Z", "+00:00"))
        right_dt = datetime.fromisoformat(str(right).replace("Z", "+00:00"))
        if left_dt.tzinfo is None:
            left_dt = left_dt.replace(tzinfo=timezone.utc)
        if right_dt.tzinfo is None:
            right_dt = right_dt.replace(tzinfo=timezone.utc)
        return abs((left_dt - right_dt).total_seconds()) <= hours * 3600
    except ValueError:
        return True
