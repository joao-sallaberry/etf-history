from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

# Dimensional publishes after the US close; "today" is anchored to Brasília so it does not depend on the host.
MARKET_TZ = ZoneInfo("America/Sao_Paulo")


def today() -> date:
    return datetime.now(MARKET_TZ).date()


def now() -> datetime:
    return datetime.now(UTC)


def weekdays_backwards(until: date, since: date) -> Iterator[date]:
    """Mon-Fri dates from `until` down to `since`, both inclusive."""
    day = until
    while day >= since:
        if day.weekday() < 5:
            yield day
        day -= timedelta(days=1)
