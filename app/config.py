import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    x_bearer_token: str
    openai_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    database_path: Path
    openai_model: str
    fetch_interval_seconds: int
    hype_alert_threshold: float
    posts_per_account: int
    accounts_path: Path
    narratives_path: Path


def _get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_config() -> Config:
    load_dotenv()

    return Config(
        x_bearer_token=_get_required_env("X_BEARER_TOKEN"),
        openai_api_key=_get_required_env("OPENAI_API_KEY"),
        telegram_bot_token=_get_required_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_get_required_env("TELEGRAM_CHAT_ID"),
        database_path=BASE_DIR / os.getenv("DATABASE_PATH", "x_narrative_tracker.sqlite3"),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        fetch_interval_seconds=int(os.getenv("FETCH_INTERVAL_SECONDS", "900")),
        hype_alert_threshold=float(os.getenv("HYPE_ALERT_THRESHOLD", "25")),
        posts_per_account=int(os.getenv("POSTS_PER_ACCOUNT", "10")),
        accounts_path=BASE_DIR / "data" / "accounts.json",
        narratives_path=BASE_DIR / "data" / "narratives.json",
    )
