"""Opt-in daemon for Nasdaq delayed market-data ingestion."""

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import logging
import os
import sys
import time
from zoneinfo import ZoneInfo

from src.data.database import Database
from src.data.market_data import MarketDataError, SyncCycleResult
from src.data.nasdaq_delayed import NasdaqDelayedPostTradeProvider
from src.data.nasdaq_ingestion import NasdaqDelayedIngestionService
from src.data.nasdaq_pretrade import (
    NasdaqPublicPreTradeProvider,
    NasdaqPublicPreTradeSyncService,
)


logger = logging.getLogger(__name__)
_STOCKHOLM = ZoneInfo("Europe/Stockholm")
_EMPTY_RESULT = SyncCycleResult(0, 0, 0, 0)
_DEFAULT_CONTRACT_KEY = "nasdaq-nordic-delayed-xsto-v1"
_PROVIDER = "nasdaq-nordic"
_DATA_TYPE = "delayed-post-trade-equity"
_MIC = "XSTO"


def build_market_sync_service(
    *,
    database,
    environ: Mapping[str, str] | None = None,
    http_session=None,
    clock: Callable[[], datetime] | None = None,
) -> NasdaqDelayedIngestionService | NasdaqPublicPreTradeSyncService:
    """Build only when ingestion is explicitly enabled and scoped."""
    environ = os.environ if environ is None else environ
    if environ.get("ENABLE_NASDAQ_PUBLIC_PRETRADE", "").lower() == "true":
        max_files = _bounded_integer(
            environ.get("NASDAQ_PRETRADE_MAX_FILES_PER_RUN", "10"),
            field="NASDAQ_PRETRADE_MAX_FILES_PER_RUN",
            minimum=1,
            maximum=10,
        )
        provider_options = {
            "session": http_session,
            "clock": clock,
        }
        temporary_directory = environ.get(
            "NASDAQ_PRETRADE_TEMP_DIRECTORY",
            "",
        ).strip()
        if temporary_directory:
            provider_options["temp_directory"] = temporary_directory
        provider = NasdaqPublicPreTradeProvider(**provider_options)
        return NasdaqPublicPreTradeSyncService(
            database=database,
            provider=provider,
            contract_key=environ.get(
                "NASDAQ_PRETRADE_CONTRACT_KEY",
                "nasdaq-public-pretrade-xsto-v1",
            ),
            max_files_per_run=max_files,
        )
    if environ.get("ENABLE_NASDAQ_DELAYED_INGESTION", "").lower() != "true":
        raise MarketDataError(
            "Nasdaq delayed ingestion is disabled; set "
            "ENABLE_NASDAQ_DELAYED_INGESTION=true explicitly"
        )

    instruments = database.get_active_instruments(mic="XSTO")
    if not instruments:
        raise MarketDataError(
            "Nasdaq ingestion requires an active XSTO instrument universe"
        )
    aliases = database.get_active_provider_aliases(
        provider=_PROVIDER,
        mic=_MIC,
    )
    runtime_clock = clock or (lambda: datetime.now(timezone.utc))
    contract_key = environ.get(
        "MARKET_DATA_CONTRACT_KEY",
        _DEFAULT_CONTRACT_KEY,
    )
    database.require_runtime_market_data_provider(
        contract_key=contract_key,
        provider=_PROVIDER,
        data_type=_DATA_TYPE,
        mic=_MIC,
        delivery_mode="DELAYED_15M",
        transport="PUBLIC_CSV",
        usage_scope="INTERNAL_ANALYSIS_AND_PAPER",
        now=runtime_clock(),
        expected_instruments=len(instruments),
    )
    max_files = _bounded_integer(
        environ.get("MARKET_DATA_MAX_FILES_PER_RUN", "10"),
        field="MARKET_DATA_MAX_FILES_PER_RUN",
        minimum=1,
        maximum=120,
    )
    provider = NasdaqDelayedPostTradeProvider(
        instruments=instruments,
        aliases=aliases,
        session=http_session,
        clock=runtime_clock,
        max_files=min(max_files, 10),
    )
    return NasdaqDelayedIngestionService(
        database=database,
        provider=provider,
        clock=runtime_clock,
        max_files_per_run=max_files,
        contract_key=contract_key,
    )


def run_market_sync_cycle(
    *,
    database,
    service,
    now: datetime,
) -> SyncCycleResult:
    """Poll Nasdaq only during an explicit open XSTO session."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise MarketDataError("market sync clock must be timezone-aware")
    session = database.get_market_session(
        "XSTO",
        now.astimezone(_STOCKHOLM).date(),
    )
    if session is None or not session.is_open(now):
        return _EMPTY_RESULT
    return service.run_once()


def run_daemon(
    *,
    database,
    service,
    interval_seconds: int,
    clock: Callable[[], datetime] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Run bounded polling; individual failures never bypass data gates."""
    if not 30 <= interval_seconds <= 300:
        raise MarketDataError(
            "MARKET_DATA_SYNC_INTERVAL_SECONDS must be between 30 and 300"
        )
    clock = clock or (lambda: datetime.now(timezone.utc))

    while True:
        cycle_started = monotonic()
        try:
            result = run_market_sync_cycle(
                database=database,
                service=service,
                now=clock(),
            )
            if result.processed_files:
                if hasattr(result, "updates_inserted"):
                    logger.info(
                        "Nasdaq public pre-trade processed=%s updates=%s "
                        "states=%s",
                        result.processed_files,
                        result.updates_inserted,
                        result.states_persisted,
                    )
                else:
                    logger.info(
                        "Nasdaq sync processed=%s quotes=%s "
                        "duplicates=%s gaps=%s",
                        result.processed_files,
                        result.inserted_quotes,
                        result.duplicate_files,
                        result.gaps_detected,
                    )
        except KeyboardInterrupt:
            logger.info("Market-data sync shutting down")
            return
        except Exception:
            logger.exception("Nasdaq market-data sync failed")
        cycle_seconds = max(0.0, monotonic() - cycle_started)
        sleeper(max(0.0, interval_seconds - cycle_seconds))


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    database = Database()
    try:
        service = build_market_sync_service(database=database)
    except MarketDataError as exc:
        logger.error("Market-data sync configuration failed: %s", exc)
        raise SystemExit(1) from None
    mode = sys.argv[1] if len(sys.argv) > 1 else "daemon"
    now = datetime.now(timezone.utc)

    if mode == "once":
        try:
            result = run_market_sync_cycle(
                database=database,
                service=service,
                now=now,
            )
        except MarketDataError as exc:
            logger.error("Market-data sync failed: %s", exc)
            raise SystemExit(1) from None
        logger.info("Market-data sync result: %s", result)
        return
    if mode != "daemon":
        raise SystemExit("Usage: python -m src.market_sync [once|daemon]")

    interval = _bounded_integer(
        os.getenv("MARKET_DATA_SYNC_INTERVAL_SECONDS", "60"),
        field="MARKET_DATA_SYNC_INTERVAL_SECONDS",
        minimum=30,
        maximum=300,
    )
    run_daemon(
        database=database,
        service=service,
        interval_seconds=interval,
    )


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
