from __future__ import annotations

from dataclasses import dataclass
from html import escape

import requests

from app.ai.analyzer import SpikeInsight
from app.scoring.hype_score import HypeSignal


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


class TelegramAlerter:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send_hype_alert(self, alert: HypeAlert) -> None:
        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": format_telegram_hype_alert(alert),
                "parse_mode": "HTML",
            },
            timeout=30,
        )
        response.raise_for_status()

    def send_summary(self, summary: NarrativeSummary) -> None:
        response = requests.post(
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
        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": format_telegram_trend_report(report),
                "parse_mode": "HTML",
            },
            timeout=30,
        )
        response.raise_for_status()


def format_hype_alert(alert: HypeAlert) -> str:
    signal = alert.signal
    posts = "\n".join(
        f"{index}. @{post.username}: {post.text}"
        for index, post in enumerate(alert.top_posts, start=1)
    )
    return (
        "🚨 Crypto Hype Spike\n\n"
        f"Token/Narrative: {signal.name}\n"
        f"Hype Score: {signal.hype_score:.2f}\n"
        f"Confidence: {alert.insight.confidence}/10\n"
        f"Action: {alert.insight.action}\n\n"
        f"Why it matters:\n{alert.insight.explanation}\n\n"
        f"Top posts:\n{posts or 'No posts available'}\n\n"
        "Related:\n"
        f"Tokens: {', '.join(alert.related_tokens) or 'None'}\n"
        f"Narratives: {', '.join(alert.related_narratives) or 'None'}"
    )


def format_telegram_hype_alert(alert: HypeAlert) -> str:
    signal = alert.signal
    posts = "\n".join(
        f"{index}. @{escape(post.username)}: {escape(post.text[:300])}"
        for index, post in enumerate(alert.top_posts, start=1)
    )
    tokens = ", ".join(escape(token) for token in alert.related_tokens) or "None"
    narratives = ", ".join(escape(item) for item in alert.related_narratives) or "None"
    return (
        "🚨 <b>Crypto Hype Spike</b>\n\n"
        f"<b>Token/Narrative:</b> {escape(signal.name)}\n"
        f"<b>Hype Score:</b> {signal.hype_score:.2f}\n"
        f"<b>Confidence:</b> {alert.insight.confidence}/10\n"
        f"<b>Action:</b> {escape(alert.insight.action)}\n\n"
        f"<b>Why it matters:</b>\n{escape(alert.insight.explanation)}\n\n"
        f"<b>Top posts:</b>\n{posts or 'No posts available'}\n\n"
        "<b>Related:</b>\n"
        f"<b>Tokens:</b> {tokens}\n"
        f"<b>Narratives:</b> {narratives}"
    )


def format_summary(summary: NarrativeSummary) -> str:
    tokens = "\n".join(
        f"{index}. {item.name} — hype score {item.hype_score:.2f}"
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
        f"{index}. {escape(item.name)} — hype score {item.hype_score:.2f}"
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
        f"{index}. {item.name} — {item.score:.2f}"
        for index, item in enumerate(report.top_24h, start=1)
    )
    top_7d = "\n".join(
        f"{index}. {item.name} — {item.score:.2f}"
        for index, item in enumerate(report.top_7d, start=1)
    )
    growing = "\n".join(
        f"{item.name} {item.growth_percent:+.0f}%"
        for item in report.fastest_growing
    )
    return (
        "📈 Crypto Narrative Trend Report\n\n"
        f"Top narratives last 24h\n{top_24h or 'None'}\n\n"
        f"Top narratives last 7d\n{top_7d or 'None'}\n\n"
        f"Fastest growing narratives\n{growing or 'None'}"
    )


def format_telegram_trend_report(report: TrendReport) -> str:
    top_24h = "\n".join(
        f"{index}. {escape(item.name)} — {item.score:.2f}"
        for index, item in enumerate(report.top_24h, start=1)
    )
    top_7d = "\n".join(
        f"{index}. {escape(item.name)} — {item.score:.2f}"
        for index, item in enumerate(report.top_7d, start=1)
    )
    growing = "\n".join(
        f"{escape(item.name)} {item.growth_percent:+.0f}%"
        for item in report.fastest_growing
    )
    return (
        "📈 <b>Crypto Narrative Trend Report</b>\n\n"
        f"<b>Top narratives last 24h</b>\n{top_24h or 'None'}\n\n"
        f"<b>Top narratives last 7d</b>\n{top_7d or 'None'}\n\n"
        f"<b>Fastest growing narratives</b>\n{growing or 'None'}"
    )
