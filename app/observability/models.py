from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


COMPONENT_STATES = frozenset(
    {"healthy", "degraded", "unhealthy", "disabled", "unknown"}
)


@dataclass(frozen=True)
class ComponentHealth:
    name: str
    status: str
    critical: bool
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def aggregate_state(components: list[ComponentHealth]) -> str:
    if any(item.critical and item.status == "unhealthy" for item in components):
        return "unhealthy"
    if any(item.status in {"degraded", "unhealthy", "unknown"} for item in components):
        return "degraded"
    return "healthy"
