from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from app.config import load_config
from app.db.database import Database
from app.deployment import validate_startup


ROOT = Path(__file__).resolve().parent.parent


def test_dockerfile_uses_non_root_slim_runtime_and_healthcheck():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert text.startswith("FROM python:3.12-slim")
    assert "USER 10001:10001" in text
    assert "HEALTHCHECK" in text
    assert "app.deployment" in text
    assert "COPY ." not in text


def test_dockerignore_excludes_secrets_databases_and_caches():
    text = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for required in (".env", ".venv", "**/__pycache__", ".pytest_cache", "*.sqlite3", "exports"):
        assert required in text
    assert "!.env.example" in text
    assert "!.env.production.example" in text


def test_compose_persists_state_and_hardens_the_container():
    text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "tracker_data:/app/data" in text
    assert "tracker_exports:/app/exports" in text
    assert "read_only: true" in text
    assert "no-new-privileges:true" in text
    assert "cap_drop:" in text and "- ALL" in text
    assert "127.0.0.1:8000/ready" in text


def test_container_path_overrides_are_absolute():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        environment = {
            "DATABASE_PATH": str(root / "data" / "tracker.sqlite3"),
            "NARRATIVES_PATH": str(root / "resources" / "narratives.json"),
            "DASHBOARD_HOST": "0.0.0.0",
            "DASHBOARD_PORT": "8000",
            "TRACKER_ENABLED": "false",
        }
        with patch.dict(os.environ, environment, clear=False):
            config = load_config()
        assert config.database_path == root / "data" / "tracker.sqlite3"
        assert config.narratives_path == root / "resources" / "narratives.json"
        assert config.dashboard_host == "0.0.0.0"
        assert config.tracker_enabled is False


def test_startup_validation_initializes_database_without_network_calls():
    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "state" / "tracker.sqlite3"
        config = replace(
            load_config(),
            database_path=database_path,
            content_sources_path=ROOT / "config" / "sources.json",
            narratives_path=ROOT / "data" / "narratives.json",
            ai_provider="mock",
            openai_api_key=None,
            telegram_bot_token=None,
            telegram_chat_id=None,
            tracker_enabled=False,
        )
        validate_startup(config)
        db = Database(database_path)
        try:
            assert db.has_table("analyzed_posts")
            assert db.connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
            assert db.connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        finally:
            db.close()


def test_production_example_contains_placeholders_only():
    text = (ROOT / ".env.production.example").read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=" in text
    assert "TELEGRAM_BOT_TOKEN=" in text
    assert "AI_PROVIDER=mock" in text
    assert "sk-" not in text
