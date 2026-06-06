from __future__ import annotations

import json
from pathlib import Path

from app.sources.x_client import XPost


def load_sample_posts(path: Path) -> list[XPost]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Sample posts file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Sample posts file contains invalid JSON: {path}") from exc

    posts = []
    for index, item in enumerate(data.get("posts", []), start=1):
        try:
            posts.append(
                XPost(
                    id=str(item["id"]),
                    username=str(item["username"]).lstrip("@"),
                    text=str(item["text"]),
                    created_at=item.get("created_at"),
                    url=str(item.get("url", f"local://sample/{item['id']}")),
                )
            )
        except KeyError as exc:
            raise RuntimeError(f"Sample post #{index} is missing field: {exc.args[0]}") from exc
    return posts
