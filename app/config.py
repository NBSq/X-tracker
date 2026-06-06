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
    accounts_path: Path
    narratives_path: Path
    sample_posts_path: Path


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


def load_config() -> Config:
    load_dotenv()

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
        accounts_path=BASE_DIR / "data" / "accounts.json",
        narratives_path=BASE_DIR / "data" / "narratives.json",
        sample_posts_path=BASE_DIR / "data" / "sample_posts.json",
    )
