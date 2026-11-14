"""Worker process — polls pipeline on an interval (skeleton)."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import time

from options_portal_domain.config import load_config, scan_symbols, watchlist_symbols
from options_portal_domain.universe import scan_batch_size
from app.pipeline import run_symbol_tick

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("worker")

_stop = asyncio.Event()


def _handle_stop(*_args: object) -> None:
    _stop.set()


async def _run_batch(symbols: list[str], cfg: dict) -> None:
    """Scan a batch of symbols with bounded concurrency."""
    trading = cfg.get("trading", {})
    batch = scan_batch_size(trading)
    sem = asyncio.Semaphore(batch)

    async def _one(symbol: str) -> None:
        async with sem:
            try:
                result = await run_symbol_tick(symbol, cfg)
                logger.info("tick %s -> %s", symbol, result.get("hybrid_action"))
            except Exception:  # noqa: BLE001
                logger.exception("tick failed for %s", symbol)

    await asyncio.gather(*[_one(s) for s in symbols])


async def loop() -> None:
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
        # Re-read caps each cycle so config edits apply without restart (when CONFIG_PATH shared)
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
        try:
            await asyncio.wait_for(_stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
    logger.info("Worker stopped")


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
