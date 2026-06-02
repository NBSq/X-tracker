from __future__ import annotations

import json
import time
from pathlib import Path

from app.ai.analyzer import OpenAIAnalyzer
from app.alerts.telegram import TelegramAlerter
from app.config import Config, load_config
from app.db.database import Database
from app.scoring.hype_score import build_hype_signal
from app.sources.x_client import XClient


def load_accounts(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return [str(username).lstrip("@") for username in data.get("accounts", [])]


def load_narratives(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return [str(narrative) for narrative in data.get("narratives", [])]


def process_once(config: Config, db: Database) -> None:
    accounts = load_accounts(config.accounts_path)
    narratives = load_narratives(config.narratives_path)
    x_client = XClient(config.x_bearer_token)
    analyzer = OpenAIAnalyzer(config.openai_api_key, config.openai_model, narratives)
    alerter = TelegramAlerter(config.telegram_bot_token, config.telegram_chat_id)

    posts = x_client.fetch_recent_posts(accounts, config.posts_per_account)
    print(f"Fetched {len(posts)} posts")

    analyzed_count = 0
    for post in posts:
        if db.has_post(post.id):
            continue

        analysis = analyzer.analyze_post(post.text)
        db.save_analysis(post, analysis)
        analyzed_count += 1
        print(f"Analyzed @{post.username}: {analysis.summary}")

    print(f"Saved {analyzed_count} new analyses")

    for row in db.get_recent_signal_stats():
        signal = build_hype_signal(row)
        if signal.hype_score < config.hype_alert_threshold:
            continue
        if db.alert_recently_sent(signal.kind, signal.name):
            continue

        alerter.send_hype_alert(signal)
        db.save_alert(
            signal.kind,
            signal.name,
            signal.hype_score,
            signal.mentions_count,
            signal.average_importance,
        )
        print(f"Alert sent for {signal.kind}: {signal.name}")


def main() -> None:
    config = load_config()
    db = Database(config.database_path)
    db.initialize()

    try:
        while True:
            started_at = time.time()
            try:
                process_once(config, db)
            except Exception as exc:
                print(f"Run failed: {exc}")

            elapsed = time.time() - started_at
            sleep_seconds = max(0, config.fetch_interval_seconds - elapsed)
            print(f"Sleeping for {sleep_seconds:.0f} seconds")
            time.sleep(sleep_seconds)
    finally:
        db.close()


if __name__ == "__main__":
    main()
