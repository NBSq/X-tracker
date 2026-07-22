from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from math import sqrt
from typing import Iterable, Mapping

from app.db.database import Database


@dataclass(frozen=True)
class PeriodSpec:
    key: str
    label: str
    days: int | None
    bucket: str


PERIODS = {
    "7d": PeriodSpec("7d", "7 days", 7, "day"),
    "30d": PeriodSpec("30d", "30 days", 30, "week"),
    "90d": PeriodSpec("90d", "90 days", 90, "week"),
    "all": PeriodSpec("all", "All time", None, "month"),
}


def parse_period(value: str) -> PeriodSpec:
    try:
        return PERIODS[value.lower()]
    except KeyError as exc:
        raise ValueError("period must be one of: 7d, 30d, 90d, all") from exc


@dataclass(frozen=True)
class HistoricalThresholds:
    growth_percent: float = 20.0
    minimum_activity: int = 2

    def __post_init__(self) -> None:
        if self.growth_percent <= 0:
            raise ValueError("Historical growth threshold must be positive")
        if self.minimum_activity <= 0:
            raise ValueError("Historical minimum activity must be positive")


@dataclass(frozen=True)
class HistoricalSummary:
    total_signals: int
    evaluated_signals: int
    successful_signals: int
    neutral_signals: int
    failed_signals: int
    success_rate: float
    average_hype_score: float | None
    average_momentum_score: float | None
    average_confidence: float | None
    average_hype_change: float | None
    average_momentum_change: float | None
    average_mentions_change: float | None


@dataclass(frozen=True)
class HistoricalBucket:
    bucket_start: str
    bucket_end: str
    signal_count: int
    evaluated_count: int
    success_rate: float | None
    average_hype_score: float | None
    average_momentum_score: float | None
    average_confidence: float | None
    average_hype_change: float | None
    average_momentum_change: float | None
    average_mentions_change: float | None


@dataclass(frozen=True)
class GrowthMetrics:
    signal_count_growth: float | None
    average_hype_growth: float | None
    average_momentum_growth: float | None
    mention_growth: float | None
    success_rate_change: float | None


@dataclass(frozen=True)
class EntityAnalytics:
    name: str
    signal_count: int
    evaluated_count: int
    success_count: int
    neutral_count: int
    failed_count: int
    success_rate: float | None
    average_hype_score: float | None
    average_momentum_score: float | None
    average_hype_change: float | None
    average_momentum_change: float | None
    average_mentions_change: float | None
    mention_count: int
    first_seen: str | None
    last_seen: str | None
    active_days: int
    current_rank: int | None
    previous_period_rank: int | None
    rank_change: int | None
    trend: str
    growth: GrowthMetrics
    consistency_score: float
    latest_hype_score: float | None
    latest_momentum_score: float | None


@dataclass(frozen=True)
class EntityDetail:
    analytics: EntityAnalytics
    timeline: tuple[HistoricalBucket, ...]
    recent_signals: tuple[dict[str, object], ...]
    recent_outcomes: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class HistoricalAnalyticsReport:
    period: PeriodSpec
    generated_at: str
    summary: HistoricalSummary
    timeline: tuple[HistoricalBucket, ...]
    narratives: tuple[EntityAnalytics, ...]
    tokens: tuple[EntityAnalytics, ...]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class HistoricalAnalyticsService:
    def __init__(
        self,
        database: Database,
        thresholds: HistoricalThresholds | None = None,
        clock=datetime.now,
    ) -> None:
        self.database = database
        self.thresholds = thresholds or HistoricalThresholds()
        self.clock = clock

    def build_report(self, period: str = "30d") -> HistoricalAnalyticsReport:
        spec = parse_period(period)
        now = _as_utc(self.clock(timezone.utc))
        all_signals = list(self.database.get_signals(limit=None))
        all_outcomes = list(self.database.get_signal_outcomes(limit=None))
        start, end, previous_start = _period_boundaries(spec, now, all_signals)
        current_signals = _between(all_signals, "timestamp", start, end)
        current_outcomes = _latest_outcomes(
            _between(all_outcomes, "evaluated_at", start, end)
        )
        if previous_start is None or start is None:
            previous_signals: list[Mapping[str, object]] = []
            previous_outcomes: list[Mapping[str, object]] = []
        else:
            previous_signals = _between(all_signals, "timestamp", previous_start, start)
            previous_outcomes = _latest_outcomes(
                _between(all_outcomes, "evaluated_at", previous_start, start)
            )

        timeline = self._timeline(spec, start, end, current_signals, current_outcomes)
        narratives = self._entities(
            "narrative",
            spec,
            start,
            current_signals,
            current_outcomes,
            previous_signals,
            previous_outcomes,
            all_signals,
        )
        tokens = self._entities(
            "token",
            spec,
            start,
            current_signals,
            current_outcomes,
            previous_signals,
            previous_outcomes,
            all_signals,
        )
        return HistoricalAnalyticsReport(
            period=spec,
            generated_at=now.isoformat(),
            summary=_summary(current_signals, current_outcomes),
            timeline=tuple(timeline),
            narratives=tuple(narratives),
            tokens=tuple(tokens),
        )

    def entity_detail(
        self,
        kind: str,
        name: str,
        period: str = "30d",
    ) -> EntityDetail | None:
        if kind not in {"narrative", "token"}:
            raise ValueError("kind must be narrative or token")
        report = self.build_report(period)
        entities = report.narratives if kind == "narrative" else report.tokens
        analytics = next(
            (item for item in entities if item.name.casefold() == name.casefold()),
            None,
        )
        if analytics is None:
            return None
        all_signals = list(self.database.get_signals(limit=None))
        matching_signals = [
            row for row in all_signals if _entity_value(row, kind) == analytics.name
        ]
        matching_ids = {int(row["id"]) for row in matching_signals}
        matching_outcomes = [
            row
            for row in self.database.get_signal_outcomes(limit=None)
            if int(row["signal_id"]) in matching_ids
        ]
        start, end, _ = _period_boundaries(report.period, _as_utc(self.clock(timezone.utc)), all_signals)
        current_signals = _between(matching_signals, "timestamp", start, end)
        current_outcomes = _latest_outcomes(
            _between(matching_outcomes, "evaluated_at", start, end)
        )
        timeline = self._timeline(
            report.period,
            start,
            end,
            current_signals,
            current_outcomes,
        )
        return EntityDetail(
            analytics=analytics,
            timeline=tuple(timeline),
            recent_signals=tuple(_signal_dict(row) for row in matching_signals[:10]),
            recent_outcomes=tuple(_outcome_dict(row) for row in matching_outcomes[:10]),
        )

    def _entities(
        self,
        kind: str,
        spec: PeriodSpec,
        current_start: datetime | None,
        current_signals: list[Mapping[str, object]],
        current_outcomes: list[Mapping[str, object]],
        previous_signals: list[Mapping[str, object]],
        previous_outcomes: list[Mapping[str, object]],
        all_signals: list[Mapping[str, object]],
    ) -> list[EntityAnalytics]:
        names = sorted(
            {
                value
                for row in [
                    *current_signals,
                    *previous_signals,
                    *current_outcomes,
                    *previous_outcomes,
                ]
                if (value := _entity_value(row, kind))
            },
            key=str.casefold,
        )
        current_stats = {
            name: _entity_stats(name, kind, current_signals, current_outcomes)
            for name in names
        }
        previous_stats = {
            name: _entity_stats(name, kind, previous_signals, previous_outcomes)
            for name in names
        }
        current_ranks = _ranks(current_stats)
        previous_ranks = _ranks(previous_stats)
        entities = []
        for name in names:
            current = current_stats[name]
            previous = previous_stats[name]
            lifetime = [row for row in all_signals if _entity_value(row, kind) == name]
            growth = _growth(current, previous)
            trend = _classify_trend(
                spec,
                current,
                previous,
                lifetime,
                current_start,
                growth,
                self.thresholds,
            )
            entity_timeline = self._entity_timeline(spec, current_start, current_signals, current_outcomes, kind, name)
            current_rank = current_ranks.get(name)
            previous_rank = previous_ranks.get(name)
            latest = max(
                (row for row in lifetime),
                key=lambda row: _timestamp(row["timestamp"]),
                default=None,
            )
            entities.append(
                EntityAnalytics(
                    name=name,
                    signal_count=current["signal_count"],
                    evaluated_count=current["evaluated_count"],
                    success_count=current["success_count"],
                    neutral_count=current["neutral_count"],
                    failed_count=current["failed_count"],
                    success_rate=current["success_rate"],
                    average_hype_score=current["average_hype_score"],
                    average_momentum_score=current["average_momentum_score"],
                    average_hype_change=current["average_hype_change"],
                    average_momentum_change=current["average_momentum_change"],
                    average_mentions_change=current["average_mentions_change"],
                    mention_count=current["mention_count"],
                    first_seen=_iso_min(lifetime, "timestamp"),
                    last_seen=_iso_max(lifetime, "timestamp"),
                    active_days=len({_timestamp(row["timestamp"]).date() for row in current["signals"]}),
                    current_rank=current_rank,
                    previous_period_rank=previous_rank,
                    rank_change=(previous_rank - current_rank if current_rank and previous_rank else None),
                    trend=trend,
                    growth=growth,
                    consistency_score=_consistency_score(entity_timeline),
                    latest_hype_score=float(latest["hype_score"]) if latest else None,
                    latest_momentum_score=float(latest["momentum_score"]) if latest else None,
                )
            )
        return sorted(
            entities,
            key=lambda item: (
                item.current_rank is None,
                item.current_rank or 10**9,
                item.name.casefold(),
            ),
        )

    def _entity_timeline(
        self,
        spec: PeriodSpec,
        start: datetime | None,
        signals: list[Mapping[str, object]],
        outcomes: list[Mapping[str, object]],
        kind: str,
        name: str,
    ) -> list[HistoricalBucket]:
        entity_signals = [row for row in signals if _entity_value(row, kind) == name]
        entity_outcomes = [row for row in outcomes if _entity_value(row, kind) == name]
        end = _period_end(_as_utc(self.clock(timezone.utc)))
        return self._timeline(spec, start, end, entity_signals, entity_outcomes)

    @staticmethod
    def _timeline(
        spec: PeriodSpec,
        start: datetime | None,
        end: datetime,
        signals: list[Mapping[str, object]],
        outcomes: list[Mapping[str, object]],
    ) -> list[HistoricalBucket]:
        if start is None:
            timestamps = [
                *(_timestamp(row["timestamp"]) for row in signals),
                *(_timestamp(row["evaluated_at"]) for row in outcomes),
            ]
            start = min(timestamps, default=end)
        buckets = _bucket_ranges(start, end, spec.bucket)
        result = []
        for bucket_start, bucket_end in buckets:
            bucket_signals = _between(signals, "timestamp", bucket_start, bucket_end)
            bucket_outcomes = _between(outcomes, "evaluated_at", bucket_start, bucket_end)
            summary = _summary(bucket_signals, bucket_outcomes)
            result.append(
                HistoricalBucket(
                    bucket_start=bucket_start.date().isoformat(),
                    bucket_end=(bucket_end - timedelta(microseconds=1)).date().isoformat(),
                    signal_count=summary.total_signals,
                    evaluated_count=summary.evaluated_signals,
                    success_rate=(summary.success_rate if summary.evaluated_signals else None),
                    average_hype_score=summary.average_hype_score,
                    average_momentum_score=summary.average_momentum_score,
                    average_confidence=summary.average_confidence,
                    average_hype_change=summary.average_hype_change,
                    average_momentum_change=summary.average_momentum_change,
                    average_mentions_change=summary.average_mentions_change,
                )
            )
        return result


def format_historical_report(report: HistoricalAnalyticsReport) -> str:
    summary = report.summary
    rising = sorted(
        (item for item in report.narratives if item.trend in {"RISING", "NEW"}),
        key=lambda item: _growth_strength(item.growth) or float("-inf"),
        reverse=True,
    )[:3]
    declining = sorted(
        (item for item in report.narratives if item.trend == "DECLINING"),
        key=lambda item: _growth_strength(item.growth) or float("inf"),
    )[:3]
    successful = sorted(
        (item for item in report.narratives if item.success_rate is not None),
        key=lambda item: (item.success_rate or 0.0, item.evaluated_count),
        reverse=True,
    )[:3]
    consistent = sorted(
        report.narratives,
        key=lambda item: (item.consistency_score, item.signal_count),
        reverse=True,
    )[:3]

    def growth_lines(items: Iterable[EntityAnalytics]) -> str:
        lines = []
        for index, item in enumerate(items, start=1):
            growth = _growth_strength(item.growth)
            value = "N/A" if growth is None else f"{growth:+.1f}%"
            lines.append(f"{index}. {item.name} - {value}")
        return "\n".join(lines) or "None"

    def success_lines(items: Iterable[EntityAnalytics]) -> str:
        return "\n".join(
            f"{index}. {item.name} - {item.success_rate:.1f}%"
            for index, item in enumerate(items, start=1)
            if item.success_rate is not None
        ) or "None"

    recent = "\n".join(
        (
            f"{bucket.bucket_start} to {bucket.bucket_end} - "
            f"{bucket.signal_count} signals - "
            f"{bucket.success_rate:.1f}% success"
            if bucket.success_rate is not None
            else (
                f"{bucket.bucket_start} to {bucket.bucket_end} - "
                f"{bucket.signal_count} signals - N/A success"
            )
        )
        for bucket in report.timeline[-6:]
    ) or "No historical data"
    average = lambda value: "N/A" if value is None else f"{value:.1f}"
    return (
        f"Historical Analytics - {report.period.label}\n\n"
        f"Signals: {summary.total_signals}\n"
        f"Evaluated: {summary.evaluated_signals}\n"
        f"Successful: {summary.successful_signals}\n"
        f"Neutral: {summary.neutral_signals}\n"
        f"Failed: {summary.failed_signals}\n"
        f"Success rate: {summary.success_rate:.1f}%\n\n"
        f"Average hype score: {average(summary.average_hype_score)}\n"
        f"Average momentum score: {average(summary.average_momentum_score)}\n"
        f"Average confidence: {average(summary.average_confidence)}\n\n"
        f"Fastest-growing narratives:\n\n{growth_lines(rising)}\n\n"
        f"Declining narratives:\n\n{growth_lines(declining)}\n\n"
        f"Most successful narratives:\n\n{success_lines(successful)}\n\n"
        "Most consistent narratives:\n\n"
        + (
            "\n".join(
                f"{index}. {item.name} - {item.consistency_score:.1f}"
                for index, item in enumerate(consistent, start=1)
            )
            or "None"
        )
        + f"\n\nRecent trend:\n{recent}"
    )


def _period_boundaries(
    spec: PeriodSpec,
    now: datetime,
    signals: Iterable[Mapping[str, object]],
) -> tuple[datetime | None, datetime, datetime | None]:
    end = _period_end(now)
    if spec.days is None:
        timestamps = [_timestamp(row["timestamp"]) for row in signals]
        start = min(timestamps, default=None)
        return start, end, None
    start = datetime.combine(
        now.date() - timedelta(days=spec.days - 1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )
    return start, end, start - timedelta(days=spec.days)


def _period_end(now: datetime) -> datetime:
    return datetime.combine(
        now.date() + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc,
    )


def _between(
    rows: Iterable[Mapping[str, object]],
    column: str,
    start: datetime | None,
    end: datetime,
) -> list[Mapping[str, object]]:
    return [
        row
        for row in rows
        if (start is None or _timestamp(row[column]) >= start)
        and _timestamp(row[column]) < end
    ]


def _latest_outcomes(rows: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    latest: dict[int, Mapping[str, object]] = {}
    for row in rows:
        signal_id = int(row["signal_id"])
        current = latest.get(signal_id)
        if current is None or (
            _timestamp(row["evaluated_at"]), int(row["id"])
        ) > (_timestamp(current["evaluated_at"]), int(current["id"])):
            latest[signal_id] = row
    return list(latest.values())


def _summary(
    signals: list[Mapping[str, object]],
    outcomes: list[Mapping[str, object]],
) -> HistoricalSummary:
    evaluated = len(outcomes)
    successful = sum(row["status"] == "SUCCESS" for row in outcomes)
    neutral = sum(row["status"] == "NEUTRAL" for row in outcomes)
    failed = sum(row["status"] == "FAILED" for row in outcomes)
    return HistoricalSummary(
        total_signals=len(signals),
        evaluated_signals=evaluated,
        successful_signals=successful,
        neutral_signals=neutral,
        failed_signals=failed,
        success_rate=successful / evaluated * 100 if evaluated else 0.0,
        average_hype_score=_average(signals, "hype_score"),
        average_momentum_score=_average(signals, "momentum_score"),
        average_confidence=_average(signals, "confidence"),
        average_hype_change=_average(outcomes, "hype_change"),
        average_momentum_change=_average(outcomes, "momentum_change"),
        average_mentions_change=_average(outcomes, "mentions_change"),
    )


def _entity_stats(
    name: str,
    kind: str,
    signals: list[Mapping[str, object]],
    outcomes: list[Mapping[str, object]],
) -> dict[str, object]:
    entity_signals = [row for row in signals if _entity_value(row, kind) == name]
    entity_outcomes = [row for row in outcomes if _entity_value(row, kind) == name]
    evaluated = len(entity_outcomes)
    successful = sum(row["status"] == "SUCCESS" for row in entity_outcomes)
    return {
        "signals": entity_signals,
        "signal_count": len(entity_signals),
        "evaluated_count": evaluated,
        "success_count": successful,
        "neutral_count": sum(row["status"] == "NEUTRAL" for row in entity_outcomes),
        "failed_count": sum(row["status"] == "FAILED" for row in entity_outcomes),
        "success_rate": successful / evaluated * 100 if evaluated else None,
        "average_hype_score": _average(entity_signals, "hype_score"),
        "average_momentum_score": _average(entity_signals, "momentum_score"),
        "average_hype_change": _average(entity_outcomes, "hype_change"),
        "average_momentum_change": _average(entity_outcomes, "momentum_change"),
        "average_mentions_change": _average(entity_outcomes, "mentions_change"),
        "mention_count": sum(int(row["mentions_count"] or 0) for row in entity_signals),
    }


def _growth(current: Mapping[str, object], previous: Mapping[str, object]) -> GrowthMetrics:
    return GrowthMetrics(
        signal_count_growth=_percent_change(current["signal_count"], previous["signal_count"]),
        average_hype_growth=_percent_change(current["average_hype_score"], previous["average_hype_score"]),
        average_momentum_growth=_percent_change(current["average_momentum_score"], previous["average_momentum_score"]),
        mention_growth=_percent_change(current["mention_count"], previous["mention_count"]),
        success_rate_change=(
            float(current["success_rate"]) - float(previous["success_rate"])
            if current["success_rate"] is not None and previous["success_rate"] is not None
            else None
        ),
    )


def _growth_strength(growth: GrowthMetrics) -> float | None:
    values = [
        value
        for value in (
            growth.signal_count_growth,
            growth.average_hype_growth,
            growth.average_momentum_growth,
            growth.mention_growth,
        )
        if value is not None
    ]
    return sum(values) / len(values) if values else None


def _classify_trend(
    spec: PeriodSpec,
    current: Mapping[str, object],
    previous: Mapping[str, object],
    lifetime: list[Mapping[str, object]],
    current_start: datetime | None,
    growth: GrowthMetrics,
    thresholds: HistoricalThresholds,
) -> str:
    current_count = int(current["signal_count"])
    previous_count = int(previous["signal_count"])
    if current_count == 0 and previous_count >= thresholds.minimum_activity:
        return "INACTIVE"
    first_seen = min((_timestamp(row["timestamp"]) for row in lifetime), default=None)
    if (
        spec.days is not None
        and current_count >= thresholds.minimum_activity
        and previous_count == 0
        and current_start is not None
        and first_seen is not None
        and first_seen >= current_start
    ):
        return "NEW"
    values = [
        value
        for value in (
            growth.signal_count_growth,
            growth.average_hype_growth,
            growth.average_momentum_growth,
            growth.mention_growth,
        )
        if value is not None
    ]
    rising = any(value >= thresholds.growth_percent for value in values)
    declining = any(value <= -thresholds.growth_percent for value in values)
    if rising and not declining:
        return "RISING"
    if declining and not rising:
        return "DECLINING"
    return "STABLE"


def _ranks(stats: Mapping[str, Mapping[str, object]]) -> dict[str, int]:
    active = [item for item in stats.items() if int(item[1]["signal_count"]) > 0]
    active.sort(
        key=lambda item: (
            -int(item[1]["signal_count"]),
            -float(item[1]["average_momentum_score"] or 0.0),
            -float(item[1]["average_hype_score"] or 0.0),
            item[0].casefold(),
        )
    )
    return {name: index for index, (name, _) in enumerate(active, start=1)}


def _bucket_ranges(
    start: datetime,
    end: datetime,
    bucket: str,
) -> list[tuple[datetime, datetime]]:
    if bucket == "day":
        cursor = datetime.combine(start.date(), datetime.min.time(), tzinfo=timezone.utc)
        step = lambda value: value + timedelta(days=1)
    elif bucket == "week":
        week_start = start.date() - timedelta(days=start.weekday())
        cursor = datetime.combine(week_start, datetime.min.time(), tzinfo=timezone.utc)
        step = lambda value: value + timedelta(days=7)
    else:
        cursor = datetime(start.year, start.month, 1, tzinfo=timezone.utc)

        def step(value: datetime) -> datetime:
            if value.month == 12:
                return datetime(value.year + 1, 1, 1, tzinfo=timezone.utc)
            return datetime(value.year, value.month + 1, 1, tzinfo=timezone.utc)

    ranges = []
    while cursor < end:
        next_cursor = step(cursor)
        ranges.append((cursor, min(next_cursor, end)))
        cursor = next_cursor
    return ranges


def _consistency_score(buckets: list[HistoricalBucket]) -> float:
    if not buckets:
        return 0.0
    active = [bucket for bucket in buckets if bucket.signal_count > 0]
    if not active:
        return 0.0
    coverage = len(active) / len(buckets)
    evidence = min(len(active) / 3.0, 1.0)
    signal_stability = _stability([float(bucket.signal_count) for bucket in buckets])
    hype_stability = _stability(
        [bucket.average_hype_score for bucket in active if bucket.average_hype_score is not None]
    )
    momentum_stability = _stability(
        [bucket.average_momentum_score for bucket in active if bucket.average_momentum_score is not None]
    )
    success_stability = _stability(
        [bucket.success_rate for bucket in active if bucket.success_rate is not None]
    )
    score = 100 * (
        0.30 * coverage
        + evidence
        * (
            0.20 * signal_stability
            + 0.15 * hype_stability
            + 0.15 * momentum_stability
            + 0.20 * success_stability
        )
    )
    return round(score, 1)


def _stability(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return 1.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    deviation = sqrt(variance)
    denominator = abs(mean) or 1.0
    return 1.0 / (1.0 + deviation / denominator)


def _average(rows: Iterable[Mapping[str, object]], column: str) -> float | None:
    values = [float(row[column]) for row in rows if row[column] is not None]
    return sum(values) / len(values) if values else None


def _percent_change(current: object, previous: object) -> float | None:
    if current is None or previous is None or float(previous) == 0.0:
        return None
    return (float(current) - float(previous)) / abs(float(previous)) * 100.0


def _entity_value(row: Mapping[str, object], kind: str) -> str | None:
    value = row[kind]
    return str(value) if value is not None and str(value).strip() else None


def _timestamp(value: object) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_min(rows: Iterable[Mapping[str, object]], column: str) -> str | None:
    values = [_timestamp(row[column]) for row in rows]
    return min(values).isoformat() if values else None


def _iso_max(rows: Iterable[Mapping[str, object]], column: str) -> str | None:
    values = [_timestamp(row[column]) for row in rows]
    return max(values).isoformat() if values else None


def _signal_dict(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "timestamp": _timestamp(row["timestamp"]).isoformat(),
        "signal_type": str(row["signal_type"]),
        "token": row["token"],
        "narrative": row["narrative"],
        "hype_score": float(row["hype_score"]),
        "momentum_score": float(row["momentum_score"]),
        "confidence": int(row["confidence"]),
        "mentions_count": row["mentions_count"],
        "action": str(row["action"]),
    }


def _outcome_dict(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": int(row["id"]),
        "signal_id": int(row["signal_id"]),
        "evaluated_at": _timestamp(row["evaluated_at"]).isoformat(),
        "evaluation_window_hours": int(row["evaluation_window_hours"]),
        "status": str(row["status"]),
        "hype_change": float(row["hype_change"]),
        "momentum_change": float(row["momentum_change"]),
        "mentions_change": int(row["mentions_change"]),
    }
