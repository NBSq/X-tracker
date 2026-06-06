import unittest
from unittest.mock import patch

from app.ai.analyzer import SpikeInsight
from app.alerts.telegram import (
    AlertPost,
    HypeAlert,
    NarrativeSummary,
    SummaryItem,
    TelegramAlerter,
    format_hype_alert,
    format_summary,
    format_telegram_hype_alert,
    format_telegram_summary,
)
from app.scoring.hype_score import HypeSignal


class TelegramAlertTests(unittest.TestCase):
    def setUp(self) -> None:
        signal = HypeSignal(
            kind="token",
            name="SOL <script>",
            mentions_count=4,
            average_importance=9.0,
            hype_score=36.0,
        )
        self.alert = HypeAlert(
            signal=signal,
            insight=SpikeInsight(
                explanation="SOL <script> activity is accelerating.",
                action="research",
                confidence=8,
            ),
            top_posts=[
                AlertPost(username="alice", text="SOL is strong <today>"),
                AlertPost(username="bob", text="Watching Solana ecosystem"),
                AlertPost(username="carol", text="SOL volume is rising"),
            ],
            related_tokens=["JUP", "SOL"],
            related_narratives=["Memecoins", "Solana ecosystem"],
        )
        self.summary = NarrativeSummary(
            top_tokens=[
                SummaryItem(name="SOL <script>", hype_score=36.0),
                SummaryItem(name="TAO", hype_score=26.0),
            ],
            top_narratives=[
                SummaryItem(name="AI Agents", hype_score=34.0),
                SummaryItem(name="Solana ecosystem", hype_score=30.0),
            ],
            important_posts=[
                AlertPost(username="alice", text="SOL is strong <today>"),
                AlertPost(username="bob", text="AI agents are accelerating"),
            ],
        )

    def test_html_formatter_escapes_dynamic_content(self) -> None:
        message = format_telegram_hype_alert(self.alert)

        self.assertIn("🚨 <b>Crypto Hype Spike</b>", message)
        self.assertIn("SOL &lt;script&gt;", message)
        self.assertIn("@alice: SOL is strong &lt;today&gt;", message)
        self.assertNotIn("SOL <script>", message)

    def test_console_formatter_contains_rich_context(self) -> None:
        message = format_hype_alert(self.alert)

        self.assertIn("Confidence: 8/10", message)
        self.assertIn("Action: research", message)
        self.assertIn("1. @alice:", message)
        self.assertIn("Tokens: JUP, SOL", message)

    def test_summary_formatters_include_rankings_and_escape_html(self) -> None:
        plain = format_summary(self.summary)
        html = format_telegram_summary(self.summary)

        self.assertIn("1. SOL <script> — hype score 36.00", plain)
        self.assertIn("1. AI Agents", plain)
        self.assertIn("SOL &lt;script&gt;", html)
        self.assertIn("SOL is strong &lt;today&gt;", html)

    @patch("app.alerts.telegram.requests.post")
    def test_sender_uses_html_parse_mode(self, post) -> None:
        post.return_value.raise_for_status.return_value = None

        TelegramAlerter("test-token", "test-chat").send_hype_alert(self.alert)

        request = post.call_args
        self.assertEqual(request.kwargs["json"]["parse_mode"], "HTML")
        self.assertEqual(request.kwargs["json"]["chat_id"], "test-chat")
        self.assertEqual(request.kwargs["timeout"], 30)

    @patch("app.alerts.telegram.requests.post")
    def test_summary_sender_uses_html_parse_mode(self, post) -> None:
        post.return_value.raise_for_status.return_value = None

        TelegramAlerter("test-token", "test-chat").send_summary(self.summary)

        request = post.call_args
        self.assertEqual(request.kwargs["json"]["parse_mode"], "HTML")
        self.assertIn("Crypto Narrative Summary", request.kwargs["json"]["text"])


if __name__ == "__main__":
    unittest.main()
