from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING, ClassVar
import time

import requests

from app.ai.analyzer import SpikeInsight
from app.scoring.hype_score import HypeSignal
from app.scoring.hype_score import normalize_hype_score
from app.scoring.momentum_score import NarrativeMomentum
from app.scoring.opportunity_score import NarrativeOpportunity
from app.observability.errors import classify_error
from app.observability.metrics import metrics
from app.observability.timing import record_timing

if TYPE_CHECKING:
    from app.ai.models import SignalAnalysisResult
    from app.rules.models import SignalFacts
    from app.watchlists.service import WatchlistService


def _telegram_post(*args, **kwargs):
    slow_threshold_ms = kwargs.pop("_slow_threshold_ms", 3000)
    started = time.perf_counter()
    try:
        response = requests.post(*args, **kwargs)
        response.raise_for_status()
    except Exception as exc:
        duration = (time.perf_counter() - started) * 1000
        metrics.increment("telegram_notifications_failed_total")
        metrics.record_error("telegram", classify_error(exc))
        record_timing("telegram_send", duration, threshold_ms=slow_threshold_ms)
        raise
    duration = (time.perf_counter() - started) * 1000
    metrics.increment("telegram_notifications_sent_total")
    metrics.record_success("telegram")
    record_timing("telegram_send", duration, threshold_ms=slow_threshold_ms)
    return response


@dataclass(frozen=True)
class AlertPost:
    username: str
    text: str


@dataclass(frozen=True)
class HypeAlert:
    signal: HypeSignal
    insight: SpikeInsight
    top_posts: list[AlertPost]
    related_tokens: list[str]
    related_narratives: list[str]
    momentum: list[NarrativeMomentum]
    merged_signal: HypeSignal | None = None
    merged_hype_score: float | None = None
    baseline_mentions_count: int | None = None


@dataclass(frozen=True)
class SummaryItem:
    name: str
    hype_score: float


@dataclass(frozen=True)
class NarrativeSummary:
    top_tokens: list[SummaryItem]
    top_narratives: list[SummaryItem]
    important_posts: list[AlertPost]


@dataclass(frozen=True)
class NarrativeTrend:
    name: str
    score: float


@dataclass(frozen=True)
class NarrativeGrowth:
    name: str
    growth_percent: float


@dataclass(frozen=True)
class TrendReport:
    top_24h: list[NarrativeTrend]
    top_7d: list[NarrativeTrend]
    fastest_growing: list[NarrativeGrowth]
    momentum: list[NarrativeMomentum]


@dataclass(frozen=True)
class DailyDigest:
    top_tokens: list[SummaryItem]
    top_narratives: list[SummaryItem]
    fastest_growing: NarrativeGrowth | None
    important_posts: list[AlertPost]
    final_summary: str
    momentum: list[NarrativeMomentum]


@dataclass(frozen=True)
class MomentumHistoryItem:
    name: str
    seven_days_ago: int | None
    today: int
    change_percent: float | None


@dataclass(frozen=True)
class MomentumHistoryReport:
    items: list[MomentumHistoryItem]


@dataclass(frozen=True)
class OpportunityReport:
    opportunities: list[NarrativeOpportunity]


@dataclass(frozen=True)
class PerformanceNarrative:
    name: str
    signals_count: int
    average_momentum: float
    average_confidence: float


@dataclass(frozen=True)
class SignalPerformanceReport:
    signals_generated: int
    successful: int
    accuracy: float
    average_confidence: float
    average_momentum: float
    best_narratives: list[PerformanceNarrative]
    worst_narratives: list[PerformanceNarrative]


@dataclass(frozen=True)
class OutcomeNarrative:
    name: str
    evaluated_count: int
    outcome_score: float
    average_momentum_change: float


@dataclass(frozen=True)
class SignalOutcomeReport:
    signals_evaluated: int
    success: int
    neutral: int
    failed: int
    success_rate: float
    average_mention_change: float
    average_momentum_change: float
    best_narratives: list[OutcomeNarrative]
    worst_narratives: list[OutcomeNarrative]
    average_hype_change: float = 0.0


class TelegramAlerter:
    _command_offsets: ClassVar[dict[tuple[str, str], int]] = {}

    def __init__(
        self, bot_token: str, chat_id: str, slow_threshold_ms: int = 3000,
    ) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._command_key = (bot_token, str(chat_id))
        self._update_offset = self._command_offsets.get(self._command_key)
        self.slow_threshold_ms = slow_threshold_ms

    def _post(self, *args, **kwargs):
        kwargs["_slow_threshold_ms"] = self.slow_threshold_ms
        return _telegram_post(*args, **kwargs)

    def send_hype_alert(
        self,
        alert: HypeAlert,
        watchlist_names: tuple[str, ...] = (),
        ai_analysis: SignalAnalysisResult | None = None,
        unified_event=None,
        unified_items=(),
    ) -> None:
        response = self._post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": format_telegram_hype_alert(
                    alert,
                    watchlist_names,
                    ai_analysis,
                    unified_event,
                    unified_items,
                ),
                "parse_mode": "HTML",
            },
            timeout=30,
        )
        response.raise_for_status()

    def send_rule_alert(
        self,
        rule_name: str,
        priority: int,
        facts: SignalFacts,
    ) -> None:
        signal_name = " + ".join(
            value for value in (facts.token, facts.narrative) if value
        ) or "Unknown"
        text = (
            "<b>Smart Alert Rule Matched</b>\n\n"
            f"<b>Rule:</b> {escape(rule_name)}\n"
            f"<b>Signal:</b> {escape(signal_name)}\n"
            f"<b>Priority:</b> {priority}\n\n"
            f"<b>Hype:</b> {facts.hype_score:.1f}/100\n"
            f"<b>Momentum:</b> {facts.momentum_score:.1f}\n"
            f"<b>Confidence:</b> {facts.confidence}/10\n"
            f"<b>Mentions:</b> {facts.mentions}"
        )
        response = self._post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
            },
            timeout=30,
        )
        response.raise_for_status()

    def send_unified_event_update(self, event, items, reason: str) -> None:
        sources = ", ".join(str(item["source_name"]) for item in items[:6]) or "Unknown"
        conflicts = ""
        if int(event["conflict_count"] or 0):
            conflicts = (
                f"\n<b>Conflicts:</b> {int(event['conflict_count'])} "
                "(review recommended)"
            )
        text = (
            "<b>Crypto Event Update</b>\n"
            f"<b>Event:</b> {_safe_html(event['title'], 180)}\n"
            f"<b>Why:</b> {_safe_html(reason, 180)}\n"
            f"<b>Sources:</b> {_safe_html(sources, 300)}\n"
            f"<b>Coverage:</b> {int(event['source_count'])} sources, "
            f"{int(event['item_count'])} items\n"
            f"<b>Hype:</b> {normalize_hype_score(float(event['hype_score']))}/100\n"
            f"<b>Momentum:</b> {float(event['momentum_score']):.1f}/100"
            f"{conflicts}"
        )
        self._send_html(text)

    def send_source_health_alert(
        self, source_key: str, message: str, recovered: bool = False,
    ) -> None:
        heading = "Source Recovered" if recovered else "Source Fetch Warning"
        self._send_html(
            f"<b>{heading}</b>\n\n"
            f"<b>Source:</b> {_safe_html(source_key, 100)}\n"
            f"<b>Status:</b> {_safe_html(message, 240)}"
        )

    def _send_html(self, text: str) -> None:
        response = self._post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
            timeout=30,
        )
        response.raise_for_status()

    def send_summary(self, summary: NarrativeSummary) -> None:
        response = self._post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": format_telegram_summary(summary),
                "parse_mode": "HTML",
            },
            timeout=30,
        )
        response.raise_for_status()

    def send_trend_report(self, report: TrendReport) -> None:
        response = self._post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": format_telegram_trend_report(report),
                "parse_mode": "HTML",
            },
            timeout=30,
        )
        response.raise_for_status()

    def send_daily_digest(self, digest: DailyDigest) -> None:
        response = self._post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": format_telegram_daily_digest(digest),
                "parse_mode": "HTML",
            },
            timeout=30,
        )
        response.raise_for_status()

    def send_history_report(self, report: MomentumHistoryReport) -> None:
        response = self._post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": format_telegram_history_report(report),
                "parse_mode": "HTML",
            },
            timeout=30,
        )
        response.raise_for_status()

    def send_opportunity_report(self, report: OpportunityReport) -> None:
        response = self._post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": format_telegram_opportunity_report(report),
                "parse_mode": "HTML",
            },
            timeout=30,
        )
        response.raise_for_status()

    def send_performance_report(self, report: SignalPerformanceReport) -> None:
        response = self._post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": format_telegram_performance_report(report),
                "parse_mode": "HTML",
            },
            timeout=30,
        )
        response.raise_for_status()

    def send_outcome_report(self, report: SignalOutcomeReport) -> None:
        response = self._post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": format_telegram_outcome_report(report),
                "parse_mode": "HTML",
            },
            timeout=30,
        )
        response.raise_for_status()

    def poll_performance_commands(self, report: SignalOutcomeReport) -> int:
        return self.poll_commands(report, None)

    def poll_commands(
        self,
        report: SignalOutcomeReport,
        watchlists: WatchlistService | None,
    ) -> int:
        params: dict[str, object] = {
            "timeout": 0,
            "allowed_updates": '["message"]',
        }
        if self._update_offset is not None:
            params["offset"] = self._update_offset
        response = requests.get(
            f"https://api.telegram.org/bot{self.bot_token}/getUpdates",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        handled = 0
        for update in response.json().get("result", []):
            update_id = int(update.get("update_id", 0))
            self._update_offset = max(self._update_offset or 0, update_id + 1)
            self._command_offsets[self._command_key] = self._update_offset
            message = update.get("message") or {}
            chat_id = str((message.get("chat") or {}).get("id", ""))
            text = str(message.get("text", "")).strip()
            parts = text.split(maxsplit=1)
            command = parts[0] if parts else ""
            if chat_id != str(self.chat_id):
                continue
            command = command.split("@", 1)[0].lower()
            if command == "/performance":
                self.send_outcome_report(report)
                handled += 1
            elif command == "/watchlists" and watchlists is not None:
                self._send_html(format_telegram_watchlists(watchlists))
                handled += 1
            elif command == "/watchlist" and watchlists is not None:
                name = parts[1].strip() if len(parts) > 1 else ""
                watchlist = watchlists.get_watchlist(name) if name else None
                if watchlist is None:
                    self._send_html(
                        "<b>Watchlist not found</b>\n"
                        "Use <code>/watchlist &lt;name&gt;</code>."
                    )
                else:
                    self._send_html(
                        format_telegram_watchlist_report(
                            watchlists.report(watchlist.id)
                        )
                    )
                handled += 1
        return handled

    def _send_html(self, text: str) -> None:
        response = self._post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"},
            timeout=30,
        )
        response.raise_for_status()


def format_hype_alert(alert: HypeAlert) -> str:
    posts = "\n".join(
        f"{index}. @{post.username}: {post.text}"
        for index, post in enumerate(alert.top_posts, start=1)
    )
    momentum = "\n".join(f"{item.name} {item.score}" for item in alert.momentum)
    return (
        "🚨 Crypto Hype Spike\n"
        f"Signal: {_alert_title(alert)}\n\n"
        f"{_plain_signal_fields(alert)}"
        f"Hype Score: {_combined_display_hype_score(alert)}/100\n"
        f"{_plain_components(alert)}"
        f"Confidence: {alert.insight.confidence}/10\n"
        f"Action: {alert.insight.action}\n\n"
        f"Why it matters:\n{alert.insight.explanation}\n\n"
        f"Top posts:\n{posts or 'No posts available'}\n\n"
        "Related:\n"
        f"Tokens: {', '.join(alert.related_tokens) or 'None'}\n"
        f"Narratives: {', '.join(alert.related_narratives) or 'None'}"
        f"\n\nNarrative Momentum:\n{momentum or 'None'}"
    )


def format_telegram_hype_alert(
    alert: HypeAlert,
    watchlist_names: tuple[str, ...] = (),
    ai_analysis: SignalAnalysisResult | None = None,
    unified_event=None,
    unified_items=(),
) -> str:
    posts = "\n".join(
        f"{index}. @{_safe_html(post.username, 50)}: {_safe_html(post.text, 180)}"
        for index, post in enumerate(alert.top_posts, start=1)
    )
    tokens = ", ".join(
        _safe_html(token, 30) for token in alert.related_tokens[:8]
    ) or "None"
    narratives = ", ".join(
        _safe_html(item, 30) for item in alert.related_narratives[:8]
    ) or "None"
    momentum = "\n".join(
        f"{_safe_html(item.name, 50)} {item.score}" for item in alert.momentum[:8]
    )
    watchlists = (
        "<b>Watchlists:</b> "
        + ", ".join(_safe_html(name, 40) for name in watchlist_names[:8])
        + "\n\n"
        if watchlist_names
        else ""
    )
    unified_context = ""
    if unified_event is not None:
        source_names = ", ".join(
            _safe_html(item["source_name"], 60) for item in unified_items[:6]
        ) or "Unknown"
        review = "yes" if bool(unified_event["requires_review"]) else "no"
        unified_context = (
            "\n\n<b>Unified event:</b> "
            f"{int(unified_event['source_count'])} sources / "
            f"{int(unified_event['item_count'])} items\n"
            f"<b>Coverage:</b> {source_names}\n"
            f"<b>Requires review:</b> {review}"
        )
    message = (
        "🚨 <b>Crypto Hype Spike</b>\n"
        f"<b>Signal:</b> {_safe_html(_alert_title(alert), 120)}\n\n"
        f"{_html_signal_fields(alert)}"
        f"<b>Hype Score:</b> {_combined_display_hype_score(alert)}/100\n"
        f"{_html_components(alert)}"
        f"<b>Confidence:</b> {alert.insight.confidence}/10\n"
        f"<b>Action:</b> {escape(alert.insight.action)}\n\n"
        f"{watchlists}"
        f"<b>Why it matters:</b>\n{_safe_html(alert.insight.explanation, 400)}\n\n"
        f"<b>Top posts:</b>\n{posts or 'No posts available'}\n\n"
        "<b>Related:</b>\n"
        f"<b>Tokens:</b> {tokens}\n"
        f"<b>Narratives:</b> {narratives}"
        f"\n\n<b>Narrative Momentum:</b>\n{momentum or 'None'}"
        f"{_format_ai_analysis(ai_analysis)}"
        f"{unified_context}"
    )
    return message


def _format_ai_analysis(ai_analysis: SignalAnalysisResult | None) -> str:
    if ai_analysis is None:
        return ""
    action = getattr(ai_analysis.action, "value", ai_analysis.action)
    risk = getattr(ai_analysis.risk_level, "value", ai_analysis.risk_level)
    supports = "\n".join(
        f"• {_safe_html(item, 90)}" for item in ai_analysis.supporting_factors[:2]
    ) or "None"
    risks = "\n".join(
        f"• {_safe_html(item, 90)}" for item in ai_analysis.risk_factors[:2]
    ) or "None"
    invalidation = "\n".join(
        f"• {_safe_html(item, 90)}"
        for item in ai_analysis.invalidation_conditions[:2]
    ) or "None"
    mode = "fallback" if ai_analysis.fallback_used else ai_analysis.provider
    return (
        "\n\n<b>AI Signal Reasoning</b>\n"
        f"<b>Summary:</b> {_safe_html(ai_analysis.summary, 200)}\n"
        f"<b>Why:</b> {_safe_html(ai_analysis.why_it_matters, 300)}\n"
        f"<b>Action:</b> {escape(str(action))}\n"
        f"<b>AI confidence:</b> {ai_analysis.confidence}/10\n"
        f"<b>Risk:</b> {escape(str(risk))}\n"
        f"<b>Mode:</b> {escape(mode)}\n\n"
        f"<b>Supporting factors:</b>\n{supports}\n\n"
        f"<b>Risk factors:</b>\n{risks}\n\n"
        f"<b>Invalidation:</b>\n{invalidation}"
    )


def format_telegram_watchlists(service: WatchlistService) -> str:
    watchlists = service.list_watchlists(enabled=True)
    if not watchlists:
        return "<b>Watchlists</b>\n\nNo enabled watchlists."
    lines = ["<b>Watchlists</b>", ""]
    for watchlist in watchlists:
        report = service.report(watchlist.id)
        rate = (
            f"{report.success_rate:.1f}%"
            if report.success_rate is not None
            else "collecting outcomes"
        )
        lines.append(
            f"<b>{escape(watchlist.name)}</b> - {len(report.items)} items, "
            f"{report.signals_count} recent signals, {rate} success"
        )
    return "\n".join(lines)


def format_telegram_watchlist_report(report) -> str:
    rate = (
        f"{report.success_rate:.1f}%"
        if report.success_rate is not None
        else "collecting outcomes"
    )
    latest = "\n".join(
        f"{index}. {escape(str(item['token'] or item['narrative'] or 'Unknown'))} - "
        f"{float(item['hype_score']):.1f} hype - "
        f"{escape(str(item['outcome_status'] or 'Pending'))}"
        for index, item in enumerate(report.latest_matches[:5], start=1)
    ) or "None"
    return (
        f"<b>Watchlist: {escape(report.watchlist.name)}</b>\n\n"
        f"Status: {'Enabled' if report.watchlist.enabled else 'Disabled'}\n"
        f"Priority: {report.watchlist.priority}\n"
        f"Items: {len(report.items)}\n"
        f"Recent signals: {report.signals_count}\n"
        f"Unified events: {report.unified_event_count}\n"
        f"Raw articles: {report.raw_article_count}\n"
        f"Evaluated: {report.evaluated_count}\n"
        f"Success rate: {rate}\n\n"
        f"<b>Latest matches</b>\n{latest}"
    )


def _combined_hype_score(alert: HypeAlert) -> float:
    if alert.merged_signal is None:
        return alert.signal.hype_score
    if alert.merged_hype_score is not None:
        return alert.merged_hype_score
    return max(alert.signal.hype_score, alert.merged_signal.hype_score)


def _combined_display_hype_score(alert: HypeAlert) -> int:
    return normalize_hype_score(_combined_hype_score(alert))


def _alert_title(alert: HypeAlert) -> str:
    if alert.merged_signal is None:
        return alert.signal.name
    signals = [alert.signal, alert.merged_signal]
    token = next((item.name for item in signals if item.kind == "token"), None)
    narrative = next((item.name for item in signals if item.kind == "narrative"), None)
    return " + ".join(item for item in (token, narrative) if item)


def _plain_signal_fields(alert: HypeAlert) -> str:
    if alert.merged_signal is None:
        return f"Type: {alert.signal.kind}\nToken/Narrative: {alert.signal.name}\n"
    signals = [alert.signal, alert.merged_signal]
    token = next(item.name for item in signals if item.kind == "token")
    narrative = next(item.name for item in signals if item.kind == "narrative")
    return (
        "Type: token + narrative\n"
        f"Token: {token}\n"
        f"Narrative: {narrative}\n"
    )


def _html_signal_fields(alert: HypeAlert) -> str:
    if alert.merged_signal is None:
        return (
            f"<b>Type:</b> {escape(alert.signal.kind)}\n"
            f"<b>Token/Narrative:</b> {_safe_html(alert.signal.name, 80)}\n"
        )
    signals = [alert.signal, alert.merged_signal]
    token = next(item.name for item in signals if item.kind == "token")
    narrative = next(item.name for item in signals if item.kind == "narrative")
    return (
        "<b>Type:</b> token + narrative\n"
        f"<b>Token:</b> {_safe_html(token, 80)}\n"
        f"<b>Narrative:</b> {_safe_html(narrative, 80)}\n"
    )


def _plain_components(alert: HypeAlert) -> str:
    if alert.merged_signal is None:
        return ""
    components = _ordered_components(alert)
    return (
        "Components:\n"
        + "".join(
            f"- {item.name}: {item.display_hype_score}/100\n"
            for item in components
        )
    )


def _html_components(alert: HypeAlert) -> str:
    if alert.merged_signal is None:
        return ""
    components = _ordered_components(alert)
    return (
        "<b>Components:</b>\n"
        + "".join(
            f"• {_safe_html(item.name, 80)}: {item.display_hype_score}/100\n"
            for item in components
        )
    )


def _ordered_components(alert: HypeAlert) -> list[HypeSignal]:
    components = [alert.signal]
    if alert.merged_signal is not None:
        components.append(alert.merged_signal)
    return sorted(components, key=lambda item: 0 if item.kind == "token" else 1)


def _safe_html(value: object, max_length: int) -> str:
    parts: list[str] = []
    length = 0
    for character in str(value):
        escaped = escape(character)
        if length + len(escaped) > max_length:
            break
        parts.append(escaped)
        length += len(escaped)
    return "".join(parts)


def format_summary(summary: NarrativeSummary) -> str:
    tokens = "\n".join(
        f"{index}. {item.name} — hype score {normalize_hype_score(item.hype_score)}/100"
        for index, item in enumerate(summary.top_tokens, start=1)
    )
    narratives = "\n".join(
        f"{index}. {item.name}"
        for index, item in enumerate(summary.top_narratives, start=1)
    )
    posts = "\n".join(
        f"{index}. @{post.username}: {post.text}"
        for index, post in enumerate(summary.important_posts, start=1)
    )
    return (
        "📊 Crypto Narrative Summary\n\n"
        f"Top Tokens:\n{tokens or 'None'}\n\n"
        f"Top Narratives:\n{narratives or 'None'}\n\n"
        f"Most important posts:\n{posts or 'None'}"
    )


def format_telegram_summary(summary: NarrativeSummary) -> str:
    tokens = "\n".join(
        f"{index}. {escape(item.name)} — hype score {normalize_hype_score(item.hype_score)}/100"
        for index, item in enumerate(summary.top_tokens, start=1)
    )
    narratives = "\n".join(
        f"{index}. {escape(item.name)}"
        for index, item in enumerate(summary.top_narratives, start=1)
    )
    posts = "\n".join(
        f"{index}. @{escape(post.username)}: {escape(post.text[:300])}"
        for index, post in enumerate(summary.important_posts, start=1)
    )
    return (
        "📊 <b>Crypto Narrative Summary</b>\n\n"
        f"<b>Top Tokens:</b>\n{tokens or 'None'}\n\n"
        f"<b>Top Narratives:</b>\n{narratives or 'None'}\n\n"
        f"<b>Most important posts:</b>\n{posts or 'None'}"
    )


def format_trend_report(report: TrendReport) -> str:
    top_24h = "\n".join(
        f"{index}. {item.name} — {normalize_hype_score(item.score)}/100"
        for index, item in enumerate(report.top_24h, start=1)
    )
    top_7d = "\n".join(
        f"{index}. {item.name} — {normalize_hype_score(item.score)}/100"
        for index, item in enumerate(report.top_7d, start=1)
    )
    growing = "\n".join(
        f"{item.name} {item.growth_percent:+.0f}%"
        for item in report.fastest_growing
    )
    momentum = "\n".join(f"{item.name} {item.score}" for item in report.momentum)
    return (
        "📈 Crypto Narrative Trend Report\n\n"
        f"Top narratives last 24h\n{top_24h or 'None'}\n\n"
        f"Top narratives last 7d\n{top_7d or 'None'}\n\n"
        f"Fastest growing narratives\n{growing or 'None'}\n\n"
        f"Narrative Momentum\n{momentum or 'None'}"
    )


def format_telegram_trend_report(report: TrendReport) -> str:
    top_24h = "\n".join(
        f"{index}. {escape(item.name)} — {normalize_hype_score(item.score)}/100"
        for index, item in enumerate(report.top_24h, start=1)
    )
    top_7d = "\n".join(
        f"{index}. {escape(item.name)} — {normalize_hype_score(item.score)}/100"
        for index, item in enumerate(report.top_7d, start=1)
    )
    growing = "\n".join(
        f"{escape(item.name)} {item.growth_percent:+.0f}%"
        for item in report.fastest_growing
    )
    momentum = "\n".join(
        f"{escape(item.name)} {item.score}" for item in report.momentum
    )
    return (
        "📈 <b>Crypto Narrative Trend Report</b>\n\n"
        f"<b>Top narratives last 24h</b>\n{top_24h or 'None'}\n\n"
        f"<b>Top narratives last 7d</b>\n{top_7d or 'None'}\n\n"
        f"<b>Fastest growing narratives</b>\n{growing or 'None'}\n\n"
        f"<b>Narrative Momentum</b>\n{momentum or 'None'}"
    )


def format_daily_digest(digest: DailyDigest) -> str:
    tokens = "\n".join(
        f"{index}. {item.name} — hype score {normalize_hype_score(item.hype_score)}/100"
        for index, item in enumerate(digest.top_tokens, start=1)
    )
    narratives = "\n".join(
        f"{index}. {item.name} — hype score {normalize_hype_score(item.hype_score)}/100"
        for index, item in enumerate(digest.top_narratives, start=1)
    )
    growing = (
        f"{digest.fastest_growing.name} {digest.fastest_growing.growth_percent:+.0f}%"
        if digest.fastest_growing
        else "None"
    )
    posts = "\n".join(
        f"{index}. @{post.username}: {post.text}"
        for index, post in enumerate(digest.important_posts, start=1)
    )
    momentum = "\n".join(f"{item.name} {item.score}" for item in digest.momentum)
    return (
        "🗞 Crypto Daily Digest\n\n"
        f"Top 5 tokens last 24h\n{tokens or 'None'}\n\n"
        f"Top 5 narratives last 24h\n{narratives or 'None'}\n\n"
        f"Fastest growing narrative\n{growing}\n\n"
        f"Top articles/posts\n{posts or 'None'}\n\n"
        f"Narrative Momentum\n{momentum or 'None'}\n\n"
        f"Summary\n{digest.final_summary}"
    )


def format_telegram_daily_digest(digest: DailyDigest) -> str:
    tokens = "\n".join(
        f"{index}. {escape(item.name)} — hype score {normalize_hype_score(item.hype_score)}/100"
        for index, item in enumerate(digest.top_tokens, start=1)
    )
    narratives = "\n".join(
        f"{index}. {escape(item.name)} — hype score {normalize_hype_score(item.hype_score)}/100"
        for index, item in enumerate(digest.top_narratives, start=1)
    )
    growing = (
        f"{escape(digest.fastest_growing.name)} "
        f"{digest.fastest_growing.growth_percent:+.0f}%"
        if digest.fastest_growing
        else "None"
    )
    posts = "\n".join(
        f"{index}. @{escape(post.username)}: {escape(post.text[:300])}"
        for index, post in enumerate(digest.important_posts, start=1)
    )
    momentum = "\n".join(
        f"{escape(item.name)} {item.score}" for item in digest.momentum
    )
    return (
        "🗞 <b>Crypto Daily Digest</b>\n\n"
        f"<b>Top 5 tokens last 24h</b>\n{tokens or 'None'}\n\n"
        f"<b>Top 5 narratives last 24h</b>\n{narratives or 'None'}\n\n"
        f"<b>Fastest growing narrative</b>\n{growing}\n\n"
        f"<b>Top articles/posts</b>\n{posts or 'None'}\n\n"
        f"<b>Narrative Momentum</b>\n{momentum or 'None'}\n\n"
        f"<b>Summary</b>\n{escape(digest.final_summary)}"
    )


def format_history_report(report: MomentumHistoryReport) -> str:
    if not report.items:
        return "Narrative Momentum History\n\nNo daily momentum snapshots available."
    sections = []
    for item in report.items:
        previous = (
            str(item.seven_days_ago)
            if item.seven_days_ago is not None
            else "collecting history"
        )
        change = (
            f"{item.change_percent:+.0f}%"
            if item.change_percent is not None
            else "collecting history"
        )
        sections.append(
            f"{item.name}\n"
            f"7d ago: {previous}\n"
            f"Today: {item.today}\n"
            f"Change: {change}"
        )
    return "Narrative Momentum History\n\n" + "\n\n".join(sections)


def format_telegram_history_report(report: MomentumHistoryReport) -> str:
    if not report.items:
        return "<b>Narrative Momentum History</b>\n\nNo daily momentum snapshots available."
    sections = []
    for item in report.items:
        previous = (
            str(item.seven_days_ago)
            if item.seven_days_ago is not None
            else "collecting history"
        )
        change = (
            f"{item.change_percent:+.0f}%"
            if item.change_percent is not None
            else "collecting history"
        )
        sections.append(
            f"<b>{escape(item.name)}</b>\n"
            f"7d ago: {previous}\n"
            f"Today: {item.today}\n"
            f"Change: {change}"
        )
    return "<b>Narrative Momentum History</b>\n\n" + "\n\n".join(sections)


def format_opportunity_report(report: OpportunityReport) -> str:
    if not report.opportunities:
        return "🚀 Top Opportunities\n\nNo momentum history available."
    sections = []
    for index, item in enumerate(report.opportunities, start=1):
        growth = (
            f"{item.growth_percent:+.0f}%"
            if item.growth_percent is not None
            else "collecting history"
        )
        sections.append(
            f"{index}. {item.name}\n"
            f"Momentum: {item.momentum_score}\n"
            f"7d Growth: {growth}\n"
            f"Status: {item.status}"
        )
    return "🚀 Top Opportunities\n\n" + "\n\n".join(sections)


def format_telegram_opportunity_report(report: OpportunityReport) -> str:
    if not report.opportunities:
        return "🚀 <b>Top Opportunities</b>\n\nNo momentum history available."
    sections = []
    for index, item in enumerate(report.opportunities, start=1):
        growth = (
            f"{item.growth_percent:+.0f}%"
            if item.growth_percent is not None
            else "collecting history"
        )
        sections.append(
            f"<b>{index}. {escape(item.name)}</b>\n"
            f"Momentum: {item.momentum_score}\n"
            f"7d Growth: {growth}\n"
            f"Status: {escape(item.status)}"
        )
    return "🚀 <b>Top Opportunities</b>\n\n" + "\n\n".join(sections)


def format_performance_report(report: SignalPerformanceReport) -> str:
    best = _format_performance_narratives(report.best_narratives)
    worst = _format_performance_narratives(report.worst_narratives)
    return (
        "📊 Signal Performance\n\n"
        f"Signals generated: {report.signals_generated}\n"
        f"Successful: {report.successful}\n"
        f"Accuracy: {report.accuracy:.0f}%\n\n"
        f"Average confidence: {report.average_confidence:.1f}/10\n\n"
        f"Average momentum: {report.average_momentum:.1f}/100\n\n"
        f"Best performing narratives\n{best or 'None'}\n\n"
        f"Worst performing narratives\n{worst or 'None'}"
    )


def format_telegram_performance_report(report: SignalPerformanceReport) -> str:
    best = _format_performance_narratives(report.best_narratives, html=True)
    worst = _format_performance_narratives(report.worst_narratives, html=True)
    return (
        "📊 <b>Signal Performance</b>\n\n"
        f"<b>Signals generated:</b> {report.signals_generated}\n"
        f"<b>Successful:</b> {report.successful}\n"
        f"<b>Accuracy:</b> {report.accuracy:.0f}%\n\n"
        f"<b>Average confidence:</b> {report.average_confidence:.1f}/10\n\n"
        f"<b>Average momentum:</b> {report.average_momentum:.1f}/100\n\n"
        f"<b>Best performing narratives</b>\n{best or 'None'}\n\n"
        f"<b>Worst performing narratives</b>\n{worst or 'None'}"
    )


def format_outcome_report(report: SignalOutcomeReport) -> str:
    best = _format_outcome_narratives(report.best_narratives)
    worst = _format_outcome_narratives(report.worst_narratives)
    return (
        "📈 Signal Outcomes\n\n"
        f"Signals evaluated: {report.signals_evaluated}\n"
        f"Successful: {report.success}\n"
        f"Neutral: {report.neutral}\n"
        f"Failed: {report.failed}\n"
        f"Success rate: {report.success_rate:.0f}%\n\n"
        f"Average hype change: {report.average_hype_change:+.1f}\n"
        f"Average momentum change: {report.average_momentum_change:+.1f}\n"
        f"Average mentions change: {report.average_mention_change:+.1f}\n\n"
        f"Best-performing narratives:\n\n{best or 'None'}\n\n"
        f"Worst-performing narratives:\n\n{worst or 'None'}"
    )


def format_telegram_outcome_report(report: SignalOutcomeReport) -> str:
    best = _format_outcome_narratives(report.best_narratives, html=True)
    worst = _format_outcome_narratives(report.worst_narratives, html=True)
    return (
        "📈 <b>Signal Outcomes</b>\n\n"
        f"<b>Signals evaluated:</b> {report.signals_evaluated}\n"
        f"<b>Successful:</b> {report.success}\n"
        f"<b>Neutral:</b> {report.neutral}\n"
        f"<b>Failed:</b> {report.failed}\n"
        f"<b>Success rate:</b> {report.success_rate:.0f}%\n\n"
        f"<b>Average hype change:</b> {report.average_hype_change:+.1f}\n"
        f"<b>Average momentum change:</b> {report.average_momentum_change:+.1f}\n"
        f"<b>Average mentions change:</b> {report.average_mention_change:+.1f}\n\n"
        f"<b>Best-performing narratives</b>\n{best or 'None'}\n\n"
        f"<b>Worst-performing narratives</b>\n{worst or 'None'}"
    )


def _format_outcome_narratives(
    narratives: list[OutcomeNarrative],
    html: bool = False,
) -> str:
    lines = []
    for index, item in enumerate(narratives, start=1):
        name = escape(item.name) if html else item.name
        lines.append(
            f"{index}. {name} — {item.outcome_score:.1f}%"
        )
    return "\n".join(lines)


def _format_performance_narratives(
    narratives: list[PerformanceNarrative],
    html: bool = False,
) -> str:
    lines = []
    for index, item in enumerate(narratives, start=1):
        name = escape(item.name) if html else item.name
        lines.append(
            f"{index}. {name} — momentum {item.average_momentum:.1f}, "
            f"confidence {item.average_confidence:.1f}, signals {item.signals_count}"
        )
    return "\n".join(lines)
