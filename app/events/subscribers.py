from __future__ import annotations

import logging

from app.alerts.telegram import TelegramAlerter
from app.db.database import Database
from app.events.bus import EventBus
from app.events.models import (
    PerformanceUpdated,
    SignalCreated,
    SignalEvaluated,
    WatchlistMatched,
)
from app.rules.engine import RuleEngine
from app.watchlists.service import WatchlistService


logger = logging.getLogger("x_narrative_tracker")


class SignalDatabaseStorage:
    def __init__(self, db: Database) -> None:
        self.db = db

    def __call__(self, event: SignalCreated) -> None:
        for signal in (event.alert.signal, event.alert.merged_signal):
            if signal is None:
                continue
            self.db.save_alert(
                signal.kind,
                signal.name,
                signal.hype_score,
                signal.mentions_count,
                signal.average_importance,
            )


class SignalPerformanceTracker:
    def __init__(self, db: Database, event_bus: EventBus) -> None:
        self.db = db
        self.event_bus = event_bus

    def __call__(self, event: SignalCreated) -> None:
        self.db.save_signal_history(**event.history_record())
        self.event_bus.publish(_performance_updated(self.db))


class SignalOutcomeStorage:
    def __init__(self, db: Database, event_bus: EventBus) -> None:
        self.db = db
        self.event_bus = event_bus

    def __call__(self, event: SignalEvaluated) -> None:
        self.db.save_signal_outcome(
            signal_id=event.signal_id,
            hours_after=event.hours_after,
            status=event.status,
            score_change=event.score_change,
            mentions_change=event.mentions_change,
            momentum_change=event.momentum_change,
            notes=event.notes,
            evaluation_window_hours=event.evaluation_window_hours,
            original_hype_score=event.original_hype_score,
            current_hype_score=event.current_hype_score,
            original_momentum_score=event.original_momentum_score,
            current_momentum_score=event.current_momentum_score,
            original_mentions=event.original_mentions,
            current_mentions=event.current_mentions,
        )
        self.event_bus.publish(_performance_updated(self.db))


class WatchlistSignalMatcher:
    def __init__(self, db: Database, event_bus: EventBus) -> None:
        self.db = db
        self.event_bus = event_bus
        self.watchlists = WatchlistService(db)

    def __call__(self, event: SignalCreated) -> None:
        matches = self.watchlists.find_matching_watchlists(event)
        if not matches:
            return
        signal_id = self.db.find_signal_history_id(event.history_record())
        if signal_id is None:
            logger.warning("Watchlist matching skipped: signal history row was not found")
            return
        self.watchlists.associate_signal(signal_id, matches)
        items = [item for match in matches for item in match.items]
        self.event_bus.publish(
            WatchlistMatched(
                signal_id=signal_id,
                watchlist_ids=tuple(match.watchlist.id for match in matches),
                watchlist_names=tuple(match.watchlist.name for match in matches),
                matched_tokens=tuple(
                    dict.fromkeys(
                        item.item_value for item in items if item.item_type == "token"
                    )
                ),
                matched_narratives=tuple(
                    dict.fromkeys(
                        item.item_value
                        for item in items
                        if item.item_type == "narrative"
                    )
                ),
                highest_priority=max(match.watchlist.priority for match in matches),
            )
        )


class TelegramSignalNotifier:
    def __init__(self, db: Database, telegram: TelegramAlerter) -> None:
        self.db = db
        self.telegram = telegram

    def __call__(self, event: SignalCreated) -> None:
        try:
            signal_id = self.db.find_signal_history_id(event.history_record())
            rows = (
                self.db.get_signal_watchlists(signal_id, telegram_only=True)
                if signal_id is not None
                else []
            )
            watchlist_names = (
                tuple(str(row["name"]) for row in rows)
                if isinstance(rows, (list, tuple))
                else ()
            )
            if watchlist_names:
                self.telegram.send_hype_alert(event.alert, watchlist_names)
            else:
                self.telegram.send_hype_alert(event.alert)
            name = " + ".join(
                item for item in (event.token, event.narrative) if item
            )
            logger.info("Telegram alert sent for %s", name)
        except Exception:
            logger.exception("Telegram alert failed")


def register_default_subscribers(
    event_bus: EventBus,
    db: Database,
    telegram: TelegramAlerter | None,
) -> None:
    event_bus.subscribe(SignalCreated, SignalPerformanceTracker(db, event_bus))
    event_bus.subscribe(SignalCreated, WatchlistSignalMatcher(db, event_bus))
    event_bus.subscribe(SignalCreated, RuleEngine(db, telegram))
    if telegram is not None:
        event_bus.subscribe(SignalCreated, TelegramSignalNotifier(db, telegram))
    event_bus.subscribe(SignalCreated, SignalDatabaseStorage(db))
    event_bus.subscribe(SignalEvaluated, SignalOutcomeStorage(db, event_bus))


def _performance_updated(db: Database) -> PerformanceUpdated:
    generated = db.get_signal_performance_summary()
    outcomes = db.get_signal_outcome_summary()
    evaluated = _row_int(outcomes, "signals_evaluated")
    success = _row_int(outcomes, "success")
    return PerformanceUpdated(
        signals_generated=_row_int(generated, "signals_generated"),
        signals_evaluated=evaluated,
        success_rate=(success / evaluated * 100.0) if evaluated else 0.0,
    )


def _row_int(row, key: str) -> int:
    try:
        value = row[key]
    except (KeyError, TypeError):
        return 0
    if isinstance(value, (int, float, str)):
        return int(value or 0)
    return 0
