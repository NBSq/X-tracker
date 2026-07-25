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
    return config
