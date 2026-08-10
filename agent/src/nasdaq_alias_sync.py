"""Opt-in daemon for entitlement-delivered Nasdaq XSTO aliases."""

from collections.abc import Callable, Mapping
from datetime import datetime, time as local_time, timezone
import logging
import os
import sys
import time
from zoneinfo import ZoneInfo

from src.data.database import Database
from src.data.market_data import MarketDataError
from src.data.nasdaq_ndl import (
    NasdaqNdlSftpClient,
    NasdaqNdlSftpConfig,
    nasdaq_reference_source_path,
)
from src.data.nasdaq_reference import validate_nasdaq_tip_version
from src.data.reference_sync import NasdaqReferenceSyncService


logger = logging.getLogger(__name__)
_STOCKHOLM = ZoneInfo("Europe/Stockholm")
_EARLIEST_DAILY_DOWNLOAD = local_time(6, 45)


class NasdaqAliasSyncService:
    """Select the latest official XSTO session, download, and import it."""

    def __init__(
        self,
        *,
        database,
        ndl_client,
        reference_service,
        contract_key: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._ndl_client = ndl_client
        self._reference_service = reference_service
        self._contract_key = contract_key
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def sync_latest(self):
        now = self._clock()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise MarketDataError(
                "Nasdaq alias sync clock must be timezone-aware"
            )
        stockholm_now = now.astimezone(_STOCKHOLM)
        if stockholm_now.time().replace(tzinfo=None) < (
            _EARLIEST_DAILY_DOWNLOAD
        ):
            raise MarketDataError(
                "Nasdaq reference file is not expected before 06:45 "
                "Europe/Stockholm"
            )
        session_date = self._database.get_latest_market_session_date(
            mic="XSTO",
            on_or_before=stockholm_now.date(),
        )
        if session_date is None:
            raise MarketDataError(
                "Nasdaq alias sync requires an official XSTO calendar"
            )
        entitlement = (
            self._database.require_runtime_reference_entitlement(
                contract_key=self._contract_key,
                provider="nasdaq-nordic",
                mic="XSTO",
                transport="SFTP",
                usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
                now=now,
            )
        )
        source_path = nasdaq_reference_source_path(session_date)
        content = self._ndl_client.download(
            source_path,
            expected_host_key_algorithm=(
                entitlement.host_key_algorithm
            ),
            expected_host_key_fingerprint_sha256=(
                entitlement.host_key_fingerprint_sha256
            ),
        )
        return self._reference_service.sync_file(
            content=content,
            source_path=source_path,
            entitlement_id=entitlement.entitlement_id,
        )


def build_nasdaq_alias_sync_service(
    *,
    database,
    environ: Mapping[str, str] | None = None,
    ssh_client_factory=None,
    clock: Callable[[], datetime] | None = None,
) -> NasdaqAliasSyncService:
    environ = os.environ if environ is None else environ
    if environ.get("ENABLE_NASDAQ_REFERENCE_SYNC", "").lower() != "true":
        raise MarketDataError(
            "Nasdaq reference sync is disabled; set "
            "ENABLE_NASDAQ_REFERENCE_SYNC=true explicitly"
        )
    runtime_clock = clock or (lambda: datetime.now(timezone.utc))
    config = NasdaqNdlSftpConfig.from_environ(environ)
    contract_key = environ.get(
        "NASDAQ_REFERENCE_CONTRACT_KEY",
        "",
    )
    if not contract_key:
        raise MarketDataError(
            "NASDAQ_REFERENCE_CONTRACT_KEY is required"
        )
    tip_version = validate_nasdaq_tip_version(
        environ.get(
            "NASDAQ_NDL_TIP_VERSION",
            "3.10.17.1",
        )
    )
    ndl_client = NasdaqNdlSftpClient(
        config=config,
        ssh_client_factory=ssh_client_factory,
    )
    reference_service = NasdaqReferenceSyncService(
        database=database,
        clock=runtime_clock,
        tip_version=tip_version,
    )
    return NasdaqAliasSyncService(
        database=database,
        ndl_client=ndl_client,
        reference_service=reference_service,
        contract_key=contract_key,
        clock=runtime_clock,
    )


def run_daemon(
    *,
    service: NasdaqAliasSyncService,
    interval_seconds: int,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    if not 3600 <= interval_seconds <= 86400:
        raise MarketDataError(
            "NASDAQ_REFERENCE_SYNC_INTERVAL_SECONDS must be between "
            "3600 and 86400"
        )
    while True:
        try:
            result = service.sync_latest()
            logger.info(
                "Nasdaq alias snapshot inserted=%s aliases=%s "
                "snapshot_id=%s",
                result.inserted,
                result.aliases_inserted,
                result.snapshot_id,
            )
        except KeyboardInterrupt:
            logger.info("Nasdaq alias sync shutting down")
            return
        except MarketDataError as exc:
            logger.error("Nasdaq alias sync failed: %s", exc)
        except Exception:
            logger.error(
                "Nasdaq alias sync failed due to an unexpected error"
            )
        sleeper(interval_seconds)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        database = Database()
        service = build_nasdaq_alias_sync_service(database=database)
    except (MarketDataError, RuntimeError) as exc:
        logger.error("Nasdaq alias sync configuration failed: %s", exc)
        raise SystemExit(1) from None

    mode = sys.argv[1] if len(sys.argv) > 1 else "daemon"
    if mode == "once":
        try:
            result = service.sync_latest()
        except (MarketDataError, RuntimeError) as exc:
            logger.error("Nasdaq alias sync failed: %s", exc)
            raise SystemExit(1) from None
        logger.info("Nasdaq alias sync result: %s", result)
        return
    if mode != "daemon":
        raise SystemExit(
            "Usage: python -m src.nasdaq_alias_sync [once|daemon]"
        )

    try:
        interval = _bounded_integer(
            os.getenv(
                "NASDAQ_REFERENCE_SYNC_INTERVAL_SECONDS",
                "3600",
            ),
            field="NASDAQ_REFERENCE_SYNC_INTERVAL_SECONDS",
            minimum=3600,
            maximum=86400,
        )
        run_daemon(
            service=service,
            interval_seconds=interval,
        )
    except MarketDataError as exc:
        logger.error("Nasdaq alias sync configuration failed: %s", exc)
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
