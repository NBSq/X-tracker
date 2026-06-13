import unittest
from unittest.mock import patch

from app.ai.analyzer import SpikeInsight
from app.alerts.telegram import (
    AlertPost,
    DailyDigest,
    HypeAlert,
    MomentumHistoryItem,
    MomentumHistoryReport,
    OpportunityReport,
    NarrativeSummary,
    NarrativeGrowth,
    NarrativeTrend,
    SummaryItem,
    TelegramAlerter,
    TrendReport,
    format_hype_alert,
    format_history_report,
    format_opportunity_report,
    format_daily_digest,
    format_summary,
    format_telegram_hype_alert,
    format_telegram_history_report,
    format_telegram_opportunity_report,
    format_telegram_daily_digest,
    format_telegram_summary,
    format_telegram_trend_report,
    format_trend_report,
)
from app.scoring.hype_score import HypeSignal
from app.scoring.momentum_score import NarrativeMomentum
from app.scoring.opportunity_score import NarrativeOpportunity


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
            momentum=[NarrativeMomentum(name="Solana ecosystem", score=92)],
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
        self.trend_report = TrendReport(
            top_24h=[NarrativeTrend(name="AI <Agents>", score=42.0)],
            top_7d=[NarrativeTrend(name="RWA", score=15.0)],
            fastest_growing=[
                NarrativeGrowth(name="AI <Agents>", growth_percent=42.0),
                NarrativeGrowth(name="Memecoins", growth_percent=-8.0),
            ],
            momentum=[
                NarrativeMomentum(name="AI Agents", score=92),
                NarrativeMomentum(name="RWA", score=61),
            ],
        )
        self.daily_digest = DailyDigest(
            top_tokens=[SummaryItem(name="SOL <script>", hype_score=36.0)],
            top_narratives=[SummaryItem(name="AI Agents", hype_score=34.0)],
            fastest_growing=NarrativeGrowth(name="RWA", growth_percent=15.0),
            important_posts=[AlertPost(username="alice", text="SOL is strong <today>")],
            final_summary="SOL and AI <Agents> led attention.",
            momentum=[NarrativeMomentum(name="AI <Agents>", score=92)],
        )
        self.history_report = MomentumHistoryReport(
            items=[
                MomentumHistoryItem(
                    name="AI <Agents>",
                    seven_days_ago=32,
                    today=92,
                    change_percent=187.5,
                )
            ]
        )
        self.opportunity_report = OpportunityReport(
            opportunities=[
                NarrativeOpportunity(
                    name="AI <Agents>",
                    momentum_score=92,
                    growth_percent=187.0,
                    recency_days=0.0,
                    rank_score=98,
                    status="Emerging",
                )
            ]
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
        self.assertIn("Solana ecosystem 92", message)

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

    def test_trend_report_formatters(self) -> None:
        plain = format_trend_report(self.trend_report)
        html = format_telegram_trend_report(self.trend_report)

        self.assertIn("AI <Agents> +42%", plain)
        self.assertIn("Memecoins -8%", plain)
        self.assertIn("AI &lt;Agents&gt;", html)
        self.assertIn("AI Agents 92", plain)

    @patch("app.alerts.telegram.requests.post")
    def test_trend_report_sender_uses_html_parse_mode(self, post) -> None:
        post.return_value.raise_for_status.return_value = None

        TelegramAlerter("test-token", "test-chat").send_trend_report(self.trend_report)

        request = post.call_args
        self.assertEqual(request.kwargs["json"]["parse_mode"], "HTML")
        self.assertIn("Crypto Narrative Trend Report", request.kwargs["json"]["text"])

    def test_daily_digest_formatters(self) -> None:
        plain = format_daily_digest(self.daily_digest)
        html = format_telegram_daily_digest(self.daily_digest)

        self.assertIn("Top 5 tokens last 24h", plain)
        self.assertIn("RWA +15%", plain)
        self.assertIn("SOL &lt;script&gt;", html)
        self.assertIn("AI &lt;Agents&gt;", html)
        self.assertIn("AI &lt;Agents&gt; 92", html)

    @patch("app.alerts.telegram.requests.post")
    def test_daily_digest_sender_uses_html_parse_mode(self, post) -> None:
        post.return_value.raise_for_status.return_value = None

        TelegramAlerter("test-token", "test-chat").send_daily_digest(self.daily_digest)

        request = post.call_args
        self.assertEqual(request.kwargs["json"]["parse_mode"], "HTML")
        self.assertIn("Crypto Daily Digest", request.kwargs["json"]["text"])

    def test_history_report_formatters(self) -> None:
        plain = format_history_report(self.history_report)
        html = format_telegram_history_report(self.history_report)

        self.assertIn("7d ago: 32", plain)
        self.assertIn("Today: 92", plain)
        self.assertIn("Change: +188%", plain)
        self.assertIn("AI &lt;Agents&gt;", html)

    @patch("app.alerts.telegram.requests.post")
    def test_history_report_sender_uses_html_parse_mode(self, post) -> None:
        post.return_value.raise_for_status.return_value = None

        TelegramAlerter("test-token", "test-chat").send_history_report(
            self.history_report
        )

        request = post.call_args
        self.assertEqual(request.kwargs["json"]["parse_mode"], "HTML")
        self.assertIn("Narrative Momentum History", request.kwargs["json"]["text"])

    def test_opportunity_report_formatters(self) -> None:
        plain = format_opportunity_report(self.opportunity_report)
        html = format_telegram_opportunity_report(self.opportunity_report)

        self.assertIn("Momentum: 92", plain)
        self.assertIn("7d Growth: +187%", plain)
        self.assertIn("Status: Emerging", plain)
        self.assertIn("AI &lt;Agents&gt;", html)

    @patch("app.alerts.telegram.requests.post")
    def test_opportunity_report_sender_uses_html_parse_mode(self, post) -> None:
        post.return_value.raise_for_status.return_value = None

        TelegramAlerter("test-token", "test-chat").send_opportunity_report(
            self.opportunity_report
        )

        request = post.call_args
        self.assertEqual(request.kwargs["json"]["parse_mode"], "HTML")
        self.assertIn("Top Opportunities", request.kwargs["json"]["text"])


if __name__ == "__main__":
    unittest.main()
