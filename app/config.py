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

    return Config(
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
    )
