import unittest

from app.scoring.momentum_score import calculate_momentum_score


class MomentumScoreTests(unittest.TestCase):
    def test_strong_recent_growing_narrative_scores_high(self) -> None:
        score = calculate_momentum_score(
            mentions_count=10,
            average_importance=9.0,
            growth_percent=150.0,
            recency_hours=1.0,
        )

        self.assertGreaterEqual(score, 85)
        self.assertLessEqual(score, 100)

    def test_old_low_importance_narrative_scores_low(self) -> None:
        score = calculate_momentum_score(
            mentions_count=1,
            average_importance=2.0,
            growth_percent=-20.0,
            recency_hours=23.0,
        )

        self.assertLess(score, 20)


if __name__ == "__main__":
    unittest.main()
