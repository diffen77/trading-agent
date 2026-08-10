"""Container healthcheck for the official XSTO reference universe."""

from datetime import datetime, timezone

from src.data.database import Database


def reference_universe_is_fresh(
    database,
    *,
    now: datetime,
    maximum_age_days: int = 10,
) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        return False
    if not isinstance(maximum_age_days, int) or not 1 <= maximum_age_days <= 31:
        return False
    snapshot_date = database.get_latest_reference_snapshot_date(
        provider="esma-firds",
        mic="XSTO",
    )
    if snapshot_date is None:
        return False
    age_days = (now.date() - snapshot_date).days
    return 0 <= age_days <= maximum_age_days


def main() -> None:
    try:
        healthy = reference_universe_is_fresh(
            Database(),
            now=datetime.now(timezone.utc),
        )
    except Exception:
        healthy = False
    raise SystemExit(0 if healthy else 1)


if __name__ == "__main__":
    main()
