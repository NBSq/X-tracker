from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.observability.context import correlation_id, request_id


_SECRET_KEYS = re.compile(
    r"(api[_-]?key|token|authorization|password|secret|bearer|openai_api_key|telegram_bot_token)",
    re.IGNORECASE,
)
_SAFE_FIELDS = (
    "event", "request_id", "correlation_id", "signal_id", "unified_event_id",
    "source_id", "rule_id", "watchlist_id", "duration_ms", "error_type",
    "operation", "component", "status_code", "method", "route",
)


def redact(value: Any, key: str = "") -> Any:
    if _SECRET_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(bot)\d{6,}:[A-Za-z0-9_-]+", r"\1[REDACTED]", text)
    return text


class ContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = getattr(record, "correlation_id", None) or correlation_id()
        record.request_id = getattr(record, "request_id", None) or request_id()
        record.event = getattr(record, "event", None) or "log"
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, include_timestamp: bool = True) -> None:
        super().__init__()
        self.include_timestamp = include_timestamp

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", "log"),
            "message": redact(record.getMessage()),
        }
        if self.include_timestamp:
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        for field in _SAFE_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = redact(value, field)
        if record.exc_info:
            payload["error_type"] = payload.get("error_type") or record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


class TextFormatter(logging.Formatter):
    def __init__(self, include_timestamp: bool = True) -> None:
        prefix = "%(asctime)s | " if include_timestamp else ""
        super().__init__(prefix + "%(levelname)s | %(message)s")

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(config: Any | None = None) -> None:
    level_name = str(getattr(config, "log_level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    log_format = str(getattr(config, "log_format", "text")).lower()
    include_timestamp = bool(getattr(config, "log_include_timestamp", True))
    handler = logging.StreamHandler()
    handler.addFilter(ContextFilter())
    handler.setFormatter(
        JsonFormatter(include_timestamp) if log_format == "json"
        else TextFormatter(include_timestamp)
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


def log_event(
    logger: logging.Logger, level: int, event: str, message: str, **fields: Any,
) -> None:
    safe = {key: redact(value, key) for key, value in fields.items() if value is not None}
    logger.log(level, message, extra={"event": event, **safe})
