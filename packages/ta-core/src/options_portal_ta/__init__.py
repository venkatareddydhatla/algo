"""Technical analysis indicators and default signal generator."""

from options_portal_ta.indicators import (
    EMAPlugin,
    RSIPlugin,
    SupertrendPlugin,
    VWAPPlugin,
)
from options_portal_ta.registry import IndicatorRegistry
from options_portal_ta.signal_generator import ConfluenceSignalGenerator

__all__ = [
    "ConfluenceSignalGenerator",
    "EMAPlugin",
    "IndicatorRegistry",
    "RSIPlugin",
    "SupertrendPlugin",
    "VWAPPlugin",
]
