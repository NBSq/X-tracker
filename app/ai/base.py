from __future__ import annotations

from typing import Protocol

from app.ai.models import SignalAnalysisContext, SignalAnalysisResult


class SignalAnalyzer(Protocol):
    provider: str
    model: str

    def analyze_signal(
        self,
        context: SignalAnalysisContext,
    ) -> SignalAnalysisResult: ...


class SignalAnalysisUnavailable(RuntimeError):
    pass


class SignalProviderError(RuntimeError):
    def __init__(self, message: str, error_type: str, *, transient: bool) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.transient = transient
