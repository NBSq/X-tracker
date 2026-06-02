from __future__ import annotations

import requests

from app.scoring.hype_score import HypeSignal


class TelegramAlerter:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id

    def send_hype_alert(self, signal: HypeSignal) -> None:
        text = (
            "Crypto hype spike detected\n"
            f"Type: {signal.kind}\n"
            f"Name: {signal.name}\n"
            f"Hype score: {signal.hype_score:.2f}\n"
            f"Mentions: {signal.mentions_count}\n"
            f"Average importance: {signal.average_importance:.2f}"
        )
        response = requests.post(
            f"https://api.telegram.org/bot{self.bot_token}/sendMessage",
            json={"chat_id": self.chat_id, "text": text},
            timeout=30,
        )
        response.raise_for_status()
