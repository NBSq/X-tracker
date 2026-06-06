from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import requests


OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


@dataclass(frozen=True)
class AnalysisResult:
    tokens: list[str]
    narratives: list[str]
    sentiment: str
    importance: int
    summary: str


class OpenAIAnalyzer:
    def __init__(self, api_key: str, model: str, known_narratives: list[str]) -> None:
        self.api_key = api_key
        self.model = model
        self.known_narratives = known_narratives

    def analyze_post(self, text: str) -> AnalysisResult:
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "You analyze crypto Twitter/X posts. Return only strict JSON with keys: "
                        "tokens, narratives, sentiment, importance, summary. "
                        "tokens and narratives must be arrays of strings. sentiment must be one of "
                        "bullish, bearish, neutral. importance must be an integer from 1 to 10."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Known narratives: {', '.join(self.known_narratives)}\n\n"
                        f"Analyze this post:\n{text}"
                    ),
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "x_post_analysis",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "tokens": {"type": "array", "items": {"type": "string"}},
                            "narratives": {"type": "array", "items": {"type": "string"}},
                            "sentiment": {
                                "type": "string",
                                "enum": ["bullish", "bearish", "neutral"],
                            },
                            "importance": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 10,
                            },
                            "summary": {"type": "string"},
                        },
                        "required": [
                            "tokens",
                            "narratives",
                            "sentiment",
                            "importance",
                            "summary",
                        ],
                    },
                }
            },
        }

        response = requests.post(
            OPENAI_RESPONSES_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        return self._parse_response(response.json())

    def _parse_response(self, data: dict[str, Any]) -> AnalysisResult:
        raw = data.get("output_text")
        if raw is None:
            raw = self._extract_output_text(data)
        parsed = json.loads(raw)

        return AnalysisResult(
            tokens=[str(token).upper() for token in parsed.get("tokens", [])],
            narratives=[str(narrative) for narrative in parsed.get("narratives", [])],
            sentiment=str(parsed["sentiment"]),
            importance=max(1, min(10, int(parsed["importance"]))),
            summary=str(parsed.get("summary", "")),
        )

    def _extract_output_text(self, data: dict[str, Any]) -> str:
        chunks = []
        for output in data.get("output", []):
            for content in output.get("content", []):
                if content.get("type") in {"output_text", "text"} and "text" in content:
                    chunks.append(content["text"])
        if not chunks:
            raise ValueError("OpenAI response did not contain output text")
        return "".join(chunks)


class LocalAnalyzer:
    TOKEN_ALIASES = {
        "BTC": ("btc", "bitcoin"),
        "ETH": ("eth", "ethereum"),
        "SOL": ("sol", "solana"),
        "LINK": ("link", "chainlink"),
        "ONDO": ("ondo",),
        "RNDR": ("rndr", "render"),
        "TAO": ("tao", "bittensor"),
        "DOGE": ("doge", "dogecoin"),
        "PEPE": ("pepe",),
        "ARB": ("arb", "arbitrum"),
    }
    BULLISH_WORDS = {
        "bullish", "breakout", "buy", "growth", "rally", "strong", "surge",
        "upside", "adoption", "accumulating", "outperform",
    }
    BEARISH_WORDS = {
        "bearish", "breakdown", "dump", "risk", "sell", "weak", "downside",
        "overheated", "avoid", "liquidation",
    }

    def __init__(self, known_narratives: list[str]) -> None:
        self.known_narratives = known_narratives

    def analyze_post(self, text: str) -> AnalysisResult:
        normalized = text.lower()
        words = set(re.findall(r"[a-z0-9]+", normalized))
        tokens = [
            token
            for token, aliases in self.TOKEN_ALIASES.items()
            if any(alias.lower() in words for alias in aliases)
        ]
        narratives = [
            narrative
            for narrative in self.known_narratives
            if narrative.lower() in normalized
        ]

        bullish_hits = len(words & self.BULLISH_WORDS)
        bearish_hits = len(words & self.BEARISH_WORDS)
        if bullish_hits > bearish_hits:
            sentiment = "bullish"
        elif bearish_hits > bullish_hits:
            sentiment = "bearish"
        else:
            sentiment = "neutral"

        importance = min(10, 4 + bullish_hits + bearish_hits + len(tokens) + len(narratives))
        return AnalysisResult(
            tokens=tokens,
            narratives=narratives,
            sentiment=sentiment,
            importance=importance,
            summary=text.strip()[:180],
        )
