import os
import unittest
from unittest.mock import patch

from app.config import load_config


class ConfigTests(unittest.TestCase):
    def test_default_outcome_windows(self) -> None:
        with patch.dict(
            os.environ,
            {"OUTCOME_EVALUATION_HOURS": "", "OUTCOME_EVALUATION_WINDOWS": ""},
        ):
            os.environ.pop("OUTCOME_EVALUATION_HOURS")
            os.environ.pop("OUTCOME_EVALUATION_WINDOWS")
            self.assertEqual(load_config().outcome_evaluation_windows, (24, 72, 168))

    def test_configured_windows_are_positive_unique_in_order(self) -> None:
        with patch.dict(os.environ, {"OUTCOME_EVALUATION_WINDOWS": "72,24,72,168"}):
            self.assertEqual(load_config().outcome_evaluation_windows, (72, 24, 168))

    def test_legacy_single_window_is_honored(self) -> None:
        with patch.dict(
            os.environ,
            {"OUTCOME_EVALUATION_HOURS": "48", "OUTCOME_EVALUATION_WINDOWS": ""},
        ):
            os.environ.pop("OUTCOME_EVALUATION_WINDOWS")
            self.assertEqual(load_config().outcome_evaluation_windows, (48,))

    def test_invalid_outcome_windows_fail_fast(self) -> None:
        with patch.dict(os.environ, {"OUTCOME_EVALUATION_WINDOWS": "24,0"}):
            with self.assertRaisesRegex(RuntimeError, "positive evaluation windows"):
                load_config()


if __name__ == "__main__":
    unittest.main()
