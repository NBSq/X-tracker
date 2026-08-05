from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

import uvicorn

from app.config import Config, load_config
from app.dashboard import create_app
from app.db.database import Database
from app.ingestion.sources import load_source_definitions
from app.main import run_rss_once, validate_rss_config
from app.observability.context import correlation_scope
from app.observability.logging import configure_logging, log_event


logger = logging.getLogger("x_narrative_tracker.deployment")


def validate_startup(config: Config) -> None:
    """Fail fast on critical local configuration without calling external services."""
    validate_rss_config(config, mock_ai=config.ai_provider == "mock")
    required_files = {
        "narratives": config.narratives_path,
        "content sources": config.content_sources_path,
    }
    missing = [name for name, path in required_files.items() if not path.is_file()]
    if missing:
        raise RuntimeError(f"Missing required configuration: {', '.join(missing)}")
    load_source_definitions(config.content_sources_path)

    database_dir = config.database_path.parent
    database_dir.mkdir(parents=True, exist_ok=True)
    _assert_writable_directory(database_dir)

    db = Database(config.database_path)
    try:
        db.initialize()
        db.connection.execute("SELECT 1").fetchone()
    finally:
        db.close()

    logger.info(
        "Container startup validated: database=%s sources=%d ai_provider=%s "
        "telegram_configured=%s tracker_enabled=%s",
        config.database_path,
        len(load_source_definitions(config.content_sources_path)),
        config.ai_provider,
        bool(config.telegram_bot_token and config.telegram_chat_id),
        config.tracker_enabled,
        extra={"event": "container_startup_validated", "component": "deployment"},
    )


def _assert_writable_directory(path: Path) -> None:
    probe = path / f".write-test-{os.getpid()}"
    try:
        probe.touch(exist_ok=False)
    except OSError as exc:
        raise RuntimeError(f"Directory is not writable: {path}") from exc
    finally:
        try:
            probe.unlink()
        except FileNotFoundError:
            pass


def tracker_loop(config: Config, stop_event: threading.Event) -> None:
    db = Database(config.database_path)
    try:
        db.initialize()
        while not stop_event.is_set():
            with correlation_scope():
                try:
                    run_rss_once(
                        config,
                        db,
                        mock_ai=config.ai_provider == "mock",
                    )
                except Exception:
                    logger.exception("Container tracker cycle failed")
            stop_event.wait(config.fetch_interval_seconds)
    finally:
        db.close()
        log_event(
            logger, logging.INFO, "tracker_stopped", "Container tracker stopped",
            component="deployment",
        )


def main() -> None:
    config = load_config()
    configure_logging(config)
    validate_startup(config)

    stop_event = threading.Event()
    tracker: threading.Thread | None = None
    if config.tracker_enabled:
        tracker = threading.Thread(
            target=tracker_loop,
            args=(config, stop_event),
            name="rss-tracker",
            daemon=True,
        )
        tracker.start()
    else:
        logger.info("Container tracker disabled by TRACKER_ENABLED=false")

    try:
        uvicorn.run(
            create_app(config.database_path, config=config),
            host=config.dashboard_host,
            port=config.dashboard_port,
            log_config=None,
        )
    finally:
        stop_event.set()
        if tracker is not None:
            tracker.join(config.tracker_shutdown_timeout_seconds)
            if tracker.is_alive():
                logger.warning(
                    "Tracker did not stop within %d seconds; process shutdown will continue",
                    config.tracker_shutdown_timeout_seconds,
                    extra={"event": "tracker_shutdown_timeout", "component": "deployment"},
                )


if __name__ == "__main__":
    main()
