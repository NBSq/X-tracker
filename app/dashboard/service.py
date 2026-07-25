from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

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
            return service.report(watchlist.id).as_dict()
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

    def _database(self) -> Database:
        db = Database(self.database_path)
        if (
            not db.has_table("alert_rules")
            or not db.has_table("watchlists")
            or not db.has_table("signal_ai_analyses")
        ):
            db.initialize()
        return db


def _value(row, key: str, default=0):
    return row[key] if row and key in row.keys() else default
