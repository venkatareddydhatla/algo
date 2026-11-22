"""Worker process — pipeline ticks + RSS news ingest + daily Gmail summary."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from typing import Any

from options_portal_domain.calendar import is_nse_trading_day
from options_portal_domain.config import load_config, scan_symbols, trading_mode, watchlist_symbols
from options_portal_domain.pine_signals import latest_closed_bar_open
from options_portal_domain.scan_cadence import (
    scan_cadence_config,
    seconds_until_bar_close_scan,
)
from options_portal_domain.session_gates import (
    AtrTrailScaleOutPolicy,
    FixedPctExitPolicy,
    MorningProfitBookPolicy,
    SoftEodPolicy,
)
from options_portal_domain.tz import ensure_tzdata, now_ist
from options_portal_domain.universe import is_index_underlying, scan_batch_size
from options_portal_notify.daily_summary import (
    QUIET_SUMMARY_REASONS,
    daily_summary_config,
    maybe_send_daily_summary,
)
from app.entry_admission import EntryCycleAdmission
from app.news_ingest import news_poll_loop
from app.pipeline import run_symbol_tick

ensure_tzdata()

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("worker")

_stop = asyncio.Event()


def _handle_stop(*_args: object) -> None:
    _stop.set()


def _log_daily_summary_status(status: dict[str, Any]) -> None:
    reason = str(status.get("reason") or "")
    if status.get("sent"):
        logger.info("Daily Gmail summary sent: %s", status)
    elif reason in QUIET_SUMMARY_REASONS:
        if reason == "non_trading_day":
            logger.debug("Daily Gmail summary skipped: %s", status)
    else:
        # gmail_not_configured / smtp_failed / unexpected — surface at INFO
        logger.info("Daily Gmail summary skipped: %s", status)


async def _run_batch(symbols: list[str], cfg: dict) -> None:
    """Scan a batch of symbols with bounded concurrency.

    New equity entries are staged during the scan and admitted afterward by
    strongest |hybrid| into free slots (dynamic floor/ceiling + min |hybrid|
    when enabled; anti-churn: keep existing opens; do not rotate for tiny
    score edges). Soft flatten also runs on a dedicated open-book poll loop.
    """
    trading = cfg.get("trading", {})
    batch = scan_batch_size(trading)
    sem = asyncio.Semaphore(batch)
    admission = EntryCycleAdmission.from_config(cfg)
    from app.pipeline import clear_scan_hybrid_abs

    clear_scan_hybrid_abs()
    if admission is not None:
        from options_portal_domain.high_conviction import high_conviction_config

        hc = high_conviction_config(cfg)
        logger.info(
            "Entry selection enabled anti_churn=%s dynamic=%s floor=%d "
            "ceiling=%d min_|hybrid|=%.2f hard_cap=%d "
            "cooloff=%s/%dm morning=%s until=%s morning_min=%.2f "
            "morning_max_new=%d high_conviction=%s floors=%.2f/%.2f/%.2f "
            "late_base=%.2f@wr≥%.0f%% top_pct=%s late_after=%s "
            "proximity_until=%s proximity=%.1f×ATR rvol=%.1f× "
            "news_fail_open=%s "
            "(rank by |hybrid|; empty_slots_only; no min-trade quota)",
            admission.anti_churn,
            admission.dynamic_enabled,
            admission.floor,
            admission.ceiling,
            admission.min_hybrid_abs,
            admission.max_open_symbols,
            admission.cooloff_enabled,
            admission.cooloff_minutes,
            admission.morning_enabled,
            admission.morning_until_ist,
            admission.morning_min_hybrid_abs,
            admission.morning_max_new_entries,
            hc.get("enabled", True),
            hc.get("min_hybrid_before_12", 0.75),
            hc.get("min_hybrid_after_12", 0.75),
            hc.get("min_hybrid_after_1420", 0.85),
            hc.get("late_base_min_hybrid", 0.75),
            float(hc.get("late_base_wr_threshold", 0.55)) * 100.0,
            (
                f"{float(hc.get('top_pct_of_scan', 0.10)):.0%}"
                if hc.get("top_pct_enabled", True)
                else "off"
            ),
            hc.get("late_after_ist", "14:20"),
            hc.get("proximity_skip_until_ist", "10:30"),
            float(hc.get("proximity_atr_mult", 0.1)),
            float(hc.get("rvol_mult", 1.0)),
            bool(hc.get("news_fail_open", True)),
        )

    async def _one(symbol: str) -> None:
        async with sem:
            try:
                result = await run_symbol_tick(
                    symbol, cfg, entry_admission=admission
                )
                logger.info("tick %s -> %s", symbol, result.get("hybrid_action"))
            except Exception:  # noqa: BLE001
                logger.exception("tick failed for %s", symbol)

    await asyncio.gather(*[_one(s) for s in symbols])
    if admission is not None:
        try:
            summary = await admission.flush(cfg)
            logger.info("Entry selection flush: %s", summary)
        except Exception:  # noqa: BLE001
            logger.exception("Entry selection flush failed")


async def _tick_open_book_management(cfg: dict[str, Any]) -> dict[str, Any]:
    """Evaluate open-book exits on a timely path (not blocked by full scans).

    Runs when any of:
    - fixed ±2% exits enabled, stock opens exist, and cash risk-exit window
      (09:15–15:40 IST trading days) — not overnight
    - ATR trail / profit scale-out enabled (when re-enabled) + same window
    - soft-flatten window (15:20–15:40 IST)
    - morning profit-book window (09:15–10:00 IST, when enabled)
    - open book contains index underlyings (soft-close ``index_not_equity``)

    Outside the risk-exit window the loop may idle; full-scan / API mark
    refresh can continue without atr/profit exit fills or Gmail.
    """
    soft_policy = SoftEodPolicy.from_config(cfg)
    morning_policy = MorningProfitBookPolicy.from_config(cfg)
    atr_trail_policy = AtrTrailScaleOutPolicy.from_config(cfg)
    fixed_pct_policy = FixedPctExitPolicy.from_config(cfg)
    ist_now = now_ist()
    if not is_nse_trading_day(ist_now.date()):
        return {"ran": False, "reason": "non_trading_day", "symbols": 0}

    mode = trading_mode(cfg)
    safe_mode = "dry_run" if mode == "live" else mode
    symbols: list[str] = []
    try:
        from options_portal_persistence.paper_trades import PaperTradeStore

        paper = PaperTradeStore()
        if paper.available:
            symbols = [str(s).upper() for s in paper.open_symbols(mode=safe_mode)]  # type: ignore[arg-type]
    except Exception:  # noqa: BLE001
        logger.exception("open-book management: paper ledger unavailable")
        return {"ran": False, "reason": "paper_unavailable", "symbols": 0}

    if not symbols:
        return {"ran": True, "reason": "no_open_positions", "symbols": 0}

    index_opens = [s for s in symbols if is_index_underlying(s)]
    stock_opens = [s for s in symbols if s not in set(index_opens)]
    in_soft = soft_policy.in_soft_flatten_window(ist_now)
    in_morning = morning_policy.enabled and morning_policy.in_window(ist_now)
    in_risk = soft_policy.in_risk_exit_window(ist_now)
    # Fixed-pct / ATR exits only during [09:15, 15:40] — not overnight.
    fixed_pct_active = (
        fixed_pct_policy.enabled and bool(stock_opens) and in_risk
    )
    atr_trail_active = (
        atr_trail_policy.enabled and bool(stock_opens) and in_risk
    )
    if (
        not in_soft
        and not in_morning
        and not index_opens
        and not fixed_pct_active
        and not atr_trail_active
    ):
        return {
            "ran": False,
            "reason": "outside_open_book_windows",
            "symbols": 0,
            "soft_window": f"{soft_policy.soft_flatten_ist}-{soft_policy.soft_flatten_until_ist}",
            "morning_window": f"{morning_policy.start_ist}-{morning_policy.until_ist}",
            "risk_exit_window": (
                f"{soft_policy.open_ist}-{soft_policy.soft_flatten_until_ist}"
            ),
        }

    # Modest concurrency — open book is small (≤ max_open_symbols).
    trading = cfg.get("trading", {}) or {}
    batch = min(scan_batch_size(trading), max(1, len(symbols)))
    sem = asyncio.Semaphore(batch)

    async def _one(symbol: str) -> None:
        async with sem:
            try:
                # No entry admission — exits / fixed pct / soft flatten.
                result = await run_symbol_tick(symbol, cfg, entry_admission=None)
                soft = result.get("soft_eod") or {}
                morning = result.get("morning_tp") or {}
                atr = result.get("atr_trail") or {}
                fixed = result.get("fixed_pct_exit") or {}
                profit_candle = result.get("profit_candle_protect") or {}
                logger.info(
                    "open-book tick %s -> hybrid=%s fixed_pct=%s profit_candle=%s "
                    "atr=%s soft=%s morning=%s note=%s",
                    symbol,
                    result.get("hybrid_action"),
                    fixed.get("action"),
                    profit_candle.get("action"),
                    atr.get("action"),
                    soft.get("action"),
                    morning.get("action"),
                    result.get("note")
                    or fixed.get("reason")
                    or profit_candle.get("reason")
                    or atr.get("reason")
                    or soft.get("reason")
                    or morning.get("reason"),
                )
            except Exception:  # noqa: BLE001
                logger.exception("open-book tick failed for %s", symbol)

    logger.info(
        "Open-book pass starting symbols=%d fixed_pct=%s atr_trail=%s "
        "soft=%s morning=%s index_opens=%s",
        len(symbols),
        fixed_pct_active,
        atr_trail_active,
        in_soft,
        in_morning,
        index_opens,
    )
    await asyncio.gather(*[_one(s) for s in symbols])
    return {
        "ran": True,
        "reason": "ok",
        "symbols": len(symbols),
        "fixed_pct": fixed_pct_active,
        "atr_trail": atr_trail_active,
        "soft": in_soft,
        "morning": in_morning,
        "index_opens": index_opens,
    }


# Back-compat alias for tests / imports.
_tick_open_book_soft_flatten = _tick_open_book_management


async def soft_flatten_poll_loop(stop_event: asyncio.Event) -> None:
    """Poll open-book exits (fixed ±2%, soft flatten, optional ATR/morning).

    Full-universe scans can run many minutes under live MD. Soft flatten used
    to fire only when each symbol's turn arrived mid-scan, so a slow/hung cycle
    (or a symbol last seen before 15:20) could exit hours late. This loop ticks
    the open book on its own cadence for fixed ±2% exits (cash risk-exit
    window 09:15–15:40 IST), soft-flatten (15:20–15:40), and any open index
    underlyings that must leave the equity paper book. ATR trail / morning TP
    remain callable when re-enabled in config. Overnight: no risk-exit fills.
    """
    cfg = load_config()
    soft_policy = SoftEodPolicy.from_config(cfg)
    morning_policy = MorningProfitBookPolicy.from_config(cfg)
    atr_trail_policy = AtrTrailScaleOutPolicy.from_config(cfg)
    fixed_pct_policy = FixedPctExitPolicy.from_config(cfg)
    poll_sec = soft_policy.soft_flatten_poll_interval_sec
    logger.info(
        "Open-book management loop starting poll=%ss fixed_pct=%s atr_trail=%s "
        "morning=%s-%s soft=%s-%s IST (independent of full-universe scan)",
        poll_sec,
        fixed_pct_policy.enabled,
        atr_trail_policy.enabled,
        morning_policy.start_ist,
        morning_policy.until_ist,
        soft_policy.soft_flatten_ist,
        soft_policy.soft_flatten_until_ist,
    )
    # Immediate check so a restart inside a window still acts promptly.
    try:
        status = await _tick_open_book_management(cfg)
        if status.get("ran") and status.get("symbols", 0) > 0:
            logger.info("Open-book pass: %s", status)
    except Exception:  # noqa: BLE001
        logger.exception("Open-book pass failed")
    while not stop_event.is_set():
        cfg = load_config()
        soft_policy = SoftEodPolicy.from_config(cfg)
        poll_sec = soft_policy.soft_flatten_poll_interval_sec
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_sec)
            break
        except TimeoutError:
            try:
                status = await _tick_open_book_management(cfg)
                if status.get("ran") and int(status.get("symbols") or 0) > 0:
                    logger.info("Open-book pass: %s", status)
            except Exception:  # noqa: BLE001
                logger.exception("Open-book pass failed")
    logger.info("Open-book management loop stopped")


async def daily_summary_poll_loop(stop_event: asyncio.Event) -> None:
    """Poll 16:00 IST daily summary on its own cadence (not after full scans).

    Full-universe scans can run many minutes (longer under live broker limits).
    Soft-flatten open-book + fill emails run on soft_flatten_poll_loop; the
    daily summary used to wait until ``_run_batch`` returned, so a long/hung
    cycle after 15:20 could miss 16:00.
    """
    cfg = load_config()
    poll_sec = daily_summary_config(cfg)["poll_interval_sec"]
    logger.info(
        "Daily summary schedule loop starting poll=%ss "
        "(16:00 IST trading days; independent of scan cycle)",
        poll_sec,
    )
    # Immediate check so a restart after 16:00 still sends the same day.
    try:
        _log_daily_summary_status(maybe_send_daily_summary(config=cfg))
    except Exception:  # noqa: BLE001
        logger.exception("Daily Gmail summary failed")
    while not stop_event.is_set():
        cfg = load_config()
        poll_sec = daily_summary_config(cfg)["poll_interval_sec"]
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_sec)
            break
        except TimeoutError:
            try:
                _log_daily_summary_status(maybe_send_daily_summary(config=cfg))
            except Exception:  # noqa: BLE001
                logger.exception("Daily Gmail summary failed")
    logger.info("Daily summary schedule loop stopped")


async def pipeline_loop() -> None:
    """Full-watchlist Pine Action + entry/exit passes.

    Default cadence: **15m bar-close** — after each completed candle (+ settle),
    re-evaluate **all** ``scan_symbols`` (volatile_fno50 when uncapped). Open-book
    ±2% / profit-candle exits still run on the dedicated soft-flatten poll.
    Re-entry cooloff (120m) is unchanged inside ``run_symbol_tick``.
    """
    cfg = load_config()
    watchlist = watchlist_symbols(cfg)
    active = scan_symbols(cfg)
    trading = cfg.get("trading", {})
    cadence = scan_cadence_config(cfg)
    batch = trading.get("scan_batch_size_resolved") or scan_batch_size(trading)
    max_active = trading.get("max_active_symbols", 0)
    logger.info(
        "Worker starting watchlist=%d active_scan=%d max_active_symbols=%s "
        "batch=%s mode=%s scan_on_bar_close=%s tf=%dm settle=%ss "
        "fallback_interval=%ss "
        "(0/null max_active_symbols = full list each pass; "
        "bar-close preferred so every 15m candle rescored)",
        len(watchlist),
        len(active),
        max_active,
        batch,
        cfg.get("trading", {}).get("mode", "dry_run"),
        cadence["on_bar_close"],
        cadence["timeframe_minutes"],
        cadence["settle_sec"],
        cadence["fallback_interval_sec"],
    )
    logger.info(
        "Active scan symbols: %s",
        ",".join(active[:40]) + ("..." if len(active) > 40 else ""),
    )
    last_closed_scanned = None
    while not _stop.is_set():
        cfg = load_config()
        active = scan_symbols(cfg)
        cadence = scan_cadence_config(cfg)
        tf = int(cadence["timeframe_minutes"])
        settle = float(cadence["settle_sec"])

        if cadence["on_bar_close"]:
            wait_s = seconds_until_bar_close_scan(
                timeframe_minutes=tf,
                settle_sec=settle,
                last_closed_scanned=last_closed_scanned,
            )
            if wait_s > 0:
                logger.info(
                    "Bar-close scan waiting %.1fs (tf=%dm settle=%ss "
                    "last_closed=%s symbols=%d)",
                    wait_s,
                    tf,
                    settle,
                    last_closed_scanned.isoformat()
                    if last_closed_scanned is not None
                    else "none",
                    len(active),
                )
                try:
                    await asyncio.wait_for(_stop.wait(), timeout=wait_s)
                    break
                except TimeoutError:
                    pass
                if _stop.is_set():
                    break

        cycle_started = time.monotonic()
        closed_open = latest_closed_bar_open(timeframe_minutes=tf)
        logger.info(
            "Full scan starting symbols=%d closed_bar_open=%s "
            "(Pine Action + entries/exits; cooloff unchanged)",
            len(active),
            closed_open.isoformat(),
        )
        await _run_batch(active, cfg)
        elapsed = time.monotonic() - cycle_started
        last_closed_scanned = closed_open
        logger.info(
            "Full scan cycle done symbols=%d elapsed=%.1fs (batch=%s "
            "closed_bar_open=%s)",
            len(active),
            elapsed,
            scan_batch_size(cfg.get("trading", {})),
            closed_open.isoformat(),
        )
        if elapsed > tf * 60:
            logger.warning(
                "Full scan took %.1fs (> %dm bar); next pass starts as soon "
                "as the next closed bar is ready so the list still completes "
                "each candle when possible",
                elapsed,
                tf,
            )
        # Soft flatten + daily summary run on dedicated loops (not gated on scan).
        if not cadence["on_bar_close"]:
            interval = int(cadence["fallback_interval_sec"])
            try:
                await asyncio.wait_for(_stop.wait(), timeout=interval)
                break
            except TimeoutError:
                continue
    logger.info("Pipeline loop stopped")


async def loop() -> None:
    """Run pipeline + news + soft-flatten + daily-summary pollers until stop."""
    news_task = asyncio.create_task(news_poll_loop(_stop), name="news-poll")
    soft_flatten_task = asyncio.create_task(
        soft_flatten_poll_loop(_stop), name="soft-flatten"
    )
    summary_task = asyncio.create_task(
        daily_summary_poll_loop(_stop), name="daily-summary"
    )
    try:
        await pipeline_loop()
    finally:
        _stop.set()
        for task in (news_task, soft_flatten_task, summary_task):
            try:
                await asyncio.wait_for(task, timeout=5)
            except (TimeoutError, asyncio.CancelledError):
                task.cancel()


def main() -> None:
    loop_obj = asyncio.new_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop_obj.add_signal_handler(sig, _handle_stop)
        except NotImplementedError:
            pass
    loop_obj.run_until_complete(loop())


if __name__ == "__main__":
    main()
