import unittest

from app.scoring.hype_score import (
    interpret_display_hype_score,
    normalize_hype_score,
)


class HypeScoreTests(unittest.TestCase):
    def test_normalizes_raw_hype_score_to_display_score(self) -> None:
        self.assertEqual(normalize_hype_score(116), 92)

    def test_normalized_score_never_exceeds_100(self) -> None:
        self.assertEqual(normalize_hype_score(1_000_000), 100)

    def test_interprets_display_score_ranges(self) -> None:
        self.assertEqual(interpret_display_hype_score(20), "Low")
        self.assertEqual(interpret_display_hype_score(40), "Moderate")
        self.assertEqual(interpret_display_hype_score(60), "Strong")
        self.assertEqual(interpret_display_hype_score(80), "High")
        self.assertEqual(interpret_display_hype_score(81), "Extreme")


if __name__ == "__main__":
    unittest.main()
