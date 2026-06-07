from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
import json

import requests

from app.sources.x_client import XPost


logger = logging.getLogger("x_narrative_tracker")
ATOM_NAMESPACE = {"atom": "http://www.w3.org/2005/Atom"}
DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"


@dataclass(frozen=True)
class RSSFeed:
    name: str
    url: str


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        cleaned = data.strip()
        if cleaned:
            self.parts.append(cleaned)


def load_rss_feeds(path: Path) -> list[RSSFeed]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise RuntimeError(f"RSS feed configuration not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"RSS feed configuration contains invalid JSON: {path}") from exc

    feeds = []
    for index, item in enumerate(data.get("RSS_FEEDS", []), start=1):
        try:
            feeds.append(RSSFeed(name=str(item["name"]), url=str(item["url"])))
        except KeyError as exc:
            raise RuntimeError(f"RSS feed #{index} is missing field: {exc.args[0]}") from exc
    return feeds


class RSSClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {"User-Agent": "x-narrative-tracker/0.1 (+local RSS reader)"}
        )

    def fetch_recent_posts(
        self,
        feeds: list[RSSFeed],
        limit_per_feed: int = 10,
    ) -> list[XPost]:
        posts = []
        for feed in feeds:
            try:
                posts.extend(self._fetch_feed(feed, limit_per_feed))
            except Exception:
                logger.exception("Could not fetch RSS feed: %s", feed.name)
        return posts

    def _fetch_feed(self, feed: RSSFeed, limit: int) -> list[XPost]:
        response = self.session.get(feed.url, timeout=30)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        items = root.findall("./channel/item")
        if items:
            return [self._rss_item_to_post(feed, item) for item in items[:limit]]

        entries = root.findall("atom:entry", ATOM_NAMESPACE)
        return [self._atom_entry_to_post(feed, entry) for entry in entries[:limit]]

    def _rss_item_to_post(self, feed: RSSFeed, item: ET.Element) -> XPost:
        title = item.findtext("title", default="")
        description = item.findtext("description", default="")
        url = item.findtext("link", default="").strip()
        identifier = item.findtext("guid", default="").strip() or url or title
        author = (
            item.findtext("author")
            or item.findtext(DC_CREATOR)
            or feed.name
        )
        return XPost(
            id=_stable_article_id(identifier),
            username=_clean_text(author) or feed.name,
            text=_article_text(title, description),
            url=url,
            created_at=item.findtext("pubDate"),
        )

    def _atom_entry_to_post(self, feed: RSSFeed, entry: ET.Element) -> XPost:
        title = entry.findtext("atom:title", default="", namespaces=ATOM_NAMESPACE)
        description = (
            entry.findtext("atom:summary", default="", namespaces=ATOM_NAMESPACE)
            or entry.findtext("atom:content", default="", namespaces=ATOM_NAMESPACE)
        )
        link = entry.find("atom:link", ATOM_NAMESPACE)
        url = link.get("href", "") if link is not None else ""
        identifier = (
            entry.findtext("atom:id", default="", namespaces=ATOM_NAMESPACE)
            or url
            or title
        )
        author = entry.findtext(
            "atom:author/atom:name",
            default=feed.name,
            namespaces=ATOM_NAMESPACE,
        )
        created_at = (
            entry.findtext("atom:published", default="", namespaces=ATOM_NAMESPACE)
            or entry.findtext("atom:updated", default="", namespaces=ATOM_NAMESPACE)
        )
        return XPost(
            id=_stable_article_id(identifier),
            username=_clean_text(author) or feed.name,
            text=_article_text(title, description),
            url=url,
            created_at=created_at,
        )


def _stable_article_id(identifier: str) -> str:
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
    return f"rss-{digest}"


def _article_text(title: str, description: str) -> str:
    clean_title = _clean_text(title)
    clean_description = _clean_text(description)
    return f"{clean_title}. {clean_description}".strip(" .")[:5000]


def _clean_text(value: str) -> str:
    parser = _TextExtractor()
    parser.feed(unescape(value or ""))
    return " ".join(parser.parts)
