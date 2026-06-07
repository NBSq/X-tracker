import unittest
from unittest.mock import Mock

from app.sources.rss_client import RSSClient, RSSFeed


class RSSClientTests(unittest.TestCase):
    def test_parses_rss_article_into_shared_post_format(self) -> None:
        xml = b"""
        <rss><channel><item>
          <title>SOL adoption grows</title>
          <description><![CDATA[<p>Solana activity is rising.</p>]]></description>
          <link>https://example.com/sol</link>
          <guid>article-1</guid>
          <author>News Desk</author>
          <pubDate>Sun, 07 Jun 2026 10:00:00 GMT</pubDate>
        </item></channel></rss>
        """
        client = RSSClient()
        response = Mock(content=xml)
        response.raise_for_status.return_value = None
        client.session.get = Mock(return_value=response)

        posts = client.fetch_recent_posts([RSSFeed("Example", "https://example.com/rss")])

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].author, "News Desk")
        self.assertEqual(posts[0].url, "https://example.com/sol")
        self.assertIn("SOL adoption grows", posts[0].text)
        self.assertIn("Solana activity is rising.", posts[0].text)

    def test_parses_atom_article(self) -> None:
        xml = b"""
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>Bitcoin update</title>
            <summary>BTC activity is rising.</summary>
            <link href="https://example.com/btc"/>
            <id>article-2</id>
            <author><name>Example Author</name></author>
            <updated>2026-06-07T10:00:00Z</updated>
          </entry>
        </feed>
        """
        client = RSSClient()
        response = Mock(content=xml)
        response.raise_for_status.return_value = None
        client.session.get = Mock(return_value=response)

        posts = client.fetch_recent_posts([RSSFeed("Example", "https://example.com/atom")])

        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0].author, "Example Author")
        self.assertEqual(posts[0].created_at, "2026-06-07T10:00:00Z")


if __name__ == "__main__":
    unittest.main()
