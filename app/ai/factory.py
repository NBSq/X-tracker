from __future__ import annotations

from typing import Any

from app.ai.openai_analyzer import OpenAISignalAnalyzer
from app.ai.service import SignalReasoningService
from app.config import Config
from app.db.database import Database
from app.events.bus import EventBus


def create_signal_reasoning_service(
    db: Database,
    config: Config,
    *,
    event_bus: EventBus | None = None,
    force_mock: bool = False,
    openai_client: Any | None = None,
) -> SignalReasoningService:
    analyzer = None
    if not force_mock and config.ai_provider in {"openai", "auto"}:
        if config.openai_api_key or openai_client is not None:
            analyzer = OpenAISignalAnalyzer(
                config.openai_api_key or "test-key",
                config.openai_model,
                timeout_seconds=config.openai_timeout_seconds,
                max_output_tokens=config.openai_max_output_tokens,
                store_responses=config.openai_store_responses,
                max_post_length=config.openai_max_post_length,
                client=openai_client,
            )
    return SignalReasoningService(
        db,
        config,
        event_bus=event_bus,
        openai_analyzer=analyzer,
        force_mock=force_mock,
    )
