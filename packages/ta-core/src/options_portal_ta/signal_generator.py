"""Rule-based 15m confluence signal generator."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from options_portal_domain.types import Candle, SignalAction, TechnicalSignal


class ConfluenceSignalGenerator:
    name = "confluence_v1"

    def evaluate(
        self,
        symbol: str,
        candles: Sequence[Candle],
        features: dict[str, Any],
    ) -> TechnicalSignal:
        if not candles:
            raise ValueError("candles required")

        votes: list[float] = []
        reasons: list[str] = []

        rsi = features.get("rsi")
        if rsi is not None:
            if rsi >= 55:
                votes.append(0.4)
                reasons.append(f"RSI(14) {rsi:.1f} — bullish bias")
            elif rsi <= 45:
                votes.append(-0.4)
                reasons.append(f"RSI(14) {rsi:.1f} — bearish bias")
            else:
                reasons.append(f"RSI(14) {rsi:.1f} — neutral")

        ema_cross = features.get("ema_cross")
        if ema_cross is not None:
            votes.append(0.35 * float(ema_cross))
            reasons.append(
                "EMA9 > EMA21" if ema_cross > 0 else "EMA9 < EMA21"
            )

        st_dir = features.get("supertrend_dir")
        if st_dir is not None:
            votes.append(0.25 * float(st_dir))
            reasons.append(
                "Supertrend bullish" if st_dir > 0 else "Supertrend bearish"
            )

        vwap_bias = features.get("vwap_bias")
        if vwap_bias is not None:
            votes.append(0.15 * float(vwap_bias))
            reasons.append(
                "Close above VWAP" if vwap_bias > 0 else "Close below VWAP"
            )

        score = max(-1.0, min(1.0, sum(votes))) if votes else 0.0
        confidence = min(1.0, abs(score) + 0.2 * len(votes) / 4)

        if score >= 0.35:
            action = SignalAction.BUY_CE_BIAS
        elif score <= -0.35:
            action = SignalAction.BUY_PE_BIAS
        else:
            action = SignalAction.HOLD
            reasons.append("Confluence below entry threshold → HOLD")

        bar = candles[-1]
        return TechnicalSignal(
            symbol=symbol,
            timeframe=bar.timeframe,
            bar_ts=bar.ts,
            action=action,
            score=round(score, 6),
            confidence=round(confidence, 4),
            reasons=reasons,
            features=dict(features),
        )
