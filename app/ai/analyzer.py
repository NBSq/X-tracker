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
        "BTC": ("bitcoin",),
        "ETH": ("ethereum", "ether"),
        "SOL": ("solana",),
        "BNB": ("binance coin",),
        "XRP": ("ripple",),
        "DOGE": ("dogecoin",),
        "ADA": ("cardano",),
        "AVAX": ("avalanche",),
        "LINK": ("chainlink",),
        "TON": ("the open network", "toncoin"),
        "ARB": ("arbitrum",),
        "OP": ("optimism",),
        "SUI": (),
        "APT": ("aptos",),
        "INJ": ("injective",),
        "SEI": (),
        "TAO": ("bittensor",),
        "FET": ("fetch.ai", "fetch ai"),
        "RNDR": ("render",),
        "NEAR": ("near protocol",),
        "TIA": ("celestia",),
        "JUP": ("jupiter",),
        "WIF": ("dogwifhat",),
        "PEPE": (),
        "BONK": (),
    }
    LOWERCASE_TICKERS = {
        "btc", "eth", "sol", "bnb", "xrp", "doge", "ada", "avax",
        "arb", "sui", "apt", "inj", "tao", "fet", "rndr", "tia", "jup",
        "wif", "pepe", "bonk",
    }
    NARRATIVE_KEYWORDS = {
        "Bitcoin / macro": (
            "bitcoin", "btc", "fed", "rates", "interest rate", "macro", "oil",
            "stocks", "risk assets", "risk asset", "inflation", "liquidity",
        ),
        "Ethereum / L2": (
            "ethereum", "eth", "layer 2", "l2", "rollup", "arbitrum",
            "optimism", "base chain", "zk rollup",
        ),
        "Solana ecosystem": (
            "solana", "sol", "jupiter", "jup", "bonk", "wif",
        ),
        "AI agents": (
            "ai", "artificial intelligence", "agent", "agents", "bittensor",
            "tao", "fetch.ai", "fet", "render", "rndr",
        ),
        "DePIN": (
            "depin", "decentralized physical infrastructure", "physical infrastructure",
        ),
        "RWA": (
            "real world assets", "real-world assets", "tokenized", "tokenization",
            "treasury", "treasuries", "rwa",
        ),
        "Memecoins": (
            "memecoin", "memecoins", "meme coin", "meme coins", "doge",
            "dogecoin", "pepe", "bonk", "wif", "dogwifhat",
        ),
        "Gaming": (
            "gaming", "gamefi", "blockchain game", "web3 game", "play to earn",
        ),
        "Stablecoins": (
            "stablecoin", "stablecoins", "usdt", "usdc", "tether", "circle",
        ),
        "ETFs": (
            "etf", "etfs", "blackrock", "spot bitcoin etf", "spot ether etf",
        ),
        "Regulation": (
            "sec", "regulation", "regulator", "lawsuit", "mica", "compliance",
        ),
        "Privacy": (
            "privacy", "private transaction", "zero knowledge", "zk proof",
            "monero", "zcash",
        ),
        "DeFi": (
            "defi", "decentralized finance", "dex", "lending protocol",
            "liquidity pool", "yield farming", "uniswap", "aave",
        ),
        "Infrastructure": (
            "infrastructure", "oracle", "chainlink", "validator", "rpc",
            "modular blockchain", "data availability", "interoperability",
        ),
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
        tokens = []
        for token, aliases in self.TOKEN_ALIASES.items():
            ticker_match = bool(re.search(rf"(?<![A-Z0-9])\$?{token}(?![A-Z0-9])", text))
            lowercase_match = token.lower() in self.LOWERCASE_TICKERS and token.lower() in words
            alias_match = any(self._contains_keyword(normalized, alias) for alias in aliases)
            if ticker_match or lowercase_match or alias_match:
                tokens.append(token)

        narratives = [
            narrative
            for narrative, keywords in self.NARRATIVE_KEYWORDS.items()
            if any(self._contains_keyword(normalized, keyword) for keyword in keywords)
        ]
        for narrative in self.known_narratives:
            canonical = self._canonical_narrative(narrative)
            if self._contains_keyword(normalized, narrative) and canonical not in narratives:
                narratives.append(canonical)

        if "BTC" in tokens and (
            "ETFs" in narratives or "Bitcoin / macro" in narratives
        ) and "Bitcoin / macro" not in narratives:
            narratives.append("Bitcoin / macro")

        narratives = list(dict.fromkeys(narratives))

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

    @staticmethod
    def _contains_keyword(normalized_text: str, keyword: str) -> bool:
        pattern = rf"(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])"
        return bool(re.search(pattern, normalized_text))

    @staticmethod
    def _canonical_narrative(narrative: str) -> str:
        aliases = {
            "real world assets": "RWA",
            "ethereum scaling": "Ethereum / L2",
            "memecoins": "Memecoins",
            "solana ecosystem": "Solana ecosystem",
            "ai agents": "AI agents",
            "depin": "DePIN",
            "zero knowledge": "Privacy",
            "modular blockchains": "Infrastructure",
            "bitcoin l2": "Bitcoin / macro",
        }
        return aliases.get(narrative.strip().lower(), narrative.strip())

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
