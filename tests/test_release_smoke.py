from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app import __version__
from app.ai.analyzer import LocalAnalyzer
from app.config import Config
from app.dashboard.app import create_app
from app.db.database import Database
from app.graph.service import GraphService
from app.quality.service import SignalQualityService
from app.sources.local_client import load_sample_posts


ROOT = Path(__file__).resolve().parents[1]


def _config(database_path: Path) -> Config:
    return Config(
        x_bearer_token=None,
        openai_api_key=None,
        telegram_bot_token=None,
        telegram_chat_id=None,
        database_path=database_path,
        openai_model="gpt-4o-mini",
        fetch_interval_seconds=900,
        hype_alert_threshold=25,
        posts_per_account=10,
        rss_articles_per_feed=10,
        outcome_evaluation_hours=24,
        outcome_success_threshold=10,
        outcome_failure_threshold=-10,
        accounts_path=ROOT / "data" / "accounts.json",
        narratives_path=ROOT / "data" / "narratives.json",
        sample_posts_path=ROOT / "data" / "sample_posts.json",
        rss_feeds_path=ROOT / "data" / "rss_feeds.json",
        ai_provider="mock",
        content_sources_path=ROOT / "config" / "sources.json",
        tracker_enabled=False,
    )


def test_v1_version_is_canonical_and_reported_by_cli() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app.main", "--version"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert __version__ == "1.0.0"
    assert result.stdout.strip() == __version__


def test_offline_release_components_smoke(tmp_path: Path) -> None:
    config = _config(tmp_path / "release-smoke.sqlite3")
    db = Database(config.database_path)
    db.initialize()

    posts = load_sample_posts(config.sample_posts_path)
    assert len(posts) == 30
    analysis = LocalAnalyzer(["AI agents", "RWA"]).analyze_post(posts[0].text)
    assert analysis.sentiment in {"bullish", "bearish", "neutral"}
    assert 1 <= analysis.importance <= 10
    db.save_analysis(posts[0], analysis)

    graph_result = GraphService(db, config).rebuild()
    assert graph_result["nodes"] >= 0
    quality_summary = SignalQualityService(db, config).summary()
    assert "average_quality_score" in quality_summary
    db.close()

    with TestClient(create_app(config.database_path, config=config)) as client:
        assert client.get("/live").status_code == 200
        assert client.get("/ready").status_code == 200
        version = client.get("/api/system/version")
        assert version.status_code == 200
        assert version.json()["version"] == __version__
        dashboard = client.get("/")
        assert dashboard.status_code == 200
        assert "Overview" in dashboard.text
