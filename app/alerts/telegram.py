from __future__ import annotations

from html import escape

import requests

from app.scoring.hype_score import HypeSignal


def build_signal_summary(signal: HypeSignal) -> str:
    return f"{signal.name} is getting unusual attention across recent crypto posts."


class TelegramAlerter:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send_hype_alert(self, signal: HypeSignal) -> None:
        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={
                "chat_id": self.chat_id,
                "text": format_telegram_hype_alert(signal),
                "parse_mode": "HTML",
            },
            timeout=30,
        )
        response.raise_for_status()


def format_hype_alert(signal: HypeSignal) -> str:
    return (
        "🚨 Crypto Hype Spike Detected\n\n"
        f"Type: {signal.kind}\n"
        f"Name: {signal.name}\n\n"
        f"Hype Score: {signal.hype_score:.2f}\n"
        f"Mentions: {signal.mentions_count}\n"
        f"Average Importance: {signal.average_importance:.2f}\n\n"
        "Summary:\n"
        f"{build_signal_summary(signal)}"
    )


def format_telegram_hype_alert(signal: HypeSignal) -> str:
    kind = escape(signal.kind)
    name = escape(signal.name)
    summary = escape(build_signal_summary(signal))
    return (
        "🚨 <b>Crypto Hype Spike Detected</b>\n\n"
        f"<b>Type:</b> {kind}\n"
        f"<b>Name:</b> {name}\n\n"
        f"<b>Hype Score:</b> {signal.hype_score:.2f}\n"
        f"<b>Mentions:</b> {signal.mentions_count}\n"
        f"<b>Average Importance:</b> {signal.average_importance:.2f}\n\n"
        "<b>Summary:</b>\n"
        f"{summary}"
    )
