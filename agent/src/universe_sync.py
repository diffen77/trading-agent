"""Opt-in daemon for the official ESMA FIRDS XSTO universe."""

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import logging
import os
import sys
import time

from src.data.database import Database
from src.data.market_data import MarketDataError
from src.data.reference_sync import EsmaReferenceSyncService


logger = logging.getLogger(__name__)


def build_universe_sync_service(
    *,
    database,
    environ: Mapping[str, str] | None = None,
    client=None,
    clock: Callable[[], datetime] | None = None,
) -> EsmaReferenceSyncService:
    environ = os.environ if environ is None else environ
    if environ.get("ENABLE_ESMA_REFERENCE_SYNC", "").lower() != "true":
        raise MarketDataError(
            "ESMA reference sync is disabled; set "
            "ENABLE_ESMA_REFERENCE_SYNC=true explicitly"
        )
    minimum = _bounded_integer(
        environ.get("XSTO_MINIMUM_INSTRUMENTS", "300"),
        field="XSTO_MINIMUM_INSTRUMENTS",
        minimum=250,
        maximum=750,
    )
    maximum = _bounded_integer(
        environ.get("XSTO_MAXIMUM_INSTRUMENTS", "1000"),
        field="XSTO_MAXIMUM_INSTRUMENTS",
        minimum=minimum,
        maximum=2000,
    )
    return EsmaReferenceSyncService(
        database=database,
        client=client,
        clock=clock,
        minimum_instruments=minimum,
        maximum_instruments=maximum,
    )


def run_daemon(
    *,
    service: EsmaReferenceSyncService,
    interval_seconds: int,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    if not 3600 <= interval_seconds <= 86400:
        raise MarketDataError(
            "REFERENCE_SYNC_INTERVAL_SECONDS must be between 3600 and 86400"
        )
    while True:
        try:
            result = service.sync()
            logger.info(
                "XSTO universe snapshot=%s inserted=%s "
                "files=%s instruments=%s deactivated=%s",
                result.snapshot_date,
                result.inserted,
                result.downloaded_files,
                result.instrument_count,
                result.instruments_deactivated,
            )
        except KeyboardInterrupt:
            logger.info("Reference-data sync shutting down")
            return
        except Exception:
            logger.exception("ESMA reference-data sync failed")
        sleeper(interval_seconds)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        database = Database()
        service = build_universe_sync_service(database=database)
    except (MarketDataError, RuntimeError) as exc:
        logger.error("Reference-data sync configuration failed: %s", exc)
        raise SystemExit(1) from None

    mode = sys.argv[1] if len(sys.argv) > 1 else "daemon"
    if mode == "once":
        try:
            result = service.sync()
        except (MarketDataError, RuntimeError) as exc:
            logger.error("Reference-data sync failed: %s", exc)
            raise SystemExit(1) from None
        logger.info("Reference-data sync result: %s", result)
        return
    if mode != "daemon":
        raise SystemExit("Usage: python -m src.universe_sync [once|daemon]")

    try:
        interval = _bounded_integer(
            os.getenv("REFERENCE_SYNC_INTERVAL_SECONDS", "21600"),
            field="REFERENCE_SYNC_INTERVAL_SECONDS",
            minimum=3600,
            maximum=86400,
        )
        run_daemon(
            service=service,
            interval_seconds=interval,
        )
    except MarketDataError as exc:
        logger.error("Reference-data sync configuration failed: %s", exc)
        raise SystemExit(1) from None


def _bounded_integer(
    value: object,
    *,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise MarketDataError(f"{field} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise MarketDataError(
            f"{field} must be between {minimum} and {maximum}"
        )
    return parsed


if __name__ == "__main__":
    main()
