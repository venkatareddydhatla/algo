"""Worker process — pipeline ticks + RSS news ingest + daily Gmail summary."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time
from typing import Any

from options_portal_domain.config import load_config, scan_symbols, watchlist_symbols
from options_portal_domain.tz import ensure_tzdata
from options_portal_domain.universe import scan_batch_size
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
    strongest |hybrid| into free ``max_open_symbols`` slots (anti-churn:
    keep existing opens; do not rotate for tiny score edges).
    """
    trading = cfg.get("trading", {})
    batch = scan_batch_size(trading)
    sem = asyncio.Semaphore(batch)
    admission = EntryCycleAdmission.from_config(cfg)
    if admission is not None:
        logger.info(
            "Entry selection enabled anti_churn=%s max_open_symbols=%d "
            "(rank new entries by |hybrid|; fill empty slots only)",
            admission.anti_churn,
            admission.max_open_symbols,
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


async def daily_summary_poll_loop(stop_event: asyncio.Event) -> None:
    """Poll 16:00 IST daily summary on its own cadence (not after full scans).

    Full-universe scans can run many minutes (longer under live broker limits).
    Soft-flatten fill emails fire mid-scan; the daily summary used to wait until
    ``_run_batch`` returned, so a long/hung cycle after 15:20 could miss 16:00.
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
        # Daily Gmail summary runs in daily_summary_poll_loop (not gated on scan).
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval)
        except TimeoutError:
            continue
    logger.info("Pipeline loop stopped")


async def loop() -> None:
    """Run pipeline + news + daily-summary pollers concurrently until stop."""
    news_task = asyncio.create_task(news_poll_loop(_stop), name="news-poll")
    summary_task = asyncio.create_task(
        daily_summary_poll_loop(_stop), name="daily-summary"
    )
    try:
        await pipeline_loop()
    finally:
        _stop.set()
        for task in (news_task, summary_task):
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
