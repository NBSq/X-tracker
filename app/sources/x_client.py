from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import requests


API_BASE = "https://api.twitter.com/2"


@dataclass(frozen=True)
class XPost:
    id: str
    username: str
    text: str
    created_at: str | None
    url: str


class XClient:
    def __init__(self, bearer_token: str) -> None:
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {bearer_token}"})

    def fetch_recent_posts(self, usernames: Iterable[str], limit: int = 10) -> list[XPost]:
        posts: list[XPost] = []
        for username in usernames:
            user_id = self._get_user_id(username)
            if not user_id:
                continue
            posts.extend(self._get_user_posts(user_id, username, limit))
        return posts

    def _get_user_id(self, username: str) -> str | None:
        response = self.session.get(
            f"{API_BASE}/users/by/username/{username}",
            params={"user.fields": "username"},
            timeout=30,
        )
        if response.status_code == 404:
            print(f"X user not found: {username}")
            return None
        response.raise_for_status()
        data = response.json().get("data")
        return data["id"] if data else None

    def _get_user_posts(self, user_id: str, username: str, limit: int) -> list[XPost]:
        response = self.session.get(
            f"{API_BASE}/users/{user_id}/tweets",
            params={
                "max_results": max(5, min(limit, 100)),
                "tweet.fields": "created_at",
                "exclude": "retweets,replies",
            },
            timeout=30,
        )
        response.raise_for_status()

        posts = []
        for item in response.json().get("data", []):
            post_id = item["id"]
            posts.append(
                XPost(
                    id=post_id,
                    username=username,
                    text=item.get("text", ""),
                    created_at=item.get("created_at"),
                    url=f"https://x.com/{username}/status/{post_id}",
                )
            )
        return posts
