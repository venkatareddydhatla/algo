"""Worker process — polls pipeline on an interval (skeleton)."""

from __future__ import annotations

import asyncio
import logging
import os
import signal

from options_portal_domain.config import load_config
from app.pipeline import run_symbol_tick

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("worker")

_stop = asyncio.Event()


def _handle_stop(*_args: object) -> None:
    _stop.set()


async def loop() -> None:
    cfg = load_config()
    symbols = cfg.get("trading", {}).get("symbols") or [
        "NIFTY",
        "BANKNIFTY",
        "RELIANCE",
        "TCS",
    ]
    interval = int(os.environ.get("WORKER_INTERVAL_SEC", "60"))
    logger.info(
        "Worker starting symbols=%s interval=%ss mode=%s",
        symbols,
        interval,
        cfg.get("trading", {}).get("mode", "dry_run"),
    )
    while not _stop.is_set():
        for symbol in symbols:
            try:
                result = await run_symbol_tick(symbol, cfg)
                logger.info("tick %s -> %s", symbol, result.get("hybrid_action"))
            except Exception:  # noqa: BLE001
                logger.exception("tick failed for %s", symbol)
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
