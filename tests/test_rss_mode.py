import unittest
from unittest.mock import Mock, patch

from app.main import run_rss


class RSSModeTests(unittest.TestCase):
    @patch("app.main.run_rss_once")
    def test_rss_runs_once_by_default(self, run_once) -> None:
        config = Mock(openai_api_key=None)
        database = Mock()

        run_rss(config, database, mock_ai=True)

        run_once.assert_called_once_with(config, database, False, True)


if __name__ == "__main__":
    unittest.main()
