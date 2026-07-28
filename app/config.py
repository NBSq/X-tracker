import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    x_bearer_token: str | None
    openai_api_key: str | None
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    database_path: Path
    openai_model: str
    fetch_interval_seconds: int
    hype_alert_threshold: float
    posts_per_account: int
    rss_articles_per_feed: int
    outcome_evaluation_hours: int
    outcome_success_threshold: float
    outcome_failure_threshold: float
    accounts_path: Path
    narratives_path: Path
    sample_posts_path: Path
    rss_feeds_path: Path
    outcome_evaluation_windows: tuple[int, ...] = (24, 72, 168)
    history_growth_threshold: float = 20.0
    history_minimum_activity: int = 2
    ai_provider: str = "mock"
    openai_timeout_seconds: int = 30
    openai_max_retries: int = 1
    openai_max_output_tokens: int = 700
    openai_min_hype_score: float = 65.0
    openai_min_momentum_score: float = 50.0
    openai_min_confidence: int = 6
    openai_daily_request_limit: int = 100
    openai_cache_ttl_hours: int = 24
    openai_fallback_to_mock: bool = True
    openai_store_responses: bool = False
    openai_max_post_length: int = 600
    content_sources_path: Path = BASE_DIR / "config" / "sources.json"
    source_fetch_timeout_seconds: int = 20
    source_max_retries: int = 2
    source_default_interval_seconds: int = 300
    source_failure_backoff_seconds: int = 600
    source_max_items_per_fetch: int = 100
    source_enabled: bool = True
    deduplication_enabled: bool = True
    deduplication_time_window_hours: int = 24
    deduplication_title_similarity_threshold: float = 0.82
    deduplication_body_similarity_threshold: float = 0.88
    deduplication_min_shared_entities: int = 1
    deduplication_cross_source_only: bool = False
    event_update_notifications: bool = True
    event_update_min_new_sources: int = 2
    event_update_min_hype_change: float = 10.0
    event_update_min_momentum_change: float = 10.0
    event_update_cooldown_minutes: int = 30
    source_alert_after_failures: int = 3
    source_failure_alert_cooldown_minutes: int = 60
    source_recovery_notifications: bool = True
    graph_recency_half_life_days: float = 14.0
    graph_min_edge_weight: float = 0.05
    graph_min_node_weight: float = 0.05
    graph_ai_relationship_min_confidence: float = 0.75
    graph_default_period_days: int = 30
    graph_max_nodes: int = 250
    quality_calculation_version: int = 1
    quality_minimum_evidence: int = 2
    quality_minimum_sample_size: int = 5
    quality_outcome_weight: float = 0.25
    quality_calibration_weight: float = 0.15
    quality_source_reliability_weight: float = 0.15
    quality_evidence_weight: float = 0.10
    quality_source_diversity_weight: float = 0.10
    quality_timeliness_weight: float = 0.10
    quality_rule_precision_weight: float = 0.05
    quality_watchlist_relevance_weight: float = 0.05
    quality_ai_agreement_weight: float = 0.05
    quality_excellent_threshold: float = 85.0
    quality_strong_threshold: float = 70.0
    quality_moderate_threshold: float = 55.0
    quality_weak_threshold: float = 40.0
    quality_timeliness_excellent_minutes: int = 5
    quality_timeliness_good_minutes: int = 15
    quality_timeliness_weak_minutes: int = 60
    quality_change_significance: float = 5.0


def _get_int_env(name: str, default: str) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc


def _get_float_env(name: str, default: str) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc


def _get_bool_env(name: str, default: str) -> bool:
    value = os.getenv(name, default).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false")


def _get_int_tuple_env(name: str, default: str) -> tuple[int, ...]:
    raw_values = os.getenv(name, default).split(",")
    try:
        values = tuple(dict.fromkeys(int(item.strip()) for item in raw_values))
    except ValueError as exc:
        raise RuntimeError(f"{name} must contain comma-separated integers") from exc
    if not values or any(value <= 0 for value in values):
        raise RuntimeError(f"{name} must contain positive evaluation windows")
    return values


def load_config() -> Config:
    load_dotenv()

    legacy_outcome_hours = _get_int_env("OUTCOME_EVALUATION_HOURS", "24")
    windows_default = (
        str(legacy_outcome_hours)
        if "OUTCOME_EVALUATION_HOURS" in os.environ
        and "OUTCOME_EVALUATION_WINDOWS" not in os.environ
        else "24,72,168"
    )

    ai_provider = os.getenv("AI_PROVIDER", "mock").strip().lower()
    if ai_provider not in {"mock", "openai", "auto"}:
        raise RuntimeError("AI_PROVIDER must be mock, openai, or auto")

    config = Config(
        x_bearer_token=os.getenv("X_BEARER_TOKEN"),
        openai_api_key=os.getenv("OPENAI_API_KEY"),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID"),
        database_path=BASE_DIR / os.getenv("DATABASE_PATH", "x_narrative_tracker.sqlite3"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        fetch_interval_seconds=_get_int_env("FETCH_INTERVAL_SECONDS", "900"),
        hype_alert_threshold=_get_float_env("HYPE_ALERT_THRESHOLD", "25"),
        posts_per_account=_get_int_env("POSTS_PER_ACCOUNT", "10"),
        rss_articles_per_feed=_get_int_env("RSS_ARTICLES_PER_FEED", "10"),
        outcome_evaluation_hours=legacy_outcome_hours,
        outcome_evaluation_windows=_get_int_tuple_env(
            "OUTCOME_EVALUATION_WINDOWS",
            windows_default,
        ),
        outcome_success_threshold=_get_float_env("OUTCOME_SUCCESS_THRESHOLD", "10"),
        outcome_failure_threshold=_get_float_env("OUTCOME_FAILURE_THRESHOLD", "-10"),
        accounts_path=BASE_DIR / "data" / "accounts.json",
        narratives_path=BASE_DIR / "data" / "narratives.json",
        sample_posts_path=BASE_DIR / "data" / "sample_posts.json",
        rss_feeds_path=BASE_DIR / "data" / "rss_feeds.json",
        history_growth_threshold=_get_float_env(
            "HISTORY_GROWTH_THRESHOLD",
            "20",
        ),
        history_minimum_activity=_get_int_env(
            "HISTORY_MINIMUM_ACTIVITY",
            "2",
        ),
        ai_provider=ai_provider,
        openai_timeout_seconds=_get_int_env("OPENAI_TIMEOUT_SECONDS", "30"),
        openai_max_retries=_get_int_env("OPENAI_MAX_RETRIES", "1"),
        openai_max_output_tokens=_get_int_env("OPENAI_MAX_OUTPUT_TOKENS", "700"),
        openai_min_hype_score=_get_float_env("OPENAI_MIN_HYPE_SCORE", "65"),
        openai_min_momentum_score=_get_float_env(
            "OPENAI_MIN_MOMENTUM_SCORE",
            "50",
        ),
        openai_min_confidence=_get_int_env("OPENAI_MIN_CONFIDENCE", "6"),
        openai_daily_request_limit=_get_int_env(
            "OPENAI_DAILY_REQUEST_LIMIT",
            "100",
        ),
        openai_cache_ttl_hours=_get_int_env("OPENAI_CACHE_TTL_HOURS", "24"),
        openai_fallback_to_mock=_get_bool_env(
            "OPENAI_FALLBACK_TO_MOCK",
            "true",
        ),
        openai_store_responses=_get_bool_env(
            "OPENAI_STORE_RESPONSES",
            "false",
        ),
        openai_max_post_length=_get_int_env("OPENAI_MAX_POST_LENGTH", "600"),
        content_sources_path=BASE_DIR
        / os.getenv(
            "CONTENT_SOURCES_PATH",
            os.getenv("CONTENT_SOURCES_FILE", "config/sources.json"),
        ),
        source_fetch_timeout_seconds=_get_int_env(
            "SOURCE_FETCH_TIMEOUT_SECONDS", "20"
        ),
        source_max_retries=_get_int_env("SOURCE_MAX_RETRIES", "2"),
        source_default_interval_seconds=_get_int_env(
            "SOURCE_DEFAULT_INTERVAL_SECONDS", "300"
        ),
        source_failure_backoff_seconds=_get_int_env(
            "SOURCE_FAILURE_BACKOFF_SECONDS", "600"
        ),
        source_max_items_per_fetch=_get_int_env(
            "SOURCE_MAX_ITEMS_PER_FETCH", "100"
        ),
        source_enabled=_get_bool_env("SOURCE_ENABLED", "true"),
        deduplication_enabled=_get_bool_env("DEDUPLICATION_ENABLED", "true"),
        deduplication_time_window_hours=_get_int_env(
            "DEDUPLICATION_TIME_WINDOW_HOURS", "24"
        ),
        deduplication_title_similarity_threshold=_get_float_env(
            "DEDUPLICATION_TITLE_SIMILARITY_THRESHOLD", "0.82"
        ),
        deduplication_body_similarity_threshold=_get_float_env(
            "DEDUPLICATION_BODY_SIMILARITY_THRESHOLD", "0.88"
        ),
        deduplication_min_shared_entities=_get_int_env(
            "DEDUPLICATION_MIN_SHARED_ENTITIES", "1"
        ),
        deduplication_cross_source_only=_get_bool_env(
            "DEDUPLICATION_CROSS_SOURCE_ONLY", "false"
        ),
        event_update_notifications=_get_bool_env(
            "EVENT_UPDATE_NOTIFICATIONS", "true"
        ),
        event_update_min_new_sources=_get_int_env(
            "EVENT_UPDATE_MIN_NEW_SOURCES", "2"
        ),
        event_update_min_hype_change=_get_float_env(
            "EVENT_UPDATE_MIN_HYPE_CHANGE", "10"
        ),
        event_update_min_momentum_change=_get_float_env(
            "EVENT_UPDATE_MIN_MOMENTUM_CHANGE", "10"
        ),
        event_update_cooldown_minutes=_get_int_env(
            "EVENT_UPDATE_COOLDOWN_MINUTES", "30"
        ),
        source_alert_after_failures=_get_int_env(
            "SOURCE_ALERT_AFTER_FAILURES", "3"
        ),
        source_failure_alert_cooldown_minutes=_get_int_env(
            "SOURCE_FAILURE_ALERT_COOLDOWN_MINUTES", "60"
        ),
        source_recovery_notifications=_get_bool_env(
            "SOURCE_RECOVERY_NOTIFICATIONS", "true"
        ),
        graph_recency_half_life_days=_get_float_env(
            "GRAPH_RECENCY_HALF_LIFE_DAYS", "14"
        ),
        graph_min_edge_weight=_get_float_env("GRAPH_MIN_EDGE_WEIGHT", "0.05"),
        graph_min_node_weight=_get_float_env("GRAPH_MIN_NODE_WEIGHT", "0.05"),
        graph_ai_relationship_min_confidence=_get_float_env(
            "GRAPH_AI_RELATIONSHIP_MIN_CONFIDENCE", "0.75"
        ),
        graph_default_period_days=_get_int_env("GRAPH_DEFAULT_PERIOD_DAYS", "30"),
        graph_max_nodes=_get_int_env("GRAPH_MAX_NODES", "250"),
        quality_calculation_version=_get_int_env("QUALITY_CALCULATION_VERSION", "1"),
        quality_minimum_evidence=_get_int_env("QUALITY_MINIMUM_EVIDENCE", "2"),
        quality_minimum_sample_size=_get_int_env("QUALITY_MINIMUM_SAMPLE_SIZE", "5"),
        quality_outcome_weight=_get_float_env("QUALITY_WEIGHT_OUTCOME", "0.25"),
        quality_calibration_weight=_get_float_env("QUALITY_WEIGHT_CALIBRATION", "0.15"),
        quality_source_reliability_weight=_get_float_env("QUALITY_WEIGHT_SOURCE_RELIABILITY", "0.15"),
        quality_evidence_weight=_get_float_env("QUALITY_WEIGHT_EVIDENCE", "0.10"),
        quality_source_diversity_weight=_get_float_env("QUALITY_WEIGHT_SOURCE_DIVERSITY", "0.10"),
        quality_timeliness_weight=_get_float_env("QUALITY_WEIGHT_TIMELINESS", "0.10"),
        quality_rule_precision_weight=_get_float_env("QUALITY_WEIGHT_RULE_PRECISION", "0.05"),
        quality_watchlist_relevance_weight=_get_float_env("QUALITY_WEIGHT_WATCHLIST_RELEVANCE", "0.05"),
        quality_ai_agreement_weight=_get_float_env("QUALITY_WEIGHT_AI_AGREEMENT", "0.05"),
        quality_excellent_threshold=_get_float_env("QUALITY_EXCELLENT_THRESHOLD", "85"),
        quality_strong_threshold=_get_float_env("QUALITY_STRONG_THRESHOLD", "70"),
        quality_moderate_threshold=_get_float_env("QUALITY_MODERATE_THRESHOLD", "55"),
        quality_weak_threshold=_get_float_env("QUALITY_WEAK_THRESHOLD", "40"),
        quality_timeliness_excellent_minutes=_get_int_env("QUALITY_TIMELINESS_EXCELLENT_MINUTES", "5"),
        quality_timeliness_good_minutes=_get_int_env("QUALITY_TIMELINESS_GOOD_MINUTES", "15"),
        quality_timeliness_weak_minutes=_get_int_env("QUALITY_TIMELINESS_WEAK_MINUTES", "60"),
        quality_change_significance=_get_float_env("QUALITY_CHANGE_SIGNIFICANCE", "5"),
    )
    if config.openai_timeout_seconds <= 0:
        raise RuntimeError("OPENAI_TIMEOUT_SECONDS must be positive")
    if config.openai_max_retries < 0:
        raise RuntimeError("OPENAI_MAX_RETRIES cannot be negative")
    if config.openai_max_output_tokens <= 0:
        raise RuntimeError("OPENAI_MAX_OUTPUT_TOKENS must be positive")
    if config.openai_max_post_length <= 0:
        raise RuntimeError("OPENAI_MAX_POST_LENGTH must be positive")
    if config.openai_daily_request_limit < 0:
        raise RuntimeError("OPENAI_DAILY_REQUEST_LIMIT cannot be negative")
    if config.openai_cache_ttl_hours <= 0:
        raise RuntimeError("OPENAI_CACHE_TTL_HOURS must be positive")
    if not 0 <= config.openai_min_hype_score <= 100:
        raise RuntimeError("OPENAI_MIN_HYPE_SCORE must be between 0 and 100")
    if not 0 <= config.openai_min_momentum_score <= 100:
        raise RuntimeError("OPENAI_MIN_MOMENTUM_SCORE must be between 0 and 100")
    if not 0 <= config.openai_min_confidence <= 10:
        raise RuntimeError("OPENAI_MIN_CONFIDENCE must be between 0 and 10")
    positive_values = {
        "SOURCE_FETCH_TIMEOUT_SECONDS": config.source_fetch_timeout_seconds,
        "SOURCE_DEFAULT_INTERVAL_SECONDS": config.source_default_interval_seconds,
        "SOURCE_FAILURE_BACKOFF_SECONDS": config.source_failure_backoff_seconds,
        "SOURCE_MAX_ITEMS_PER_FETCH": config.source_max_items_per_fetch,
        "DEDUPLICATION_TIME_WINDOW_HOURS": config.deduplication_time_window_hours,
        "EVENT_UPDATE_MIN_NEW_SOURCES": config.event_update_min_new_sources,
        "EVENT_UPDATE_COOLDOWN_MINUTES": config.event_update_cooldown_minutes,
        "SOURCE_ALERT_AFTER_FAILURES": config.source_alert_after_failures,
        "SOURCE_FAILURE_ALERT_COOLDOWN_MINUTES": config.source_failure_alert_cooldown_minutes,
    }
    for name, value in positive_values.items():
        if value <= 0:
            raise RuntimeError(f"{name} must be positive")
    if config.source_max_retries < 0:
        raise RuntimeError("SOURCE_MAX_RETRIES cannot be negative")
    if config.deduplication_min_shared_entities < 0:
        raise RuntimeError("DEDUPLICATION_MIN_SHARED_ENTITIES cannot be negative")
    for name, value in {
        "DEDUPLICATION_TITLE_SIMILARITY_THRESHOLD": config.deduplication_title_similarity_threshold,
        "DEDUPLICATION_BODY_SIMILARITY_THRESHOLD": config.deduplication_body_similarity_threshold,
    }.items():
        if not 0 <= value <= 1:
            raise RuntimeError(f"{name} must be between 0 and 1")
    if config.graph_recency_half_life_days <= 0:
        raise RuntimeError("GRAPH_RECENCY_HALF_LIFE_DAYS must be positive")
    for name, value in {
        "GRAPH_MIN_EDGE_WEIGHT": config.graph_min_edge_weight,
        "GRAPH_MIN_NODE_WEIGHT": config.graph_min_node_weight,
        "GRAPH_AI_RELATIONSHIP_MIN_CONFIDENCE": config.graph_ai_relationship_min_confidence,
    }.items():
        if not 0 <= value <= 1:
            raise RuntimeError(f"{name} must be between 0 and 1")
    if config.graph_default_period_days <= 0:
        raise RuntimeError("GRAPH_DEFAULT_PERIOD_DAYS must be positive")
    if not 1 <= config.graph_max_nodes <= 2000:
        raise RuntimeError("GRAPH_MAX_NODES must be between 1 and 2000")
    if config.quality_calculation_version <= 0:
        raise RuntimeError("QUALITY_CALCULATION_VERSION must be positive")
    if config.quality_minimum_evidence <= 0 or config.quality_minimum_sample_size <= 0:
        raise RuntimeError("Quality minimum evidence and sample size must be positive")
    quality_weights = (
        config.quality_outcome_weight, config.quality_calibration_weight,
        config.quality_source_reliability_weight, config.quality_evidence_weight,
        config.quality_source_diversity_weight, config.quality_timeliness_weight,
        config.quality_rule_precision_weight, config.quality_watchlist_relevance_weight,
        config.quality_ai_agreement_weight,
    )
    if any(weight < 0 for weight in quality_weights) or sum(quality_weights) <= 0:
        raise RuntimeError("Quality weights must be non-negative with a positive total")
    thresholds = (
        config.quality_excellent_threshold, config.quality_strong_threshold,
        config.quality_moderate_threshold, config.quality_weak_threshold,
    )
    if not (100 >= thresholds[0] > thresholds[1] > thresholds[2] > thresholds[3] >= 0):
        raise RuntimeError("Quality classification thresholds must descend within 0-100")
    timing = (
        config.quality_timeliness_excellent_minutes,
        config.quality_timeliness_good_minutes,
        config.quality_timeliness_weak_minutes,
    )
    if not (0 < timing[0] < timing[1] < timing[2]):
        raise RuntimeError("Quality timeliness thresholds must be positive and increasing")
    if config.quality_change_significance < 0:
        raise RuntimeError("QUALITY_CHANGE_SIGNIFICANCE cannot be negative")
    return config
