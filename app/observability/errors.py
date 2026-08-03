from __future__ import annotations

import json
import sqlite3
from typing import Any

import requests


def classify_error(exc: BaseException) -> str:
    if isinstance(exc, (json.JSONDecodeError, UnicodeError)):
        return "parsing"
    if isinstance(exc, (ValueError, TypeError)):
        return "validation"
    if isinstance(exc, sqlite3.Error):
        return "database"
    if isinstance(exc, (requests.Timeout, TimeoutError)):
        return "timeout"
    if isinstance(exc, requests.ConnectionError):
        return "network"
    if isinstance(exc, requests.HTTPError):
        status = getattr(exc.response, "status_code", 0)
        if status == 401 or status == 403:
            return "authentication"
        if status == 429:
            return "rate_limit"
        return "external_service"
    if isinstance(exc, RuntimeError):
        return "configuration" if "config" in str(exc).lower() else "internal"
    return "unknown"
