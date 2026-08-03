from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any
import json

from app.analytics.historical import HistoricalAnalyticsService, HistoricalThresholds
from app.ai.factory import create_signal_reasoning_service
from app.ai.service import result_from_row
from app.config import Config, load_config
from app.db.database import Database
from app.events import AIAnalysisCompleted, EventBus
from app.events.subscribers import AIRuleEvaluationSubscriber
from app.scoring.hype_score import normalize_hype_score
from app.rules import RuleService
from app.watchlists import WatchlistService
from app.ingestion.models import SourceDefinition
from app.ingestion.service import MultiSourceIngestionService
from app.graph.service import GraphService
from app.quality.service import SignalQualityService
from app.observability.health import HealthService, SnapshotService


class DashboardService:
    def __init__(
        self,
        database_path: Path,
        history_thresholds: HistoricalThresholds | None = None,
        config: Config | None = None,
    ) -> None:
        self.database_path = database_path
        self.history_thresholds = history_thresholds
        self.config = config or load_config()

    def status(self) -> dict[str, Any]:
        db = self._database()
        try:
            row = db.get_dashboard_status()
            return {
                "status": "operational",
                "database": self.database_path.name,
                "analyzed_posts": int(row["analyzed_posts"] or 0),
                "signals": int(row["signals"] or 0),
                "outcomes": int(row["outcomes"] or 0),
                "last_analysis_at": row["last_analysis_at"],
                "last_signal_at": row["last_signal_at"],
            }
        finally:
            db.close()

    def system_health(self) -> dict[str, Any]:
        db = self._database()
        try:
            return HealthService(db, self.config).detailed()
        finally:
            db.close()

    def system_ready(self) -> tuple[dict[str, Any], int]:
        db = self._database()
        try:
            return HealthService(db, self.config).ready()
        finally:
            db.close()

    def system_performance(self) -> dict[str, Any]:
        db = self._database()
        try:
            return HealthService(db, self.config).performance_report()
        finally:
            db.close()

    def system_metrics_summary(self) -> dict[str, Any]:
        db = self._database()
        try:
            health = HealthService(db, self.config)
            result = health.metrics_summary()
            result["history"] = SnapshotService(db, self.config).history(100)
            return result
        finally:
            db.close()

    def save_system_snapshot(self, *, force: bool = False) -> bool:
        db = self._database()
        try:
            return SnapshotService(db, self.config).save_if_due(force=force)
        finally:
            db.close()

    def sources(self) -> list[dict[str, Any]]:
        db = self._database()
        try:
            MultiSourceIngestionService(db, self.config).sync_configured_sources()
            return [dict(row) for row in db.get_content_sources()]
        finally:
            db.close()

    def source_detail(self, identifier: int | str) -> dict[str, Any] | None:
        db = self._database()
        try:
            row = db.get_content_source(identifier)
            if row is None:
                return None
            return {
                "source": dict(row),
                "items": [
                    dict(item) for item in db.get_content_items(
                        limit=100, source_id=int(row["id"])
                    )
                ],
            }
        finally:
            db.close()

    def create_source(self, payload: dict[str, Any]) -> dict[str, Any]:
        db = self._database()
        try:
            definition = SourceDefinition.from_mapping(payload)
            db.upsert_content_source(definition)
            return dict(db.get_content_source(definition.source_key))
        finally:
            db.close()

    def update_source(self, source_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        db = self._database()
        try:
            if not db.update_content_source(source_id, **payload):
                raise KeyError(source_id)
            return dict(db.get_content_source(source_id))
        finally:
            db.close()

    def delete_source(self, source_id: int) -> None:
        db = self._database()
        try:
            if not db.delete_content_source(source_id):
                raise KeyError(source_id)
        finally:
            db.close()

    def fetch_source(self, source_id: int) -> dict[str, Any]:
        db = self._database()
        try:
            result = MultiSourceIngestionService(db, self.config).fetch_source(source_id)
            return {
                "fetched_count": result.fetched_count,
                "accepted_count": result.accepted_count,
                "duplicate_count": result.duplicate_count,
                "new_event_count": result.new_event_count,
            }
        finally:
            db.close()

    def unified_events(
        self,
        *,
        source_id: int | None = None,
        token: str | None = None,
        narrative: str | None = None,
        status: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        db = self._database()
        try:
            rows = [dict(row) for row in db.get_unified_events(limit=None, status=status)]
            if source_id is not None:
                event_ids = {
                    int(item["unified_event_id"])
                    for item in db.get_content_items(limit=None, source_id=source_id)
                    if item["unified_event_id"] is not None
                }
                rows = [row for row in rows if int(row["id"]) in event_ids]
            if token:
                rows = [row for row in rows if token.casefold() in str(row["tokens_json"]).casefold()]
            if narrative:
                rows = [
                    row for row in rows
                    if narrative.casefold() in str(row["narratives_json"]).casefold()
                ]
            if from_date:
                rows = [row for row in rows if str(row["last_seen_at"])[:10] >= from_date]
            if to_date:
                rows = [row for row in rows if str(row["first_seen_at"])[:10] <= to_date]
            return rows[: max(1, min(limit, 500))]
        finally:
            db.close()

    def unified_event_detail(self, event_id: int) -> dict[str, Any] | None:
        db = self._database()
        try:
            event = db.get_unified_event(event_id)
            if event is None:
                return None
            signal_row = db.connection.execute(
                """
                SELECT id FROM signal_history
                WHERE unified_event_id = ? ORDER BY timestamp DESC, id DESC LIMIT 1
                """,
                (event_id,),
            ).fetchone()
            payload = {
                "event": dict(event),
                "items": [dict(row) for row in db.get_unified_event_items(event_id)],
                "history": [dict(row) for row in db.get_unified_event_history(event_id)],
            }
            if signal_row is not None:
                payload["signal"] = self.signal_detail(int(signal_row["id"]))
            else:
                payload["signal"] = None
            return payload
        finally:
            db.close()

    def deduplication(self, days: int = 30) -> dict[str, Any]:
        db = self._database()
        try:
            stats = dict(db.get_deduplication_stats(days))
            raw = int(stats.get("raw_items") or 0)
            duplicate_total = int(stats.get("exact_duplicates") or 0) + int(
                stats.get("near_duplicates") or 0
            )
            return {
                **stats,
                "period_days": days,
                "duplicate_reduction_percent": round(
                    duplicate_total / raw * 100 if raw else 0.0, 1
                ),
                "top_duplicate_sources": [
                    dict(row) for row in db.get_top_duplicate_sources(days)
                ],
            }
        finally:
            db.close()

    def signals(
        self,
        limit: int = 50,
        watchlist_id: int | None = None,
    ) -> list[dict[str, Any]]:
        db = self._database()
        try:
            return [
                {
                    "id": int(row["id"]),
                    "timestamp": row["timestamp"],
                    "signal_type": str(row["signal_type"]),
                    "token": row["token"],
                    "narrative": row["narrative"],
                    "hype_score": round(float(row["hype_score"]), 1),
                    "momentum_score": round(float(row["momentum_score"]), 1),
                    "confidence": int(row["confidence"]),
                    "action": str(row["action"]),
                    "mentions_count": _value(row, "mentions_count", None),
                    "outcome_status": row["outcome_status"],
                    "score_change": row["score_change"],
                    "mentions_change": row["mentions_change"],
                    "momentum_change": row["momentum_change"],
                    "evaluated_at": row["evaluated_at"],
                    "high_priority": bool(row["high_priority"]),
                    "dashboard_highlight": bool(row["dashboard_highlight"])
                    or bool(_value(row, "watchlist_dashboard_highlight", False)),
                    "include_in_digest": bool(row["include_in_digest"])
                    or bool(_value(row, "watchlist_include_in_digest", False)),
                    "csv_export_marker": bool(row["csv_export_marker"]),
                    "matched_rules": row["matched_rules"],
                    "watchlist_names": _value(row, "watchlist_names", None),
                }
                for row in db.get_signals(
                    limit=max(1, min(limit, 200)),
                    watchlist_id=watchlist_id,
                )
            ]
        finally:
            db.close()

    def performance(self) -> dict[str, Any]:
        db = self._database()
        try:
            generated = (
                db.get_signal_performance_summary()
                if db.has_table("signal_history")
                else {}
            )
            outcomes = (
                db.get_signal_outcome_summary()
                if db.has_table("signal_outcomes")
                else {}
            )
            evaluated = int(_value(outcomes, "signals_evaluated") or 0)
            success = int(_value(outcomes, "success") or 0)
            return {
                "signals_generated": int(
                    _value(generated, "signals_generated") or 0
                ),
                "signals_evaluated": evaluated,
                "success": success,
                "neutral": int(_value(outcomes, "neutral") or 0),
                "failed": int(_value(outcomes, "failed") or 0),
                "accuracy": round(success / evaluated * 100.0, 1) if evaluated else 0.0,
                "average_confidence": round(
                    float(_value(generated, "average_confidence") or 0.0), 1
                ),
                "average_momentum": round(
                    float(_value(generated, "average_momentum") or 0.0), 1
                ),
                "average_mention_change": round(
                    float(_value(outcomes, "average_mention_change") or 0.0), 1
                ),
                "average_hype_change": round(
                    float(_value(outcomes, "average_hype_change") or 0.0), 1
                ),
                "average_momentum_change": round(
                    float(_value(outcomes, "average_momentum_change") or 0.0), 1
                ),
                "best_narratives": (
                    self._outcome_narratives(db, "DESC")
                    if db.has_table("signal_outcomes")
                    else []
                ),
                "worst_narratives": (
                    self._outcome_narratives(db, "ASC")
                    if db.has_table("signal_outcomes")
                    else []
                ),
            }
        finally:
            db.close()

    def narratives(self, limit: int = 25) -> list[dict[str, Any]]:
        return self._rankings("narrative", limit)

    def tokens(self, limit: int = 25) -> list[dict[str, Any]]:
        return self._rankings("token", limit)

    def overview(self) -> dict[str, Any]:
        return {
            "status": self.status(),
            "signals": self.signals(8),
            "performance": self.performance(),
            "narratives": self.narratives(6),
            "tokens": self.tokens(6),
        }

    def signal_detail(self, signal_id: int) -> dict[str, Any] | None:
        signal = next(
            (item for item in self.signals(200) if item["id"] == signal_id),
            None,
        )
        if signal is None:
            return None
        signal["ai_analysis"] = self.signal_analysis(signal_id)
        db = self._database()
        try:
            signal["watchlists"] = [
                dict(row) for row in db.get_signal_watchlists(signal_id)
            ]
            signal["triggered_rules"] = [
                dict(row) for row in db.get_rule_matches(signal_id)
            ]
            signal["outcomes"] = [
                dict(row) for row in db.get_signal_outcomes(signal_id=signal_id)
            ]
        finally:
            db.close()
        return signal

    def ai_status(self) -> dict[str, Any]:
        db = self._database()
        try:
            return create_signal_reasoning_service(db, self.config).status()
        finally:
            db.close()

    def ai_usage(self, limit: int = 100) -> list[dict[str, Any]]:
        db = self._database()
        try:
            return [dict(row) for row in db.get_ai_usage(limit)]
        finally:
            db.close()

    def ai_analyses(
        self,
        limit: int = 100,
        provider: str | None = None,
    ) -> list[dict[str, Any]]:
        db = self._database()
        try:
            return [
                {
                    **result_from_row(row).model_dump(mode="json"),
                    "id": int(row["id"]),
                    "signal_id": int(row["signal_id"]),
                    "token": row["token"],
                    "narrative": row["narrative"],
                    "hype_score": float(row["hype_score"]),
                    "momentum_score": float(row["momentum_score"]),
                }
                for row in db.get_signal_ai_analyses(limit, provider)
            ]
        finally:
            db.close()

    def signal_analysis(self, signal_id: int) -> dict[str, Any] | None:
        db = self._database()
        try:
            row = db.get_signal_ai_analysis(signal_id)
            return (
                result_from_row(row).model_dump(mode="json")
                if row is not None
                else None
            )
        finally:
            db.close()

    def analyze_signal(self, signal_id: int) -> dict[str, Any]:
        db = self._database()
        try:
            event_bus = EventBus()
            event_bus.subscribe(
                AIAnalysisCompleted,
                AIRuleEvaluationSubscriber(db, None),
            )
            result = create_signal_reasoning_service(
                db,
                self.config,
                event_bus=event_bus,
            ).analyze_signal(
                signal_id,
                force=True,
            )
            return result.model_dump(mode="json")
        finally:
            db.close()

    def rules(self, enabled: bool | None = None) -> list[dict[str, Any]]:
        db = self._database()
        try:
            return [rule.as_dict() for rule in RuleService(db).list_rules(enabled)]
        finally:
            db.close()

    def rule(self, rule_id: int) -> dict[str, Any] | None:
        db = self._database()
        try:
            rule = RuleService(db).get_rule(rule_id)
            if rule is None:
                return None
            result = rule.as_dict()
            result["matches"] = [
                {
                    "signal_id": int(row["signal_id"]),
                    "triggered_at": row["triggered_at"],
                    "actions": row["actions_json"],
                }
                for row in db.get_rule_matches()
                if int(row["rule_id"]) == rule_id
            ][:25]
            return result
        finally:
            db.close()

    def create_rule(
        self,
        name: str,
        condition: dict[str, Any],
        actions: str | list[str],
        enabled: bool = True,
        priority: int = 0,
    ) -> dict[str, Any]:
        db = self._database()
        try:
            return RuleService(db).create_rule(
                name,
                condition,
                actions,
                enabled=enabled,
                priority=priority,
            ).as_dict()
        finally:
            db.close()

    def update_rule(self, rule_id: int, **changes: Any) -> dict[str, Any]:
        db = self._database()
        try:
            return RuleService(db).update_rule(rule_id, **changes).as_dict()
        finally:
            db.close()

    def delete_rule(self, rule_id: int) -> bool:
        db = self._database()
        try:
            return RuleService(db).delete_rule(rule_id)
        finally:
            db.close()

    def watchlists(self, enabled: bool | None = None) -> list[dict[str, Any]]:
        db = self._database()
        try:
            service = WatchlistService(db)
            result = []
            for watchlist in service.list_watchlists(enabled):
                report = service.report(watchlist.id)
                item = watchlist.as_dict()
                item.update(
                    {
                        "item_count": len(report.items),
                        "token_count": sum(
                            entry.item_type == "token" for entry in report.items
                        ),
                        "narrative_count": sum(
                            entry.item_type == "narrative" for entry in report.items
                        ),
                        "signals_count": report.signals_count,
                        "evaluated_count": report.evaluated_count,
                        "success_rate": report.success_rate,
                        "last_matched_at": report.last_matched_at,
                    }
                )
                result.append(item)
            return result
        finally:
            db.close()

    def watchlist(self, identifier: int | str) -> dict[str, Any] | None:
        db = self._database()
        try:
            service = WatchlistService(db)
            watchlist = service.get_watchlist(identifier)
            if watchlist is None:
                return None
            report = service.report(watchlist.id).as_dict()
            graph = GraphService(db, self.config)
            graph_view = graph.graph_view(
                watchlist_id=watchlist.id, min_weight=0,
                limit=self.config.graph_max_nodes,
            )
            tracked = [
                node for node in graph_view["nodes"]
                if node["node_type"] in {"token", "narrative"}
            ]
            related_edges = []
            recent_events = []
            for node in tracked:
                detail = graph.node_detail(node["node_type"], node["entity_id"])
                if detail:
                    related_edges.extend(detail["edges"])
                    recent_events.extend(detail["recent_events"])
            unique_edges = {item["id"]: item for item in related_edges}
            report["graph"] = {
                "connected_tokens": [item for item in tracked if item["node_type"] == "token"],
                "connected_narratives": [item for item in tracked if item["node_type"] == "narrative"],
                "recent_events": list({item["id"]: item for item in recent_events}.values())[:10],
                "strongest_relationships": sorted(
                    unique_edges.values(), key=lambda item: item["weight"], reverse=True
                )[:10],
                "emerging_relationships": sorted(
                    unique_edges.values(),
                    key=lambda item: item["emerging_relationship_score"], reverse=True,
                )[:10],
            }
            return report
        finally:
            db.close()

    def create_watchlist(self, **values: Any) -> dict[str, Any]:
        db = self._database()
        try:
            return WatchlistService(db).create_watchlist(**values).as_dict()
        finally:
            db.close()

    def update_watchlist(self, watchlist_id: int, **changes: Any) -> dict[str, Any]:
        db = self._database()
        try:
            return WatchlistService(db).update_watchlist(
                watchlist_id,
                **changes,
            ).as_dict()
        finally:
            db.close()

    def delete_watchlist(self, watchlist_id: int) -> bool:
        db = self._database()
        try:
            return WatchlistService(db).delete_watchlist(watchlist_id)
        finally:
            db.close()

    def add_watchlist_item(
        self,
        watchlist_id: int,
        item_type: str,
        item_value: str,
    ) -> dict[str, Any]:
        db = self._database()
        try:
            return WatchlistService(db).add_item(
                watchlist_id,
                item_type,
                item_value,
            ).as_dict()
        finally:
            db.close()

    def remove_watchlist_item(self, watchlist_id: int, item_id: int) -> bool:
        db = self._database()
        try:
            return WatchlistService(db).remove_item(watchlist_id, item_id)
        finally:
            db.close()

    def history(
        self,
        period: str = "30d",
        watchlist_id: int | None = None,
    ) -> dict[str, Any]:
        db = self._database()
        try:
            signal_ids = (
                WatchlistService(db).matching_signal_ids(watchlist_id)
                if watchlist_id is not None
                else None
            )
            data = HistoricalAnalyticsService(
                db,
                self.history_thresholds,
            ).build_report(period, signal_ids=signal_ids).as_dict()
            narratives = data["narratives"]
            for trend in ("RISING", "NEW", "DECLINING", "INACTIVE"):
                data[f"{trend.lower()}_narratives"] = [
                    item for item in narratives if item["trend"] == trend
                ]
            data["most_successful_narratives"] = sorted(
                (item for item in narratives if item["success_rate"] is not None),
                key=lambda item: (item["success_rate"], item["evaluated_count"]),
                reverse=True,
            )[:5]
            data["most_consistent_narratives"] = sorted(
                narratives,
                key=lambda item: (item["consistency_score"], item["signal_count"]),
                reverse=True,
            )[:5]
            return data
        finally:
            db.close()

    def history_detail(
        self,
        kind: str,
        name: str,
        period: str = "30d",
    ) -> dict[str, Any] | None:
        db = self._database()
        try:
            detail = HistoricalAnalyticsService(
                db,
                self.history_thresholds,
            ).entity_detail(kind, name, period)
            if detail is None:
                return None
            return asdict(detail)
        finally:
            db.close()

    def outcomes(
        self,
        limit: int = 100,
        status: str | None = None,
        evaluation_window_hours: int | None = None,
        token: str | None = None,
        narrative: str | None = None,
        period_hours: int | None = None,
        signal_id: int | None = None,
        watchlist_id: int | None = None,
    ) -> list[dict[str, Any]]:
        db = self._database()
        try:
            if not db.has_table("signal_outcomes"):
                return []
            return [
                {
                    "id": int(row["id"]),
                    "signal_id": int(row["signal_id"]),
                    "evaluated_at": row["evaluated_at"],
                    "evaluation_window_hours": int(row["evaluation_window_hours"]),
                    "status": str(row["status"]),
                    "original_hype_score": round(float(row["original_hype_score"]), 1),
                    "current_hype_score": round(float(row["current_hype_score"]), 1),
                    "hype_change": round(float(row["hype_change"]), 1),
                    "original_momentum_score": round(float(row["original_momentum_score"]), 1),
                    "current_momentum_score": round(float(row["current_momentum_score"]), 1),
                    "momentum_change": round(float(row["momentum_change"]), 1),
                    "original_mentions": int(row["original_mentions"]),
                    "current_mentions": int(row["current_mentions"]),
                    "mentions_change": int(row["mentions_change"]),
                    "notes": str(row["notes"]),
                    "signal_type": str(row["signal_type"]),
                    "token": row["token"],
                    "narrative": row["narrative"],
                    "signal_timestamp": row["signal_timestamp"],
                }
                for row in db.get_signal_outcomes(
                    limit=limit,
                    status=status,
                    evaluation_window_hours=evaluation_window_hours,
                    token=token,
                    narrative=narrative,
                    period_hours=period_hours,
                    signal_id=signal_id,
                    watchlist_id=watchlist_id,
                )
            ]
        finally:
            db.close()

    def outcome_summary(
        self,
        period_hours: int | None = None,
        watchlist_id: int | None = None,
    ) -> dict[str, Any]:
        db = self._database()
        try:
            if not db.has_table("signal_outcomes"):
                return self._empty_outcome_summary()
            if watchlist_id is not None:
                rows = db.get_signal_outcomes(
                    limit=None,
                    period_hours=period_hours,
                    watchlist_id=watchlist_id,
                )
                return self._outcome_summary_from_rows(rows)
            row = db.get_signal_outcome_summary(period_hours=period_hours)
            evaluated = int(row["signals_evaluated"] or 0)
            successful = int(row["success"] or 0)
            return {
                "signals_evaluated": evaluated,
                "successful": successful,
                "neutral": int(row["neutral"] or 0),
                "failed": int(row["failed"] or 0),
                "success_rate": round(successful / evaluated * 100, 1) if evaluated else 0.0,
                "average_hype_change": round(float(row["average_hype_change"] or 0), 1),
                "average_momentum_change": round(float(row["average_momentum_change"] or 0), 1),
                "average_mentions_change": round(float(row["average_mention_change"] or 0), 1),
                "best_narratives": self._outcome_narratives(db, "DESC"),
                "worst_narratives": self._outcome_narratives(db, "ASC"),
            }
        finally:
            db.close()

    @staticmethod
    def _outcome_summary_from_rows(rows) -> dict[str, Any]:
        total = len(rows)
        counts = {
            status: sum(str(row["status"]) == status for row in rows)
            for status in ("SUCCESS", "NEUTRAL", "FAILED")
        }
        by_narrative: dict[str, list] = {}
        for row in rows:
            if row["narrative"]:
                by_narrative.setdefault(str(row["narrative"]), []).append(row)
        rankings = []
        for name, values in by_narrative.items():
            successful = sum(str(row["status"]) == "SUCCESS" for row in values)
            rankings.append(
                {
                    "name": name,
                    "evaluated_count": len(values),
                    "outcome_score": round(successful / len(values) * 100, 2),
                    "success_rate": round(successful / len(values) * 100, 1),
                    "average_momentum_change": round(
                        sum(float(row["momentum_change"]) for row in values)
                        / len(values),
                        1,
                    ),
                }
            )
        rankings.sort(
            key=lambda item: (
                item["success_rate"],
                item["average_momentum_change"],
                item["evaluated_count"],
            ),
            reverse=True,
        )

        def average(field: str) -> float:
            return round(
                sum(float(row[field]) for row in rows) / total,
                1,
            ) if total else 0.0

        return {
            "signals_evaluated": total,
            "successful": counts["SUCCESS"],
            "neutral": counts["NEUTRAL"],
            "failed": counts["FAILED"],
            "success_rate": round(counts["SUCCESS"] / total * 100, 1)
            if total else 0.0,
            "average_hype_change": average("hype_change"),
            "average_momentum_change": average("momentum_change"),
            "average_mentions_change": average("mentions_change"),
            "best_narratives": rankings[:5],
            "worst_narratives": list(reversed(rankings[-5:])),
        }

    @staticmethod
    def _empty_outcome_summary() -> dict[str, Any]:
        return {
            "signals_evaluated": 0,
            "successful": 0,
            "neutral": 0,
            "failed": 0,
            "success_rate": 0.0,
            "average_hype_change": 0.0,
            "average_momentum_change": 0.0,
            "average_mentions_change": 0.0,
            "best_narratives": [],
            "worst_narratives": [],
        }

    def _rankings(self, kind: str, limit: int) -> list[dict[str, Any]]:
        db = self._database()
        try:
            if not db.has_table("analyzed_posts"):
                return []
            momentum = {
                str(row["narrative"]): int(row["momentum_score"])
                for row in db.get_latest_narrative_momentum()
            }
            rows = [
                row
                for row in db.get_signal_stats_for_hours(24)
                if str(row["kind"]) == kind
            ]
            rankings = [
                {
                    "name": str(row["name"]),
                    "mentions": int(row["mentions_count"]),
                    "average_importance": round(
                        float(row["average_importance"]), 1
                    ),
                    "hype_score": normalize_hype_score(
                        int(row["mentions_count"])
                        * float(row["average_importance"])
                    ),
                    "momentum_score": (
                        momentum.get(str(row["name"]), 0)
                        if kind == "narrative"
                        else 0
                    ),
                }
                for row in rows
            ]
            rankings.sort(
                key=lambda item: (item["hype_score"], item["mentions"]),
                reverse=True,
            )
            return rankings[: max(1, min(limit, 100))]
        finally:
            db.close()

    @staticmethod
    def _outcome_narratives(db: Database, order: str) -> list[dict[str, Any]]:
        return [
            {
                "name": str(row["name"]),
                "evaluated_count": int(row["evaluated_count"]),
                "outcome_score": round(float(row["outcome_score"]), 2),
                "success_rate": round(float(row["success_rate"]), 1),
                "average_momentum_change": round(
                    float(row["average_momentum_change"] or 0.0), 1
                ),
            }
            for row in db.get_signal_outcome_narratives(order)
        ]

    def graph(
        self,
        *,
        period: int | None = None,
        node_type: str | None = None,
        edge_type: str | None = None,
        min_weight: float | None = None,
        min_occurrences: int = 1,
        watchlist_id: int | None = None,
        search: str | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        db = self._database()
        try:
            return GraphService(db, self.config).graph_view(
                period_days=period, node_type=node_type, edge_type=edge_type,
                min_weight=min_weight, min_occurrences=min_occurrences,
                watchlist_id=watchlist_id, search=search, limit=limit,
            )
        finally:
            db.close()

    def graph_node(self, node_type: str, entity_id: str) -> dict[str, Any] | None:
        db = self._database()
        try:
            return GraphService(db, self.config).node_detail(node_type, entity_id)
        finally:
            db.close()

    def graph_summary(self, period: int | None = None) -> dict[str, Any]:
        db = self._database()
        try:
            return GraphService(db, self.config).summary(period)
        finally:
            db.close()

    def graph_emerging(self, limit: int = 25) -> list[dict[str, Any]]:
        db = self._database()
        try:
            return GraphService(db, self.config).emerging(limit)
        finally:
            db.close()

    def graph_bridges(self, limit: int = 25) -> list[dict[str, Any]]:
        db = self._database()
        try:
            return GraphService(db, self.config).bridges(limit)
        finally:
            db.close()

    def graph_snapshots(self, limit: int = 100) -> list[dict[str, Any]]:
        db = self._database()
        try:
            return GraphService(db, self.config).snapshots(limit)
        finally:
            db.close()

    def create_graph_snapshot(self, frequency: str) -> dict[str, Any]:
        db = self._database()
        try:
            snapshot, created = GraphService(db, self.config).create_snapshot(frequency)
            return {"snapshot": snapshot.as_dict(), "created": created}
        finally:
            db.close()

    def rebuild_graph(self) -> dict[str, int]:
        db = self._database()
        try:
            return GraphService(db, self.config).rebuild()
        finally:
            db.close()

    def validate_graph(self) -> dict[str, Any]:
        db = self._database()
        try:
            issues = GraphService(db, self.config).validate()
            return {"valid": not issues, "issue_count": len(issues), "issues": issues}
        finally:
            db.close()

    def quality_summary(self, period_days: int = 30) -> dict[str, Any]:
        db = self._database()
        try:
            return SignalQualityService(db, self.config).summary(period_days)
        finally:
            db.close()

    def quality_signals(
        self, *, limit: int = 100, offset: int = 0,
        classification: str | None = None, calculation_version: int | None = None,
        from_date: str | None = None, to_date: str | None = None,
        entity_type: str | None = None, entity_id: str | None = None,
    ) -> dict[str, Any]:
        db = self._database()
        try:
            quality = SignalQualityService(db, self.config)
            quality.calculate_missing(version=calculation_version)
            signal_ids = (
                quality.signal_ids(entity_type, entity_id)
                if entity_type and entity_id else None
            )
            rows = db.get_signal_quality_scores(
                limit=None, classification=classification,
                calculation_version=calculation_version,
                from_date=from_date, to_date=to_date, signal_ids=signal_ids,
            )
            return {
                "items": [self._quality_row(row) for row in rows[offset:offset + limit]],
                "total": len(rows), "limit": limit, "offset": offset,
            }
        finally:
            db.close()

    def signal_quality(self, signal_id: int) -> dict[str, Any] | None:
        db = self._database()
        try:
            if db.get_signal(signal_id) is None:
                return None
            row = db.get_signal_quality_score(signal_id)
            if row is None:
                SignalQualityService(db, self.config).calculate_signal(signal_id)
                row = db.get_signal_quality_score(signal_id)
            return self._quality_row(row) if row is not None else None
        finally:
            db.close()

    def quality_entities(
        self, entity_type: str, period_days: int = 30, minimum_sample: int = 0,
    ) -> list[dict[str, Any]]:
        db = self._database()
        try:
            quality = SignalQualityService(db, self.config)
            quality.calculate_missing()
            return quality.entity_report(
                entity_type, period_days=period_days, minimum_sample=minimum_sample,
            )
        finally:
            db.close()

    def quality_ai(self, period_days: int = 30) -> list[dict[str, Any]]:
        db = self._database()
        try:
            quality = SignalQualityService(db, self.config)
            quality.calculate_missing()
            return quality.ai_report(period_days)
        finally:
            db.close()

    def quality_recommendations(
        self, recommendation_status: str | None = None, limit: int = 100,
    ) -> list[dict[str, Any]]:
        db = self._database()
        try:
            return SignalQualityService(db, self.config).recommendations(
                recommendation_status, limit,
            )
        finally:
            db.close()

    def update_quality_recommendation(self, recommendation_id: int, state: str) -> dict[str, Any]:
        db = self._database()
        try:
            quality = SignalQualityService(db, self.config)
            if not quality.update_recommendation(recommendation_id, state):
                raise KeyError(recommendation_id)
            return next(
                dict(row) for row in db.get_quality_recommendations(limit=5000)
                if int(row["id"]) == recommendation_id
            )
        finally:
            db.close()

    def recalculate_quality(self, payload: dict[str, Any]) -> dict[str, int]:
        db = self._database()
        try:
            return SignalQualityService(db, self.config).recalculate(
                signal_id=payload.get("signal_id"),
                entity_type=payload.get("entity_type"),
                entity_id=payload.get("entity_id"),
                period_days=payload.get("period_days", 30),
                version=payload.get("calculation_version"),
            )
        finally:
            db.close()

    def validate_quality(self) -> dict[str, Any]:
        db = self._database()
        try:
            issues = SignalQualityService(db, self.config).validate()
            return {"valid": not issues, "issue_count": len(issues), "issues": issues}
        finally:
            db.close()

    @staticmethod
    def _quality_row(row) -> dict[str, Any]:
        item = dict(row)
        try:
            item["breakdown"] = json.loads(item.get("breakdown_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            item["breakdown"] = {}
        return item

    def _database(self) -> Database:
        db = Database(self.database_path)
        if (
            not db.has_table("alert_rules")
            or not db.has_table("watchlists")
            or not db.has_table("signal_ai_analyses")
            or not db.has_table("graph_nodes")
            or not db.has_table("signal_quality_scores")
            or not db.has_table("observability_snapshots")
        ):
            db.initialize()
        return db


def _value(row, key: str, default=0):
    return row[key] if row and key in row.keys() else default
