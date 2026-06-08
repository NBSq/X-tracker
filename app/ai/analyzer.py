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


@dataclass(frozen=True)
class SpikeInsight:
    explanation: str
    action: str
    confidence: int


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

    def explain_spike(
        self,
        kind: str,
        name: str,
        hype_score: float,
        top_posts: list[str],
        related_tokens: list[str],
        related_narratives: list[str],
    ) -> SpikeInsight:
        payload = {
            "model": self.model,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "Explain crypto hype spikes concisely. Return strict JSON with "
                        "explanation, action, and confidence. action must be watch, ignore, "
                        "or research. confidence must be an integer from 1 to 10. Do not "
                        "give financial advice."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Signal: {kind} {name}\nHype score: {hype_score:.2f}\n"
                        f"Related tokens: {', '.join(related_tokens)}\n"
                        f"Related narratives: {', '.join(related_narratives)}\n"
                        f"Top posts:\n" + "\n".join(top_posts)
                    ),
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "hype_spike_explanation",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "explanation": {"type": "string"},
                            "action": {
                                "type": "string",
                                "enum": ["watch", "ignore", "research"],
                            },
                            "confidence": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 10,
                            },
                        },
                        "required": ["explanation", "action", "confidence"],
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
        raw = response.json().get("output_text")
        if raw is None:
            raw = self._extract_output_text(response.json())
        parsed = json.loads(raw)
        return SpikeInsight(
            explanation=str(parsed["explanation"]),
            action=str(parsed["action"]),
            confidence=max(1, min(10, int(parsed["confidence"]))),
        )


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
        "XRP": ("xrp", "ripple"),
        "ADA": ("ada", "cardano"),
        "AVAX": ("avax", "avalanche"),
        "BNB": ("bnb",),
        "SUI": ("sui",),
        "JUP": ("jup", "jupiter"),
        "AAVE": ("aave",),
        "UNI": ("uni", "uniswap"),
    }
    NARRATIVE_ALIASES = {
        "ai agents": ("ai agents", "artificial intelligence", "crypto ai"),
        "bitcoin l2": ("bitcoin l2", "bitcoin layer 2", "btc layer 2"),
        "depin": ("depin", "decentralized physical infrastructure"),
        "ethereum scaling": ("ethereum scaling", "ethereum layer 2", "eth scaling"),
        "memecoins": ("memecoins", "memecoin", "meme coin"),
        "modular blockchains": ("modular blockchains", "modular blockchain"),
        "real world assets": ("real world assets", "rwa", "tokenization"),
        "restaking": ("restaking", "re-staking"),
        "solana ecosystem": ("solana ecosystem", "solana"),
        "zero knowledge": ("zero knowledge", "zk proof", "zk rollup"),
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
        narratives = []
        for narrative in self.known_narratives:
            aliases = self.NARRATIVE_ALIASES.get(
                narrative.lower(),
                (narrative.lower(),),
            )
            if any(alias in normalized for alias in aliases):
                narratives.append(narrative)

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

    def explain_spike(
        self,
        kind: str,
        name: str,
        hype_score: float,
        top_posts: list[str],
        related_tokens: list[str],
        related_narratives: list[str],
    ) -> SpikeInsight:
        confidence = max(1, min(10, round(hype_score / 5)))
        if confidence >= 7:
            action = "research"
        elif confidence >= 5:
            action = "watch"
        else:
            action = "ignore"
        explanation = (
            f"{name} is appearing across {len(top_posts)} high-importance posts, "
            f"pushing its {kind} hype score to {hype_score:.2f}. "
            "The clustered attention may signal a developing market narrative."
        )
        return SpikeInsight(explanation=explanation, action=action, confidence=confidence)
