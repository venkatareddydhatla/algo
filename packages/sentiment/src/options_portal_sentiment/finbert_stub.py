"""FinBERT scorer stub — plugin hook only; not required to run in v1.

When `sentiment.finbert.enabled=true`, wire this to a local/remote inference
sidecar via FINBERT_URL. Until then, callers should use VaderScorer.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from options_portal_domain.types import NewsItem, SentimentResult


class FinBertScorer:
    """Optional FinBERT client. Raises if enabled but unreachable unless fallback allowed."""

    model = "finbert"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout_ms: int = 1500,
        enabled: bool = False,
    ) -> None:
        self.base_url = base_url or os.environ.get("FINBERT_URL", "http://127.0.0.1:8081")
        self.timeout_ms = timeout_ms
        self.enabled = enabled

    async def score_batch(self, items: Sequence[NewsItem]) -> Sequence[SentimentResult]:
        if not self.enabled:
            # Explicit no-op path — orchestrator should skip registration
            return []
        # TODO: POST batch to FinBERT sidecar; map logits → [-1, 1]
        raise NotImplementedError(
            "FinBERT sidecar not wired. Set sentiment.finbert.enabled=false "
            "or implement HTTP client against FINBERT_URL."
        )
