"""Replay hybrid confluence signals on historical/synthetic candles (dry-run)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from options_portal_domain.tz import ist
from options_portal_domain.types import Candle, SignalAction, TechnicalSignal
from options_portal_domain.universe import is_index_underlying
from options_portal_hybrid.scorer import WeightedHybridScorer
from options_portal_ta.indicators import (
    EMAPlugin,
    RSIPlugin,
    SupertrendPlugin,
    VWAPPlugin,
)
from options_portal_ta.signal_generator import ConfluenceSignalGenerator

from options_portal_backtest.candles import RealHistoryUnavailable, resolve_candles

Side = Literal["CE", "PE"]

SYNTHETIC_NOT_TRADEABLE = "synthetic — not tradeable"


def _to_ist(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # Treat naive bar times as UTC (candle store convention)
        from datetime import timezone

        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ist())


def format_bar_ts_ist(dt: datetime) -> tuple[str, str]:
    """Return (ISO-8601 with IST offset, display ``YYYY-MM-DD HH:MM IST``)."""
    local = _to_ist(dt)
    iso = local.isoformat()
    display = local.strftime("%Y-%m-%d %H:%M IST")
    return iso, display


@dataclass
class TradeFill:
    entry_ts: str
    exit_ts: str
    entry_at_ist: str
    exit_at_ist: str
    side: Side
    entry_price: float
    exit_price: float
    pnl: float
    bars_held: int
    entry_action: str
    exit_reason: str


@dataclass
class BacktestResult:
    symbol: str
    mode: str
    dry_run: bool
    source: str
    from_date: str | None
    to_date: str | None
    bars: int
    bars_evaluated: int
    trades: int
    wins: int
    losses: int
    win_rate: float
    pnl: float
    max_drawdown: float
    profit_factor: float
    avg_trade_pnl: float
    fills: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""
    metrics_are_synthetic: bool = False
    win_rate_label: str | None = None
    pnl_label: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _features_at(candles: list[Candle]) -> dict[str, Any]:
    feats: dict[str, Any] = {}
    for plugin in (RSIPlugin(), EMAPlugin(), SupertrendPlugin(), VWAPPlugin()):
        feats.update(plugin.compute(candles))
    return feats


def _slippage_price(price: float, *, side: Side, entering: bool, slippage_bps: float) -> float:
    # Buy premium worse on entry; sell worse on exit — approximate via spot move proxy
    bps = slippage_bps / 10_000.0
    if entering:
        return price * (1.0 + bps) if side == "CE" else price * (1.0 - bps)
    return price * (1.0 - bps) if side == "CE" else price * (1.0 + bps)


def run_backtest(
    symbol: str,
    *,
    start: date | None = None,
    end: date | None = None,
    bars: int | None = 200,
    dry_run: bool = True,
    slippage_bps: float = 5.0,
    warmup: int = 30,
    fixture_path: Path | None = None,
    max_fills: int = 50,
    allow_synthetic: bool = False,
    candles: list[Candle] | None = None,
    candle_source: str | None = None,
    min_bars: int = 40,
) -> BacktestResult:
    """Run a dry-run signal replay for one symbol.

    Position model (v1): long CE-bias when action is BUY_CE_BIAS, long PE-bias
    when BUY_PE_BIAS. Flat on HOLD / flip. PnL is close-to-close spot proxy with
    slippage — not a full options chain simulator.

    Real history is required by default (``allow_synthetic=False``). Synthetic
    bars are opt-in and labeled ``synthetic — not tradeable`` so they cannot be
    mistaken for expectancy.
    """
    if not dry_run:
        # v1 hard-lock: never leave dry_run
        dry_run = True

    sym = symbol.upper().strip()
    resolved, source = resolve_candles(
        sym,
        start=start,
        end=end,
        bars=bars,
        fixture_path=fixture_path,
        allow_synthetic=allow_synthetic,
        preloaded=candles,
        preloaded_source=candle_source,
        min_bars=min_bars,
    )
    generator = ConfluenceSignalGenerator()
    scorer = WeightedHybridScorer()

    fills: list[TradeFill] = []
    open_side: Side | None = None
    entry_idx: int | None = None
    entry_price = 0.0
    entry_action = ""
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    gross_wins = 0.0
    gross_losses = 0.0
    evaluated = 0
    index_underlying = is_index_underlying(sym)

    def _close_trade(i: int, reason: str) -> None:
        nonlocal open_side, entry_idx, entry_price, entry_action, equity, peak, max_dd
        nonlocal gross_wins, gross_losses
        if open_side is None or entry_idx is None:
            return
        exit_raw = resolved[i].close
        exit_px = _slippage_price(
            exit_raw, side=open_side, entering=False, slippage_bps=slippage_bps
        )
        if open_side == "CE":
            pnl = exit_px - entry_price
        else:
            # PE-bias profits when spot falls
            pnl = entry_price - exit_px
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        if pnl >= 0:
            gross_wins += pnl
        else:
            gross_losses += abs(pnl)
        entry_iso, entry_disp = format_bar_ts_ist(resolved[entry_idx].ts)
        exit_iso, exit_disp = format_bar_ts_ist(resolved[i].ts)
        fills.append(
            TradeFill(
                entry_ts=entry_iso,
                exit_ts=exit_iso,
                entry_at_ist=entry_disp,
                exit_at_ist=exit_disp,
                side=open_side,
                entry_price=round(entry_price, 4),
                exit_price=round(exit_px, 4),
                pnl=round(pnl, 4),
                bars_held=i - entry_idx,
                entry_action=entry_action,
                exit_reason=reason,
            )
        )
        open_side = None
        entry_idx = None
        entry_price = 0.0
        entry_action = ""

    for i in range(warmup, len(resolved)):
        window = resolved[: i + 1]
        feats = _features_at(window)
        tech: TechnicalSignal = generator.evaluate(sym, window, feats)
        decision = scorer.combine(tech, sentiments=[], news=[], context={})
        evaluated += 1
        action = decision.action

        desired: Side | None = None
        if action == SignalAction.BUY_CE_BIAS:
            desired = "CE"
        elif action == SignalAction.BUY_PE_BIAS:
            desired = "PE"

        if open_side is not None and desired != open_side:
            _close_trade(i, "signal_exit" if desired is None else "flip")

        if desired is not None and open_side is None:
            open_side = desired
            entry_idx = i
            entry_action = action.value
            entry_price = _slippage_price(
                resolved[i].close,
                side=desired,
                entering=True,
                slippage_bps=slippage_bps,
            )

    # Force flat at end
    if open_side is not None and resolved:
        _close_trade(len(resolved) - 1, "eod_flat")

    wins = sum(1 for f in fills if f.pnl > 0)
    losses = sum(1 for f in fills if f.pnl <= 0)
    n = len(fills)
    win_rate = (wins / n) if n else 0.0
    total_pnl = sum(f.pnl for f in fills)
    pf = (gross_wins / gross_losses) if gross_losses > 0 else (float("inf") if gross_wins > 0 else 0.0)
    if pf == float("inf"):
        pf = 99.0

    if resolved:
        from_s = _to_ist(resolved[0].ts).date().isoformat()
        to_s = _to_ist(resolved[-1].ts).date().isoformat()
    else:
        from_s = start.isoformat() if start else None
        to_s = end.isoformat() if end else None

    synthetic = source.startswith("synthetic")
    note_parts = [
        "Dry-run spot-proxy PnL (not full options chain).",
        "Fill times are Asia/Kolkata (IST).",
        "Uses confluence TA + hybrid scorer with empty news.",
    ]
    if index_underlying:
        note_parts.append(
            "Index underlying: signal/spot-proxy replay only — not equity stock book."
        )
    if synthetic:
        note_parts.insert(
            0,
            "SYNTHETIC — NOT TRADEABLE. Bars are generated, not Upstox history; "
            "win rate / PnL are not expectancy.",
        )

    return BacktestResult(
        symbol=sym,
        mode="dry_run",
        dry_run=True,
        source=source,
        from_date=from_s,
        to_date=to_s,
        bars=len(resolved),
        bars_evaluated=evaluated,
        trades=n,
        wins=wins,
        losses=losses,
        # Headline expectancy metrics are zeroed when synthetic so UI cannot
        # present fake win-rate/PnL as tradeable; labels carry the warning.
        win_rate=0.0 if synthetic else round(win_rate, 4),
        pnl=0.0 if synthetic else round(total_pnl, 4),
        max_drawdown=0.0 if synthetic else round(max_dd, 4),
        profit_factor=0.0 if synthetic else round(pf, 4),
        avg_trade_pnl=0.0 if synthetic else (round(total_pnl / n, 4) if n else 0.0),
        fills=[asdict(f) for f in fills[:max_fills]],
        note=" ".join(note_parts),
        metrics_are_synthetic=synthetic,
        win_rate_label=SYNTHETIC_NOT_TRADEABLE if synthetic else None,
        pnl_label=SYNTHETIC_NOT_TRADEABLE if synthetic else None,
    )


__all__ = [
    "BacktestResult",
    "RealHistoryUnavailable",
    "SYNTHETIC_NOT_TRADEABLE",
    "format_bar_ts_ist",
    "run_backtest",
]
