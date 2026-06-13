import unittest

from app.ai.analyzer import LocalAnalyzer


class MockAIAnalyzerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.analyzer = LocalAnalyzer(
            [
                "Bitcoin / macro",
                "Ethereum / L2",
                "Solana ecosystem",
                "AI agents",
                "DePIN",
                "RWA",
                "Memecoins",
                "Gaming",
                "Stablecoins",
                "ETFs",
                "Regulation",
                "Privacy",
                "DeFi",
                "Infrastructure",
            ]
        )

    def test_btc_macro_article(self) -> None:
        result = self.analyzer.analyze_post(
            "BTC rises as Fed rates, stocks, and risk assets react to macro data."
        )

        self.assertIn("BTC", result.tokens)
        self.assertIn("Bitcoin / macro", result.narratives)

    def test_bitcoin_etf_article(self) -> None:
        result = self.analyzer.analyze_post(
            "BlackRock spot Bitcoin ETF sees another day of strong inflows."
        )

        self.assertIn("BTC", result.tokens)
        self.assertIn("Bitcoin / macro", result.narratives)
        self.assertIn("ETFs", result.narratives)

    def test_solana_meme_article(self) -> None:
        result = self.analyzer.analyze_post(
            "SOL activity jumps as BONK and WIF memecoins rally on Solana."
        )

        self.assertIn("SOL", result.tokens)
        self.assertIn("BONK", result.tokens)
        self.assertIn("WIF", result.tokens)
        self.assertIn("Solana ecosystem", result.narratives)
        self.assertIn("Memecoins", result.narratives)

    def test_ai_article(self) -> None:
        result = self.analyzer.analyze_post(
            "AI agents drive demand for TAO, FET, and RNDR decentralized compute."
        )

        self.assertEqual(result.tokens, ["TAO", "FET", "RNDR"])
        self.assertIn("AI agents", result.narratives)

    def test_rwa_article(self) -> None:
        result = self.analyzer.analyze_post(
            "Tokenized treasuries push real world assets and RWA adoption forward."
        )

        self.assertIn("RWA", result.narratives)

    def test_deduplicates_and_normalizes_narratives(self) -> None:
        result = self.analyzer.analyze_post(
            "RWA tokenization expands across real world assets."
        )

        self.assertEqual(result.narratives.count("RWA"), 1)

    def test_detects_bearish_language(self) -> None:
        result = self.analyzer.analyze_post(
            "ARB looks weak with bearish breakdown and liquidation risk."
        )

        self.assertIn("ARB", result.tokens)
        self.assertEqual(result.sentiment, "bearish")


if __name__ == "__main__":
    unittest.main()
