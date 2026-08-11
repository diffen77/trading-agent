"""Opt-in daemon for public Nasdaq XSTO sector classifications."""

from collections.abc import Callable, Mapping
import logging
import os
import sys
import time

from src.data.database import Database
from src.data.market_data import MarketDataError
from src.data.nasdaq_sector import NasdaqSectorClient, NasdaqSectorSyncService


logger = logging.getLogger(__name__)


def build_sector_sync_service(
    *,
    database,
    environ: Mapping[str, str] | None = None,
    client: NasdaqSectorClient | None = None,
    clock=None,
) -> NasdaqSectorSyncService:
    environ = os.environ if environ is None else environ
    if environ.get("ENABLE_NASDAQ_SECTOR_SYNC", "").lower() != "true":
        raise MarketDataError(
            "Nasdaq sector sync is disabled; set "
            "ENABLE_NASDAQ_SECTOR_SYNC=true explicitly"
        )
    try:
        minimum_coverage = float(
            environ.get("NASDAQ_SECTOR_MINIMUM_COVERAGE", "0.95")
        )
    except ValueError as exc:
        raise MarketDataError(
            "NASDAQ_SECTOR_MINIMUM_COVERAGE must be a decimal"
        ) from exc
    if not 0.90 <= minimum_coverage <= 1:
        raise MarketDataError(
            "minimum sector coverage must be between 0.90 and 1"
        )
    return NasdaqSectorSyncService(
        database=database,
        client=client,
        clock=clock,
        minimum_coverage=minimum_coverage,
    )


def run_daemon(
    *,
    service: NasdaqSectorSyncService,
    interval_seconds: int,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    if not 21600 <= interval_seconds <= 604800:
        raise MarketDataError(
            "SECTOR_SYNC_INTERVAL_SECONDS must be between 21600 and 604800"
        )
    while True:
        try:
            result = service.sync()
            logger.info(
                "sector_sync_complete active=%s matched=%s updated=%s "
                "coverage=%.4f verified_at=%s",
                result.active_instruments,
                result.matched_instruments,
                result.updated_companies,
                result.coverage,
                result.verified_at.isoformat(),
            )
        except KeyboardInterrupt:
            logger.info("sector_sync_shutdown")
            return
        except Exception:
            logger.exception("sector_sync_failed")
        sleeper(interval_seconds)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        service = build_sector_sync_service(database=Database())
    except (MarketDataError, RuntimeError) as exc:
        logger.error("sector_sync_configuration_failed error=%s", exc)
        raise SystemExit(1) from None

    mode = sys.argv[1] if len(sys.argv) > 1 else "daemon"
    if mode == "once":
        try:
            result = service.sync()
        except (MarketDataError, RuntimeError) as exc:
            logger.error("sector_sync_failed error=%s", exc)
            raise SystemExit(1) from None
        logger.info("sector_sync_once_complete result=%s", result)
        return
    if mode != "daemon":
        raise SystemExit("Usage: python -m src.sector_sync [once|daemon]")

    try:
        interval = int(os.getenv("SECTOR_SYNC_INTERVAL_SECONDS", "86400"))
        run_daemon(service=service, interval_seconds=interval)
    except (MarketDataError, ValueError) as exc:
        logger.error("sector_sync_configuration_failed error=%s", exc)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
