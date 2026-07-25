from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from app.db.database import Database
from app.events.models import SignalCreated
from app.watchlists.models import (
    WATCHLIST_ITEM_TYPES,
    Watchlist,
    WatchlistItem,
    WatchlistMatch,
    WatchlistReport,
    WatchlistValidationError,
)


class WatchlistService:
    def __init__(self, db: Database) -> None:
        self.db = db

    def list_watchlists(self, enabled: bool | None = None) -> list[Watchlist]:
        rows = self.db.get_watchlists(enabled)
        if not isinstance(rows, (list, tuple)):
            return []
        return [Watchlist.from_row(row) for row in rows]

    def get_watchlist(self, identifier: int | str) -> Watchlist | None:
        row = self.db.get_watchlist(identifier)
        return Watchlist.from_row(row) if row is not None else None

    def create_watchlist(
        self,
        name: str,
        description: str = "",
        *,
        enabled: bool = True,
        priority: int = 0,
        minimum_hype_score: float = 0,
        minimum_momentum_score: float = 0,
        minimum_confidence: int = 0,
        telegram_enabled: bool = True,
        include_in_digest: bool = False,
        dashboard_highlight: bool = True,
        case_insensitive: bool = True,
    ) -> Watchlist:
        values = _validate_settings(
            name=name,
            description=description,
            enabled=enabled,
            priority=priority,
            minimum_hype_score=minimum_hype_score,
            minimum_momentum_score=minimum_momentum_score,
            minimum_confidence=minimum_confidence,
            telegram_enabled=telegram_enabled,
            include_in_digest=include_in_digest,
            dashboard_highlight=dashboard_highlight,
            case_insensitive=case_insensitive,
        )
        watchlist_id = self.db.create_watchlist(**values)
        watchlist = self.get_watchlist(watchlist_id)
        if watchlist is None:
            raise RuntimeError("Created watchlist could not be loaded")
        return watchlist

    def update_watchlist(self, identifier: int | str, **changes: Any) -> Watchlist:
        existing = self.get_watchlist(identifier)
        if existing is None:
            raise KeyError(identifier)
        allowed = {
            "name",
            "description",
            "enabled",
            "priority",
            "minimum_hype_score",
            "minimum_momentum_score",
            "minimum_confidence",
            "telegram_enabled",
            "include_in_digest",
            "dashboard_highlight",
            "case_insensitive",
        }
        unknown = set(changes) - allowed
        if unknown:
            raise WatchlistValidationError(
                f"Unsupported watchlist fields: {', '.join(sorted(unknown))}"
            )
        merged = existing.as_dict()
        merged.update(changes)
        values = _validate_settings(**{key: merged[key] for key in allowed})
        self.db.update_watchlist(existing.id, **values)
        updated = self.get_watchlist(existing.id)
        if updated is None:
            raise RuntimeError("Updated watchlist could not be loaded")
        return updated

    def delete_watchlist(self, identifier: int | str) -> bool:
        watchlist = self.get_watchlist(identifier)
        return self.db.delete_watchlist(watchlist.id) if watchlist else False

    def set_enabled(self, identifier: int | str, enabled: bool) -> Watchlist:
        return self.update_watchlist(identifier, enabled=enabled)

    def list_items(self, identifier: int | str) -> list[WatchlistItem]:
        watchlist = self._required(identifier)
        return [
            WatchlistItem.from_row(row)
            for row in self.db.get_watchlist_items(watchlist.id)
        ]

    def add_item(
        self,
        identifier: int | str,
        item_type: str,
        item_value: str,
    ) -> WatchlistItem:
        watchlist = self._required(identifier)
        kind, display, normalized = normalize_item(item_type, item_value)
        item_id = self.db.add_watchlist_item(
            watchlist.id,
            kind,
            display,
            normalized,
        )
        row = self.db.get_watchlist_item(item_id)
        if row is None:
            raise RuntimeError("Created watchlist item could not be loaded")
        return WatchlistItem.from_row(row)

    def remove_item(
        self,
        identifier: int | str,
        item: int | str,
    ) -> bool:
        watchlist = self._required(identifier)
        return self.db.remove_watchlist_item(watchlist.id, item)

    def find_matching_watchlists(
        self,
        signal: SignalCreated | Mapping[str, Any],
    ) -> list[WatchlistMatch]:
        values = _signal_values(signal)
        if (
            values["token"] is None
            and values["narrative"] is None
        ):
            return []
        enabled_watchlists = self.list_watchlists(enabled=True)
        if not enabled_watchlists:
            return []
        items_by_watchlist: dict[int, list[WatchlistItem]] = defaultdict(list)
        for row in self.db.get_watchlist_items_for_enabled_watchlists():
            items_by_watchlist[int(row["watchlist_id"])].append(
                WatchlistItem.from_row(row)
            )
        matches = []
        for watchlist in enabled_watchlists:
            if not _passes_thresholds(watchlist, values):
                continue
            matched_items = tuple(
                item
                for item in items_by_watchlist.get(watchlist.id, [])
                if _item_matches(item, watchlist, values)
            )
            if matched_items:
                matches.append(WatchlistMatch(watchlist, matched_items))
        return sorted(
            matches,
            key=lambda match: (-match.watchlist.priority, match.watchlist.name.casefold()),
        )

    def associate_signal(
        self,
        signal_id: int,
        matches: list[WatchlistMatch],
    ) -> int:
        created = 0
        for match in matches:
            for item in match.items:
                created += int(
                    self.db.save_signal_watchlist(
                        signal_id,
                        match.watchlist.id,
                        item.item_type,
                        item.item_value,
                    )
                )
        return created

    def matching_signal_ids(self, identifier: int | str) -> set[int]:
        watchlist = self._required(identifier)
        return {
            int(row["id"])
            for row in self.db.get_watchlist_signals(watchlist.id, limit=None)
        }

    def report(self, identifier: int | str, days: int = 30) -> WatchlistReport:
        watchlist = self._required(identifier)
        stats = self.db.get_watchlist_performance(watchlist.id, days)
        latest = tuple(
            _signal_row(row)
            for row in self.db.get_watchlist_signals(watchlist.id, limit=10, days=days)
        )
        evaluated = int(stats["evaluated_count"] or 0)
        successful = int(stats["successful_count"] or 0)
        return WatchlistReport(
            watchlist=watchlist,
            items=tuple(self.list_items(watchlist.id)),
            signals_count=int(stats["signals_count"] or 0),
            evaluated_count=evaluated,
            successful_count=successful,
            neutral_count=int(stats["neutral_count"] or 0),
            failed_count=int(stats["failed_count"] or 0),
            success_rate=(successful / evaluated * 100.0) if evaluated else None,
            average_hype_score=_optional_float(stats["average_hype_score"]),
            average_momentum_score=_optional_float(stats["average_momentum_score"]),
            last_matched_at=stats["last_matched_at"],
            latest_matches=latest,
            related_rules=tuple(
                _rule_row(row)
                for row in self.db.get_rules_referencing_watchlist(
                    watchlist.id,
                    watchlist.name,
                )
            ),
            unified_event_count=int(stats["unified_event_count"] or 0),
            raw_article_count=int(stats["raw_article_count"] or 0),
        )

    def _required(self, identifier: int | str) -> Watchlist:
        watchlist = self.get_watchlist(identifier)
        if watchlist is None:
            raise KeyError(identifier)
        return watchlist


def normalize_item(item_type: str, item_value: str) -> tuple[str, str, str]:
    kind = str(item_type).strip().lower()
    if kind not in WATCHLIST_ITEM_TYPES:
        raise WatchlistValidationError(f"Unsupported watchlist item type: {item_type}")
    value = " ".join(str(item_value).strip().split())
    if not value:
        raise WatchlistValidationError("Watchlist item value cannot be empty")
    if len(value) > 120:
        raise WatchlistValidationError("Watchlist item value cannot exceed 120 characters")
    if kind == "token":
        display = value.lstrip("$").upper()
        if not display:
            raise WatchlistValidationError("Token cannot be empty")
        return kind, display, display.casefold()
    return kind, value, value.casefold()


def _validate_settings(**values: Any) -> dict[str, Any]:
    name = " ".join(str(values["name"]).strip().split())
    if not name:
        raise WatchlistValidationError("Watchlist name cannot be empty")
    if len(name) > 120:
        raise WatchlistValidationError("Watchlist name cannot exceed 120 characters")
    description = str(values.get("description", "")).strip()
    if len(description) > 500:
        raise WatchlistValidationError("Watchlist description cannot exceed 500 characters")
    priority = int(values.get("priority", 0))
    confidence = int(values.get("minimum_confidence", 0))
    hype = float(values.get("minimum_hype_score", 0))
    momentum = float(values.get("minimum_momentum_score", 0))
    if not 0 <= priority <= 100:
        raise WatchlistValidationError("Watchlist priority must be between 0 and 100")
    if not 0 <= confidence <= 10:
        raise WatchlistValidationError("Minimum confidence must be between 0 and 10")
    if not 0 <= hype <= 100 or not 0 <= momentum <= 100:
        raise WatchlistValidationError("Hype and momentum thresholds must be between 0 and 100")
    return {
        "name": name,
        "description": description,
        "enabled": bool(values.get("enabled", True)),
        "priority": priority,
        "minimum_hype_score": hype,
        "minimum_momentum_score": momentum,
        "minimum_confidence": confidence,
        "telegram_enabled": bool(values.get("telegram_enabled", True)),
        "include_in_digest": bool(values.get("include_in_digest", False)),
        "dashboard_highlight": bool(values.get("dashboard_highlight", True)),
        "case_insensitive": bool(values.get("case_insensitive", True)),
    }


def _signal_values(signal: SignalCreated | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(signal, SignalCreated):
        return {
            "token": signal.token,
            "narrative": signal.narrative,
            "hype_score": signal.hype_score,
            "momentum_score": signal.momentum_score,
            "confidence": signal.confidence,
        }
    return {
        "token": signal.get("token"),
        "narrative": signal.get("narrative"),
        "hype_score": float(signal.get("hype_score", 0)),
        "momentum_score": float(signal.get("momentum_score", 0)),
        "confidence": int(signal.get("confidence", 0)),
    }


def _passes_thresholds(watchlist: Watchlist, signal: Mapping[str, Any]) -> bool:
    return (
        float(signal["hype_score"]) >= watchlist.minimum_hype_score
        and float(signal["momentum_score"]) >= watchlist.minimum_momentum_score
        and int(signal["confidence"]) >= watchlist.minimum_confidence
    )


def _item_matches(
    item: WatchlistItem,
    watchlist: Watchlist,
    signal: Mapping[str, Any],
) -> bool:
    actual = signal[item.item_type]
    if actual is None:
        return False
    normalized = " ".join(str(actual).strip().split())
    if item.item_type == "token" or watchlist.case_insensitive:
        normalized = normalized.lstrip("$").casefold()
        return normalized == item.normalized_value
    return normalized == item.item_value


def _signal_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "timestamp": row["timestamp"],
        "token": row["token"],
        "narrative": row["narrative"],
        "hype_score": round(float(row["hype_score"]), 1),
        "momentum_score": round(float(row["momentum_score"]), 1),
        "confidence": int(row["confidence"]),
        "outcome_status": row["outcome_status"],
        "matched_at": row["matched_at"],
    }


def _rule_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": str(row["name"]),
        "enabled": bool(row["enabled"]),
        "priority": int(row["priority"]),
    }


def _optional_float(value: Any) -> float | None:
    return round(float(value), 1) if value is not None else None


def format_watchlist_report(report: WatchlistReport) -> str:
    items = "\n".join(f"* {item.item_value}" for item in report.items) or "None"
    latest = "\n".join(
        f"{index}. {item['token'] or item['narrative'] or 'Unknown'} - "
        f"Hype {item['hype_score']:.1f} - {item['outcome_status'] or 'Pending'}"
        for index, item in enumerate(report.latest_matches, start=1)
    ) or "None"
    success_rate = (
        f"{report.success_rate:.1f}%"
        if report.success_rate is not None
        else "collecting outcomes"
    )
    average_hype = (
        f"{report.average_hype_score:.1f}"
        if report.average_hype_score is not None
        else "N/A"
    )
    average_momentum = (
        f"{report.average_momentum_score:.1f}"
        if report.average_momentum_score is not None
        else "N/A"
    )
    return (
        f"Watchlist - {report.watchlist.name}\n\n"
        f"Status: {'Enabled' if report.watchlist.enabled else 'Disabled'}\n"
        f"Priority: {report.watchlist.priority}\n\n"
        f"Items:\n{items}\n\n"
        f"Signals in last 30 days: {report.signals_count}\n"
        f"Evaluated signals: {report.evaluated_count}\n"
        f"Successful: {report.successful_count}\n"
        f"Success rate: {success_rate}\n\n"
        f"Average hype score: {average_hype}\n"
        f"Average momentum score: {average_momentum}\n\n"
        f"Latest matches:\n{latest}\n\n"
        "Note: outcomes measure narrative continuation, not token price profitability."
    )
