import unittest
from unittest.mock import patch

from app.alerts.telegram import (
    TelegramAlerter,
    format_hype_alert,
    format_telegram_hype_alert,
)
from app.scoring.hype_score import HypeSignal


class TelegramAlertTests(unittest.TestCase):
    def setUp(self) -> None:
        self.signal = HypeSignal(
            kind="token",
            name="SOL <script>",
            mentions_count=4,
            average_importance=9.0,
            hype_score=36.0,
        )

    def test_html_formatter_escapes_dynamic_content(self) -> None:
        message = format_telegram_hype_alert(self.signal)

        self.assertIn("🚨 <b>Crypto Hype Spike Detected</b>", message)
        self.assertIn("SOL &lt;script&gt;", message)
        self.assertNotIn("SOL <script>", message)

    def test_console_formatter_remains_plain_text(self) -> None:
        message = format_hype_alert(self.signal)

        self.assertIn("🚨 Crypto Hype Spike Detected", message)
        self.assertIn("Hype Score: 36.00", message)
        self.assertIn("Summary:", message)

    @patch("app.alerts.telegram.requests.post")
    def test_sender_uses_html_parse_mode(self, post) -> None:
        post.return_value.raise_for_status.return_value = None

        TelegramAlerter("test-token", "test-chat").send_hype_alert(self.signal)

        post.assert_called_once()
        request = post.call_args
        self.assertEqual(
            request.args[0],
            "https://api.telegram.org/bottest-token/sendMessage",
        )
        self.assertEqual(request.kwargs["json"]["parse_mode"], "HTML")
        self.assertEqual(request.kwargs["json"]["chat_id"], "test-chat")
        self.assertIn("SOL &lt;script&gt;", request.kwargs["json"]["text"])
        self.assertEqual(request.kwargs["timeout"], 30)


if __name__ == "__main__":
    unittest.main()
