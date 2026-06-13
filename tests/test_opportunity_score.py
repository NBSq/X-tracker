import unittest

from app.scoring.opportunity_score import build_opportunity


class OpportunityScoreTests(unittest.TestCase):
    def test_high_momentum_and_growth_is_emerging(self) -> None:
        result = build_opportunity(
            name="AI Agents",
            momentum_score=92,
            growth_percent=187.0,
            recency_days=0.0,
        )

        self.assertEqual(result.status, "Emerging")
        self.assertGreaterEqual(result.rank_score, 90)

    def test_moderate_growth_is_growing(self) -> None:
        result = build_opportunity(
            name="RWA",
            momentum_score=61,
            growth_percent=38.0,
            recency_days=0.0,
        )

        self.assertEqual(result.status, "Growing")

    def test_low_growth_is_watchlist(self) -> None:
        result = build_opportunity(
            name="DePIN",
            momentum_score=55,
            growth_percent=10.0,
            recency_days=2.0,
        )

        self.assertEqual(result.status, "Watchlist")


if __name__ == "__main__":
    unittest.main()
