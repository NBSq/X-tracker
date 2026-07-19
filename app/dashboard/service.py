from __future__ import annotations

from pathlib import Path
from typing import Any

from app.db.database import Database
from app.scoring.hype_score import normalize_hype_score


class DashboardService:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path

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

    def signals(self, limit: int = 50) -> list[dict[str, Any]]:
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
                }
                for row in db.get_latest_signals(max(1, min(limit, 200)))
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
                "average_momentum_change": round(
                    float(row["average_momentum_change"] or 0.0), 1
                ),
            }
            for row in db.get_signal_outcome_narratives(order)
        ]

    def _database(self) -> Database:
        return Database(self.database_path)


def _value(row, key: str, default=0):
    return row[key] if row and key in row.keys() else default
