"""Replay hybrid confluence signals on historical/synthetic candles (dry-run)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

from options_portal_domain.types import Candle, SignalAction, TechnicalSignal
from options_portal_hybrid.scorer import WeightedHybridScorer
from options_portal_ta.indicators import (
    EMAPlugin,
    RSIPlugin,
    SupertrendPlugin,
    VWAPPlugin,
)
from options_portal_ta.signal_generator import ConfluenceSignalGenerator

from options_portal_backtest.candles import resolve_candles

Side = Literal["CE", "PE"]


@dataclass
class TradeFill:
    entry_ts: str
    exit_ts: str
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
) -> BacktestResult:
    """Run a dry-run signal replay for one symbol.

    Position model (v1): long CE-bias when action is BUY_CE_BIAS, long PE-bias
    when BUY_PE_BIAS. Flat on HOLD / flip. PnL is close-to-close spot proxy with
    slippage — not a full options chain simulator.
    """
    if not dry_run:
        # v1 hard-lock: never leave dry_run
        dry_run = True

    sym = symbol.upper().strip()
    candles, source = resolve_candles(
        sym, start=start, end=end, bars=bars, fixture_path=fixture_path
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

    def _close_trade(i: int, reason: str) -> None:
        nonlocal open_side, entry_idx, entry_price, entry_action, equity, peak, max_dd
        nonlocal gross_wins, gross_losses
        if open_side is None or entry_idx is None:
            return
        exit_raw = candles[i].close
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
        fills.append(
            TradeFill(
                entry_ts=candles[entry_idx].ts.isoformat(),
                exit_ts=candles[i].ts.isoformat(),
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

    for i in range(warmup, len(candles)):
        window = candles[: i + 1]
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
                candles[i].close,
                side=desired,
                entering=True,
                slippage_bps=slippage_bps,
            )

    # Force flat at end
    if open_side is not None and candles:
        _close_trade(len(candles) - 1, "eod_flat")

    wins = sum(1 for f in fills if f.pnl > 0)
    losses = sum(1 for f in fills if f.pnl <= 0)
    n = len(fills)
    win_rate = (wins / n) if n else 0.0
    total_pnl = sum(f.pnl for f in fills)
    pf = (gross_wins / gross_losses) if gross_losses > 0 else (float("inf") if gross_wins > 0 else 0.0)
    if pf == float("inf"):
        pf = 99.0

    from_s = candles[0].ts.date().isoformat() if candles else (start.isoformat() if start else None)
    to_s = candles[-1].ts.date().isoformat() if candles else (end.isoformat() if end else None)

    return BacktestResult(
        symbol=sym,
        mode="dry_run",
        dry_run=True,
        source=source,
        from_date=from_s,
        to_date=to_s,
        bars=len(candles),
        bars_evaluated=evaluated,
        trades=n,
        wins=wins,
        losses=losses,
        win_rate=round(win_rate, 4),
        pnl=round(total_pnl, 4),
        max_drawdown=round(max_dd, 4),
        profit_factor=round(pf, 4),
        avg_trade_pnl=round(total_pnl / n, 4) if n else 0.0,
        fills=[asdict(f) for f in fills[:max_fills]],
        note=(
            "Dry-run spot-proxy PnL (not full options chain). "
            "Uses confluence TA + hybrid scorer with empty news."
        ),
    )
