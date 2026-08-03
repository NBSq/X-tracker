from __future__ import annotations

import contextvars
import re
import uuid
from contextlib import contextmanager
from typing import Iterator


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_correlation_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)


def new_correlation_id() -> str:
    return uuid.uuid4().hex


def safe_correlation_id(value: str | None) -> str:
    candidate = str(value or "").strip()
    return candidate if _SAFE_ID.fullmatch(candidate) else new_correlation_id()


def correlation_id() -> str:
    current = _correlation_id.get()
    if current is None:
        current = new_correlation_id()
        _correlation_id.set(current)
    return current


def request_id() -> str | None:
    return _request_id.get()


@contextmanager
def correlation_scope(
    value: str | None = None, *, request: str | None = None,
) -> Iterator[str]:
    selected = safe_correlation_id(value)
    correlation_token = _correlation_id.set(selected)
    request_token = _request_id.set(request or selected)
    try:
        yield selected
    finally:
        _request_id.reset(request_token)
        _correlation_id.reset(correlation_token)
