"""Load versioned Nasdaq Stockholm calendars into PostgreSQL."""

from datetime import datetime, timezone
import logging

from src.data.database import Database
from src.data.market_data import (
    CalendarSyncResult,
    MarketDataError,
)
from src.data.xsto_calendar import nasdaq_stockholm_calendar


logger = logging.getLogger(__name__)
_VERIFIED_YEARS = (2024, 2025, 2026)


def sync_verified_calendars(
    *,
    database,
    verified_at: datetime,
) -> tuple[CalendarSyncResult, ...]:
    if (
        not isinstance(verified_at, datetime)
        or verified_at.tzinfo is None
        or verified_at.utcoffset() is None
    ):
        raise MarketDataError("verified_at must be timezone-aware")
    return tuple(
        database.sync_market_calendar(
            nasdaq_stockholm_calendar(
                year,
                verified_at=verified_at,
            )
        )
        for year in _VERIFIED_YEARS
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    try:
        results = sync_verified_calendars(
            database=Database(),
            verified_at=datetime.now(timezone.utc),
        )
    except (MarketDataError, RuntimeError) as exc:
        logger.error("Calendar sync failed: %s", exc)
        raise SystemExit(1) from None
    logger.info(
        "Calendar sync years=%s inserted=%s sessions=%s",
        len(results),
        sum(result.inserted for result in results),
        sum(result.sessions_upserted for result in results),
    )


if __name__ == "__main__":
    main()
