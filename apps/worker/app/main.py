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
from options_portal_domain.session_gates import (
    AtrTrailScaleOutPolicy,
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
    if admission is not None:
        logger.info(
            "Entry selection enabled anti_churn=%s dynamic=%s floor=%d "
            "ceiling=%d min_|hybrid|=%.2f hard_cap=%d "
            "cooloff=%s/%dm afternoon_after=%s afternoon_min=%.2f "
            "(rank by |hybrid|; empty_slots_only)",
            admission.anti_churn,
            admission.dynamic_enabled,
            admission.floor,
            admission.ceiling,
            admission.min_hybrid_abs,
            admission.max_open_symbols,
            admission.cooloff_enabled,
            admission.cooloff_minutes,
            admission.afternoon_after_ist or "off",
            float(admission.afternoon_min_hybrid_abs or admission.min_hybrid_abs),
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
    - ATR trail / profit scale-out enabled, stock opens exist, and cash
      risk-exit window (09:15–15:40 IST trading days) — not overnight
    - soft-flatten window (15:20–15:40 IST)
    - morning profit-book window (09:15–10:00 IST)
    - open book contains index underlyings (soft-close ``index_not_equity``)

    Outside the risk-exit window the loop may idle; full-scan / API mark
    refresh can continue without atr/profit exit fills or Gmail.
    """
    soft_policy = SoftEodPolicy.from_config(cfg)
    morning_policy = MorningProfitBookPolicy.from_config(cfg)
    atr_trail_policy = AtrTrailScaleOutPolicy.from_config(cfg)
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
    # ATR/profit exits only during [09:15, 15:40] — not overnight (TATACONSUM 00:05).
    atr_trail_active = (
        atr_trail_policy.enabled and bool(stock_opens) and in_risk
    )
    if not in_soft and not in_morning and not index_opens and not atr_trail_active:
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
                # No entry admission — exits / ATR trail / soft flatten / morning TP.
                result = await run_symbol_tick(symbol, cfg, entry_admission=None)
                soft = result.get("soft_eod") or {}
                morning = result.get("morning_tp") or {}
                atr = result.get("atr_trail") or {}
                logger.info(
                    "open-book tick %s -> hybrid=%s atr=%s soft=%s morning=%s note=%s",
                    symbol,
                    result.get("hybrid_action"),
                    atr.get("action"),
                    soft.get("action"),
                    morning.get("action"),
                    result.get("note")
                    or atr.get("reason")
                    or soft.get("reason")
                    or morning.get("reason"),
                )
            except Exception:  # noqa: BLE001
                logger.exception("open-book tick failed for %s", symbol)

    logger.info(
        "Open-book pass starting symbols=%d atr_trail=%s soft=%s morning=%s index_opens=%s",
        len(symbols),
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
        "atr_trail": atr_trail_active,
        "soft": in_soft,
        "morning": in_morning,
        "index_opens": index_opens,
    }


# Back-compat alias for tests / imports.
_tick_open_book_soft_flatten = _tick_open_book_management


async def soft_flatten_poll_loop(stop_event: asyncio.Event) -> None:
    """Poll open-book exits (ATR trail, soft flatten, morning TP, index soft-close).

    Full-universe scans can run many minutes under live MD. Soft flatten used
    to fire only when each symbol's turn arrived mid-scan, so a slow/hung cycle
    (or a symbol last seen before 15:20) could exit hours late. This loop ticks
    the open book on its own cadence for ATR trail/scale-out (cash risk-exit
    window 09:15–15:40 IST), soft-flatten (15:20–15:40), morning profit-book
    (09:15–10:00), and any open index underlyings that must leave the equity
    paper book. Overnight: no atr/profit exit fills.
    """
    cfg = load_config()
    soft_policy = SoftEodPolicy.from_config(cfg)
    morning_policy = MorningProfitBookPolicy.from_config(cfg)
    atr_trail_policy = AtrTrailScaleOutPolicy.from_config(cfg)
    poll_sec = soft_policy.soft_flatten_poll_interval_sec
    logger.info(
        "Open-book management loop starting poll=%ss atr_trail=%s "
        "morning=%s-%s soft=%s-%s IST (independent of full-universe scan)",
        poll_sec,
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
    cfg = load_config()
    watchlist = watchlist_symbols(cfg)
    active = scan_symbols(cfg)
    trading = cfg.get("trading", {})
    interval = int(os.environ.get("WORKER_INTERVAL_SEC", "60"))
    batch = trading.get("scan_batch_size_resolved") or scan_batch_size(trading)
    max_active = trading.get("max_active_symbols", 0)
    logger.info(
        "Worker starting watchlist=%d active_scan=%d max_active_symbols=%s "
        "batch=%s interval=%ss mode=%s "
        "(0/null max_active_symbols = full universe; expect ~2-5 min/pass dry-run)",
        len(watchlist),
        len(active),
        max_active,
        batch,
        interval,
        cfg.get("trading", {}).get("mode", "dry_run"),
    )
    logger.info(
        "Active scan symbols: %s",
        ",".join(active[:40]) + ("..." if len(active) > 40 else ""),
    )
    while not _stop.is_set():
        cfg = load_config()
        active = scan_symbols(cfg)
        cycle_started = time.monotonic()
        await _run_batch(active, cfg)
        elapsed = time.monotonic() - cycle_started
        logger.info(
            "Full scan cycle done symbols=%d elapsed=%.1fs (batch=%s)",
            len(active),
            elapsed,
            scan_batch_size(cfg.get("trading", {})),
        )
        # Soft flatten + daily summary run on dedicated loops (not gated on scan).
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval)
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
