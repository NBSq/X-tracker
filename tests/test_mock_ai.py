import unittest

from app.ai.analyzer import LocalAnalyzer


class MockAIAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = LocalAnalyzer(
            ["AI agents", "Solana ecosystem", "real world assets"]
        )

    def test_detects_tokens_narratives_sentiment_and_importance(self) -> None:
        result = self.analyzer.analyze_post(
            "Bullish SOL breakout as Solana ecosystem adoption shows strong growth."
        )

        self.assertEqual(result.tokens, ["SOL"])
        self.assertEqual(result.narratives, ["Solana ecosystem"])
        self.assertEqual(result.sentiment, "bullish")
        self.assertGreaterEqual(result.importance, 7)

    def test_detects_bearish_language(self) -> None:
        result = self.analyzer.analyze_post(
            "ARB looks weak with bearish breakdown and liquidation risk."
        )

        self.assertEqual(result.tokens, ["ARB"])
        self.assertEqual(result.sentiment, "bearish")

    def test_detects_common_narrative_shorthand(self) -> None:
        result = self.analyzer.analyze_post(
            "RWA tokenization is expanding as ONDO adoption grows."
        )

        self.assertEqual(result.tokens, ["ONDO"])
        self.assertEqual(result.narratives, ["real world assets"])


if __name__ == "__main__":
    unittest.main()
