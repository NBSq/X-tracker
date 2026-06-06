from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Protocol

from app.ai.analyzer import AnalysisResult, LocalAnalyzer, OpenAIAnalyzer
from app.alerts.telegram import TelegramAlerter, format_hype_alert
from app.config import Config, load_config
from app.db.database import Database
from app.scoring.hype_score import build_hype_signal
from app.sources.local_client import load_sample_posts
from app.sources.x_client import XClient, XPost


logger = logging.getLogger("x_narrative_tracker")


class Analyzer(Protocol):
    def analyze_post(self, text: str) -> AnalysisResult: ...


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track crypto narratives from X posts")
    parser.add_argument(
        "--mode",
        choices=("live", "local"),
        default="live",
        help="Use X/OpenAI APIs or run the offline sample-post MVP",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Disable Telegram sending and keep console alerts only",
    )
    return parser.parse_args()


def load_json_list(path: Path, key: str) -> list[str]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Configuration file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Configuration file contains invalid JSON: {path}") from exc
    return [str(item) for item in data.get(key, [])]


def process_posts(
    posts: list[XPost],
    analyzer: Analyzer,
    config: Config,
    db: Database,
    telegram: TelegramAlerter | None,
) -> None:
    logger.info("Loaded %d posts", len(posts))
    analyzed_count = 0

    for post in posts:
        if db.has_post(post.id):
            logger.debug("Skipping previously analyzed post %s", post.id)
            continue
        try:
            analysis = analyzer.analyze_post(post.text)
            db.save_analysis(post, analysis)
            analyzed_count += 1
            logger.info("Analyzed @%s: %s", post.username, analysis.summary)
        except Exception:
            logger.exception("Could not analyze post %s", post.id)

    logger.info("Saved %d new analyses", analyzed_count)
    evaluate_hype(config, db, telegram)


def evaluate_hype(config: Config, db: Database, telegram: TelegramAlerter | None) -> None:
    for row in db.get_recent_signal_stats():
        signal = build_hype_signal(row)
        logger.info(
            "Hype score | %s:%s | %.2f (%d mentions, %.2f avg importance)",
            signal.kind,
            signal.name,
            signal.hype_score,
            signal.mentions_count,
            signal.average_importance,
        )
        if signal.hype_score < config.hype_alert_threshold:
            continue
        if db.alert_recently_sent(signal.kind, signal.name):
            continue

        logger.warning("\n%s", format_hype_alert(signal))
        if telegram:
            try:
                telegram.send_hype_alert(signal)
                logger.info("Telegram alert sent for %s:%s", signal.kind, signal.name)
            except Exception:
                logger.exception("Telegram alert failed for %s:%s", signal.kind, signal.name)

        db.save_alert(
            signal.kind,
            signal.name,
            signal.hype_score,
            signal.mentions_count,
            signal.average_importance,
        )


def build_telegram(config: Config, disabled: bool = False) -> TelegramAlerter | None:
    if disabled:
        logger.info("Telegram disabled by --no-telegram")
        return None
    if config.telegram_bot_token and config.telegram_chat_id:
        return TelegramAlerter(config.telegram_bot_token, config.telegram_chat_id)
    logger.info("Telegram not configured")
    return None


def run_local(config: Config, db: Database, no_telegram: bool = False) -> None:
    narratives = load_json_list(config.narratives_path, "narratives")
    posts = load_sample_posts(config.sample_posts_path)
    process_posts(
        posts,
        LocalAnalyzer(narratives),
        config,
        db,
        build_telegram(config, no_telegram),
    )


def validate_live_config(config: Config) -> None:
    if not config.x_bearer_token:
        raise RuntimeError("X_BEARER_TOKEN is required in live mode")
    if not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required in live mode")


def run_live_once(config: Config, db: Database, no_telegram: bool = False) -> None:
    accounts = [name.lstrip("@") for name in load_json_list(config.accounts_path, "accounts")]
    narratives = load_json_list(config.narratives_path, "narratives")
    posts = XClient(config.x_bearer_token).fetch_recent_posts(accounts, config.posts_per_account)
    analyzer = OpenAIAnalyzer(config.openai_api_key, config.openai_model, narratives)
    process_posts(posts, analyzer, config, db, build_telegram(config, no_telegram))


def run_live(config: Config, db: Database, no_telegram: bool = False) -> None:
    validate_live_config(config)
    while True:
        started_at = time.time()
        try:
            run_live_once(config, db, no_telegram)
        except Exception:
            logger.exception("Live run failed")

        elapsed = time.time() - started_at
        sleep_seconds = max(0, config.fetch_interval_seconds - elapsed)
        logger.info("Sleeping for %.0f seconds", sleep_seconds)
        time.sleep(sleep_seconds)


def main() -> None:
    configure_logging()
    args = parse_args()

    try:
        config = load_config()
        db = Database(config.database_path)
        db.initialize()
    except Exception:
        logger.exception("Startup failed")
        raise SystemExit(1)

    try:
        if args.mode == "local":
            run_local(config, db, args.no_telegram)
        else:
            run_live(config, db, args.no_telegram)
    except KeyboardInterrupt:
        logger.info("Stopped")
    except Exception:
        logger.exception("%s mode failed", args.mode.capitalize())
        raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
