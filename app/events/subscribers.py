from __future__ import annotations

import logging
import sqlite3
import json
from datetime import datetime, timedelta, timezone

from app.ai.base import SignalAnalysisUnavailable
from app.ai.service import SignalReasoningService, result_from_row
from app.alerts.telegram import TelegramAlerter
from app.db.database import Database
from app.events.bus import EventBus
from app.events.models import (
    AIAnalysisCompleted,
    PerformanceUpdated,
    SignalCreated,
    SignalEvaluated,
    WatchlistMatched,
    SourceFetchFailed,
    SourceRecovered,
    UnifiedEventMateriallyChanged,
    UnifiedEventCreated,
    UnifiedEventUpdated,
    RuleTriggered,
    GraphUpdated,
    SignalQualityCalculated,
)
from app.graph.service import GraphService
from app.quality.service import SignalQualityService
from app.rules.engine import RuleEngine
from app.rules.models import condition_uses_quality
from app.watchlists.service import WatchlistService
from app.observability.timing import timed


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

    @timed("signal_creation")
    def __call__(self, event: SignalCreated) -> None:
        record = event.history_record()
        unified_event = self.db.find_unified_event_for_signal(
            event.token,
            event.narrative,
        )
        if isinstance(unified_event, (sqlite3.Row, dict)):
            record["unified_event_id"] = int(unified_event["id"])
        self.db.save_signal_history(**record)
        if isinstance(unified_event, (sqlite3.Row, dict)):
            self.db.update_unified_event(
                int(unified_event["id"]),
                hype_score=max(float(unified_event["hype_score"]), event.hype_score),
                momentum_score=max(
                    float(unified_event["momentum_score"]), event.momentum_score
                ),
                confidence=max(int(unified_event["confidence"]), event.confidence),
            )
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

    @timed("watchlist_matching")
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
            analysis_row = (
                self.db.get_signal_ai_analysis(signal_id)
                if signal_id is not None
                else None
            )
            analysis = (
                result_from_row(analysis_row)
                if isinstance(analysis_row, sqlite3.Row)
                else None
            )
            unified_event = (
                self.db.get_signal_unified_event(signal_id)
                if signal_id is not None
                else None
            )
            if isinstance(self.telegram, TelegramAlerter) and unified_event is not None:
                self.telegram.send_hype_alert(
                    event.alert,
                    watchlist_names,
                    analysis,
                    unified_event,
                    self.db.get_unified_event_items(int(unified_event["id"])),
                )
            elif watchlist_names and analysis is not None:
                self.telegram.send_hype_alert(event.alert, watchlist_names, analysis)
            elif watchlist_names:
                self.telegram.send_hype_alert(event.alert, watchlist_names)
            elif analysis is not None:
                self.telegram.send_hype_alert(event.alert, ai_analysis=analysis)
            else:
                self.telegram.send_hype_alert(event.alert)
            name = " + ".join(
                item for item in (event.token, event.narrative) if item
            )
            logger.info("Telegram alert sent for %s", name)
        except Exception:
            logger.exception("Telegram alert failed")


class SignalReasoningSubscriber:
    def __init__(self, db: Database, service: SignalReasoningService) -> None:
        self.db = db
        self.service = service

    def __call__(self, event: SignalCreated) -> None:
        signal_id = self.db.find_signal_history_id(event.history_record())
        if signal_id is None:
            logger.warning("Signal reasoning skipped: signal history row was not found")
            return
        try:
            self.service.analyze_signal(signal_id)
        except SignalAnalysisUnavailable as exc:
            logger.warning("Signal reasoning unavailable: %s", exc)
        except Exception:
            logger.exception("Signal reasoning failed for signal %s", signal_id)


class UnifiedEventTelegramNotifier:
    def __init__(self, db: Database, telegram: TelegramAlerter) -> None:
        self.db = db
        self.telegram = telegram

    def __call__(self, event: UnifiedEventMateriallyChanged) -> None:
        row = self.db.get_unified_event(event.unified_event_id)
        if row is None:
            return
        self.telegram.send_unified_event_update(
            row,
            self.db.get_unified_event_items(event.unified_event_id),
            event.reason,
        )


class SourceHealthTelegramNotifier:
    def __init__(
        self,
        db: Database,
        telegram: TelegramAlerter,
        failure_threshold: int,
        cooldown_minutes: int,
    ) -> None:
        self.db = db
        self.telegram = telegram
        self.failure_threshold = failure_threshold
        self.cooldown_minutes = cooldown_minutes

    def failed(self, event: SourceFetchFailed) -> None:
        if event.consecutive_failures < self.failure_threshold:
            return
        source = self.db.get_content_source(event.source_id)
        if source is None:
            return
        last_alert = source["last_failure_alert_at"]
        if last_alert:
            try:
                sent_at = datetime.fromisoformat(
                    str(last_alert).replace("Z", "+00:00")
                )
                if sent_at.tzinfo is None:
                    sent_at = sent_at.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < sent_at + timedelta(
                    minutes=self.cooldown_minutes
                ):
                    return
            except ValueError:
                pass
        self.telegram.send_source_health_alert(
            event.source_key,
            f"{event.error_type}; {event.consecutive_failures} consecutive failures",
        )
        self.db.mark_source_failure_alert(event.source_id)

    def recovered(self, event: SourceRecovered) -> None:
        self.telegram.send_source_health_alert(
            event.source_key,
            "fetching successfully again",
            recovered=True,
        )


class AIRuleEvaluationSubscriber:
    def __init__(
        self,
        db: Database,
        telegram: TelegramAlerter | None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.engine = RuleEngine(
            db, telegram, rule_scope="ai", event_bus=event_bus
        )

    def __call__(self, event: AIAnalysisCompleted) -> None:
        self.engine.evaluate_saved_signal(event.signal_id)


class GraphEventSubscriber:
    def __init__(self, service: GraphService, db: Database) -> None:
        self.service = service
        self.db = db

    def signal_created(self, event: SignalCreated) -> None:
        signal_id = self.db.find_signal_history_id(event.history_record())
        if signal_id is not None:
            unified_event = self.db.get_signal_unified_event(signal_id)
            if unified_event is not None:
                self.service.update_event(
                    int(unified_event["id"]), publish=False
                )
            self.service.update_signal(signal_id)

    def event_created(self, event: UnifiedEventCreated) -> None:
        self.service.update_event(event.unified_event_id)

    def event_updated(self, event: UnifiedEventUpdated) -> None:
        self.service.update_event(event.unified_event_id)

    def watchlist_matched(self, event: WatchlistMatched) -> None:
        for watchlist_id in event.watchlist_ids:
            self.service.update_watchlist(watchlist_id)
        self.service.update_signal(event.signal_id)

    def rule_triggered(self, event: RuleTriggered) -> None:
        self.service.update_rule_match(event.rule_id, event.signal_id)

    def ai_completed(self, event: AIAnalysisCompleted) -> None:
        self.service.update_ai_analysis(event.signal_id)

    def signal_evaluated(self, event: SignalEvaluated) -> None:
        self.service.update_signal(event.signal_id)


class SignalQualitySubscriber:
    def __init__(self, service: SignalQualityService, db: Database) -> None:
        self.service = service
        self.db = db

    def signal_created(self, event: SignalCreated) -> None:
        signal_id = self.db.find_signal_history_id(event.history_record())
        if signal_id is not None:
            self.service.calculate_signal(signal_id)

    def signal_evaluated(self, event: SignalEvaluated) -> None:
        self.service.calculate_signal(event.signal_id)

    def event_updated(self, event: UnifiedEventUpdated) -> None:
        for signal in self.db.get_signals(limit=None):
            if signal["unified_event_id"] == event.unified_event_id:
                self.service.calculate_signal(int(signal["id"]))

    def ai_completed(self, event: AIAnalysisCompleted) -> None:
        self.service.calculate_signal(event.signal_id)

    def rule_triggered(self, event: RuleTriggered) -> None:
        rule = self.db.get_alert_rule(event.rule_id)
        if rule is not None:
            try:
                condition = json.loads(str(rule["condition"]))
            except (TypeError, ValueError, json.JSONDecodeError):
                condition = {}
            if condition_uses_quality(condition):
                return
        self.service.calculate_signal(event.signal_id)

    def watchlist_matched(self, event: WatchlistMatched) -> None:
        self.service.calculate_signal(event.signal_id)

    def graph_updated(self, event: GraphUpdated) -> None:
        if event.update_reason == "rebuild":
            return
        latest = self.db.get_signals(limit=1)
        if latest:
            self.service.calculate_signal(int(latest[0]["id"]))


class QualityRuleEvaluationSubscriber:
    def __init__(
        self, db: Database, telegram: TelegramAlerter | None,
        event_bus: EventBus,
    ) -> None:
        self.engine = RuleEngine(
            db, telegram, rule_scope="quality", event_bus=event_bus
        )

    def __call__(self, event: SignalQualityCalculated) -> None:
        self.engine.evaluate_saved_signal(event.signal_id)


def register_default_subscribers(
    event_bus: EventBus,
    db: Database,
    telegram: TelegramAlerter | None,
    reasoning: SignalReasoningService | None = None,
    config=None,
) -> None:
    event_bus.subscribe(SignalCreated, SignalPerformanceTracker(db, event_bus))
    event_bus.subscribe(SignalEvaluated, SignalOutcomeStorage(db, event_bus))
    graph_subscriber = None
    if config is not None:
        graph_subscriber = GraphEventSubscriber(
            GraphService(db, config, event_bus), db
        )
        event_bus.subscribe(SignalCreated, graph_subscriber.signal_created)
        event_bus.subscribe(UnifiedEventCreated, graph_subscriber.event_created)
        event_bus.subscribe(UnifiedEventUpdated, graph_subscriber.event_updated)
        event_bus.subscribe(WatchlistMatched, graph_subscriber.watchlist_matched)
        event_bus.subscribe(RuleTriggered, graph_subscriber.rule_triggered)
        event_bus.subscribe(AIAnalysisCompleted, graph_subscriber.ai_completed)
        event_bus.subscribe(SignalEvaluated, graph_subscriber.signal_evaluated)
    event_bus.subscribe(SignalCreated, WatchlistSignalMatcher(db, event_bus))
    event_bus.subscribe(
        SignalCreated,
        RuleEngine(db, telegram, rule_scope="non_ai", event_bus=event_bus),
    )
    if config is not None:
        quality_subscriber = SignalQualitySubscriber(
            SignalQualityService(db, config, event_bus), db
        )
        event_bus.subscribe(SignalCreated, quality_subscriber.signal_created)
        event_bus.subscribe(SignalEvaluated, quality_subscriber.signal_evaluated)
        event_bus.subscribe(UnifiedEventUpdated, quality_subscriber.event_updated)
        event_bus.subscribe(AIAnalysisCompleted, quality_subscriber.ai_completed)
        event_bus.subscribe(RuleTriggered, quality_subscriber.rule_triggered)
        event_bus.subscribe(WatchlistMatched, quality_subscriber.watchlist_matched)
        event_bus.subscribe(GraphUpdated, quality_subscriber.graph_updated)
        event_bus.subscribe(
            SignalQualityCalculated,
            QualityRuleEvaluationSubscriber(db, telegram, event_bus),
        )
    if reasoning is not None:
        event_bus.subscribe(
            AIAnalysisCompleted,
            AIRuleEvaluationSubscriber(db, telegram, event_bus),
        )
        event_bus.subscribe(SignalCreated, SignalReasoningSubscriber(db, reasoning))
    if telegram is not None:
        event_bus.subscribe(SignalCreated, TelegramSignalNotifier(db, telegram))
        event_bus.subscribe(
            UnifiedEventMateriallyChanged,
            UnifiedEventTelegramNotifier(db, telegram),
        )
        if config is not None:
            health = SourceHealthTelegramNotifier(
                db,
                telegram,
                config.source_alert_after_failures,
                config.source_failure_alert_cooldown_minutes,
            )
            event_bus.subscribe(SourceFetchFailed, health.failed)
            event_bus.subscribe(SourceRecovered, health.recovered)
    event_bus.subscribe(SignalCreated, SignalDatabaseStorage(db))


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
