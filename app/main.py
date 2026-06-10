from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Protocol

from app.ai.analyzer import AnalysisResult, LocalAnalyzer, OpenAIAnalyzer, SpikeInsight
from app.alerts.telegram import (
    AlertPost,
    DailyDigest,
    HypeAlert,
    NarrativeSummary,
    NarrativeGrowth,
    NarrativeTrend,
    SummaryItem,
    TelegramAlerter,
    TrendReport,
    format_hype_alert,
    format_daily_digest,
    format_summary,
    format_trend_report,
)
from app.config import Config, load_config
from app.db.database import Database
from app.scoring.hype_score import build_hype_signal
from app.sources.local_client import load_sample_posts
from app.sources.rss_client import RSSClient, load_rss_feeds
from app.sources.x_client import XClient, XPost


logger = logging.getLogger("x_narrative_tracker")


class Analyzer(Protocol):
    def analyze_post(self, text: str) -> AnalysisResult: ...
    def explain_spike(
        self,
        kind: str,
        name: str,
        hype_score: float,
        top_posts: list[str],
        related_tokens: list[str],
        related_narratives: list[str],
    ) -> SpikeInsight: ...


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Track crypto narratives from X posts")
    parser.add_argument(
        "--mode",
        choices=("live", "local", "rss"),
        default="live",
        help="Use X, RSS, or run the offline sample-post MVP",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Disable Telegram sending and keep console alerts only",
    )
    parser.add_argument(
        "--reset-db",
        action="store_true",
        help="Delete previous analyses and alerts before running",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print a narrative summary after processing and optionally send it to Telegram",
    )
    parser.add_argument(
        "--mock-ai",
        action="store_true",
        help="Use deterministic keyword analysis instead of the OpenAI API",
    )
    parser.add_argument(
        "--trend-report",
        action="store_true",
        help="Print narrative trends from stored history and optionally send to Telegram",
    )
    parser.add_argument(
        "--daily-digest",
        action="store_true",
        help="Print a 24-hour digest and optionally send it to Telegram",
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
    db.save_narrative_score_history(db.get_recent_signal_stats())
    evaluate_hype(config, db, telegram, analyzer)


def evaluate_hype(
    config: Config,
    db: Database,
    telegram: TelegramAlerter | None,
    analyzer: Analyzer,
) -> None:
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

        context_rows = db.get_signal_posts(signal.kind, signal.name)
        top_posts = [
            AlertPost(username=str(item["username"]), text=str(item["text"]))
            for item in context_rows
        ]
        related_tokens = sorted(
            {
                str(token)
                for item in context_rows
                for token in json.loads(item["tokens_json"])
            }
        )
        related_narratives = sorted(
            {
                str(narrative)
                for item in context_rows
                for narrative in json.loads(item["narratives_json"])
            }
        )
        post_prompts = [f"@{post.username}: {post.text}" for post in top_posts]
        try:
            insight = analyzer.explain_spike(
                signal.kind,
                signal.name,
                signal.hype_score,
                post_prompts,
                related_tokens,
                related_narratives,
            )
        except Exception:
            logger.exception("Could not generate spike explanation; using local fallback")
            insight = LocalAnalyzer([]).explain_spike(
                signal.kind,
                signal.name,
                signal.hype_score,
                post_prompts,
                related_tokens,
                related_narratives,
            )
        alert = HypeAlert(
            signal=signal,
            insight=insight,
            top_posts=top_posts,
            related_tokens=related_tokens,
            related_narratives=related_narratives,
        )

        logger.warning("\n%s", format_hype_alert(alert))
        if telegram:
            try:
                telegram.send_hype_alert(alert)
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


def build_summary(db: Database) -> NarrativeSummary:
    token_items = []
    narrative_items = []
    for row in db.get_recent_signal_stats():
        signal = build_hype_signal(row)
        item = SummaryItem(name=signal.name, hype_score=signal.hype_score)
        if signal.kind == "token":
            token_items.append(item)
        else:
            narrative_items.append(item)

    token_items.sort(key=lambda item: item.hype_score, reverse=True)
    narrative_items.sort(key=lambda item: item.hype_score, reverse=True)
    important_posts = [
        AlertPost(username=str(row["username"]), text=str(row["text"]))
        for row in db.get_most_important_posts()
    ]
    return NarrativeSummary(
        top_tokens=token_items[:3],
        top_narratives=narrative_items[:3],
        important_posts=important_posts,
    )


def print_and_send_summary(db: Database, telegram: TelegramAlerter | None) -> None:
    summary = build_summary(db)
    logger.info("\n%s", format_summary(summary))
    if telegram:
        try:
            telegram.send_summary(summary)
            logger.info("Telegram summary sent")
        except Exception:
            logger.exception("Telegram summary failed")


def build_trend_report(db: Database) -> TrendReport:
    top_24h = [
        NarrativeTrend(name=str(row["narrative"]), score=float(row["score"]))
        for row in db.get_top_narrative_history(24)
    ]
    top_7d = [
        NarrativeTrend(name=str(row["narrative"]), score=float(row["score"]))
        for row in db.get_top_narrative_history(24 * 7)
    ]
    fastest_growing = [
        NarrativeGrowth(
            name=str(row["narrative"]),
            growth_percent=float(row["growth_percent"]),
        )
        for row in db.get_fastest_growing_narratives()
    ]
    return TrendReport(
        top_24h=top_24h,
        top_7d=top_7d,
        fastest_growing=fastest_growing,
    )


def print_and_send_trend_report(
    db: Database,
    telegram: TelegramAlerter | None,
) -> None:
    report = build_trend_report(db)
    logger.info("\n%s", format_trend_report(report))
    if telegram:
        try:
            telegram.send_trend_report(report)
            logger.info("Telegram trend report sent")
        except Exception:
            logger.exception("Telegram trend report failed")


def build_daily_digest(db: Database) -> DailyDigest:
    token_items = []
    narrative_items = []
    for row in db.get_signal_stats_for_hours(24):
        signal = build_hype_signal(row)
        item = SummaryItem(name=signal.name, hype_score=signal.hype_score)
        if signal.kind == "token":
            token_items.append(item)
        else:
            narrative_items.append(item)
    token_items.sort(key=lambda item: item.hype_score, reverse=True)
    narrative_items.sort(key=lambda item: item.hype_score, reverse=True)

    growth_rows = db.get_fastest_growing_narratives(limit=1)
    fastest_growing = None
    if growth_rows:
        fastest_growing = NarrativeGrowth(
            name=str(growth_rows[0]["narrative"]),
            growth_percent=float(growth_rows[0]["growth_percent"]),
        )
    important_posts = [
        AlertPost(username=str(row["username"]), text=str(row["text"]))
        for row in db.get_most_important_posts(lookback_minutes=24 * 60, limit=3)
    ]

    top_token = token_items[0].name if token_items else "no token"
    top_narrative = narrative_items[0].name if narrative_items else "no narrative"
    if fastest_growing:
        growth_text = (
            f"{fastest_growing.name} is the fastest-growing narrative "
            f"at {fastest_growing.growth_percent:+.0f}%."
        )
    else:
        growth_text = "There is not enough history to identify narrative growth."
    final_summary = (
        f"{top_token} led token attention while {top_narrative} led narratives. "
        f"{growth_text}"
    )
    return DailyDigest(
        top_tokens=token_items[:5],
        top_narratives=narrative_items[:5],
        fastest_growing=fastest_growing,
        important_posts=important_posts,
        final_summary=final_summary,
    )


def print_and_send_daily_digest(
    db: Database,
    telegram: TelegramAlerter | None,
) -> None:
    digest = build_daily_digest(db)
    logger.info("\n%s", format_daily_digest(digest))
    if telegram:
        try:
            telegram.send_daily_digest(digest)
            logger.info("Telegram daily digest sent")
        except Exception:
            logger.exception("Telegram daily digest failed")


def run_local(
    config: Config,
    db: Database,
    no_telegram: bool = False,
    show_summary: bool = False,
) -> None:
    narratives = load_json_list(config.narratives_path, "narratives")
    posts = load_sample_posts(config.sample_posts_path)
    telegram = build_telegram(config, no_telegram)
    process_posts(
        posts,
        LocalAnalyzer(narratives),
        config,
        db,
        telegram,
    )
    if show_summary:
        print_and_send_summary(db, telegram)


def build_analyzer(
    config: Config,
    narratives: list[str],
    mock_ai: bool = False,
) -> Analyzer:
    if mock_ai:
        logger.info("Mock AI enabled; OpenAI API will not be used")
        return LocalAnalyzer(narratives)
    if not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required unless --mock-ai is enabled")
    return OpenAIAnalyzer(config.openai_api_key, config.openai_model, narratives)


def validate_live_config(config: Config, mock_ai: bool = False) -> None:
    if not config.x_bearer_token:
        raise RuntimeError("X_BEARER_TOKEN is required in live mode")
    if not mock_ai and not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required in live mode")


def validate_rss_config(config: Config, mock_ai: bool = False) -> None:
    if not mock_ai and not config.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is required in RSS mode")


def run_live_once(
    config: Config,
    db: Database,
    no_telegram: bool = False,
    mock_ai: bool = False,
) -> None:
    accounts = [name.lstrip("@") for name in load_json_list(config.accounts_path, "accounts")]
    narratives = load_json_list(config.narratives_path, "narratives")
    posts = XClient(config.x_bearer_token).fetch_recent_posts(accounts, config.posts_per_account)
    analyzer = build_analyzer(config, narratives, mock_ai)
    process_posts(posts, analyzer, config, db, build_telegram(config, no_telegram))


def run_live(
    config: Config,
    db: Database,
    no_telegram: bool = False,
    mock_ai: bool = False,
) -> None:
    validate_live_config(config, mock_ai)
    while True:
        started_at = time.time()
        try:
            run_live_once(config, db, no_telegram, mock_ai)
        except Exception:
            logger.exception("Live run failed")

        elapsed = time.time() - started_at
        sleep_seconds = max(0, config.fetch_interval_seconds - elapsed)
        logger.info("Sleeping for %.0f seconds", sleep_seconds)
        time.sleep(sleep_seconds)


def run_rss_once(
    config: Config,
    db: Database,
    no_telegram: bool = False,
    mock_ai: bool = False,
) -> None:
    feeds = load_rss_feeds(config.rss_feeds_path)
    narratives = load_json_list(config.narratives_path, "narratives")
    posts = RSSClient().fetch_recent_posts(feeds, config.rss_articles_per_feed)
    analyzer = build_analyzer(config, narratives, mock_ai)
    process_posts(posts, analyzer, config, db, build_telegram(config, no_telegram))


def run_rss(
    config: Config,
    db: Database,
    no_telegram: bool = False,
    mock_ai: bool = False,
) -> None:
    validate_rss_config(config, mock_ai)
    while True:
        started_at = time.time()
        try:
            run_rss_once(config, db, no_telegram, mock_ai)
        except Exception:
            logger.exception("RSS run failed")

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
        if args.reset_db:
            db.reset()
            logger.info("Database reset complete")
    except Exception:
        logger.exception("Startup failed")
        raise SystemExit(1)

    try:
        if args.daily_digest:
            print_and_send_daily_digest(db, build_telegram(config, args.no_telegram))
        elif args.trend_report:
            print_and_send_trend_report(db, build_telegram(config, args.no_telegram))
        elif args.mode == "local":
            run_local(config, db, args.no_telegram, args.summary)
        elif args.mode == "rss":
            run_rss(config, db, args.no_telegram, args.mock_ai)
        else:
            run_live(config, db, args.no_telegram, args.mock_ai)
    except KeyboardInterrupt:
        logger.info("Stopped")
    except Exception:
        logger.exception("%s mode failed", args.mode.capitalize())
        raise SystemExit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
