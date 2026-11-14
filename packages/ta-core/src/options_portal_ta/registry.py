"""Simple in-process indicator registry."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from options_portal_domain.protocols import IndicatorPlugin
from options_portal_domain.types import Candle


class IndicatorRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, IndicatorPlugin] = {}

    def register(self, plugin: IndicatorPlugin) -> None:
        self._plugins[plugin.name] = plugin

    def compute_all(self, candles: Sequence[Candle]) -> dict[str, Any]:
        features: dict[str, Any] = {}
        for plugin in self._plugins.values():
            features.update(plugin.compute(candles))
        return features

    def names(self) -> list[str]:
        return list(self._plugins.keys())
