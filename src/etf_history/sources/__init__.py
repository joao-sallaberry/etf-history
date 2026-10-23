"""Where each fund's holdings come from."""

from types import ModuleType

from etf_history.sources import avantis, dimensional, nport, wayback

# dimensional: one CSV per day, whole history downloadable. avantis: latest day only.
BY_FUND: dict[str, ModuleType] = {"DFEV": dimensional, "AVES": avantis}


def for_fund(fund: str) -> ModuleType:
    """Source module of a fund; any fund not listed is assumed to be a Dimensional ETF."""
    return BY_FUND.get(fund, dimensional)


# Extra sources for funds whose own source has no history (each module: SOURCE, backfill, reload_raw).
HISTORY: dict[str, tuple[ModuleType, ...]] = {"AVES": (wayback, nport)}


def reloaders(fund: str) -> list[ModuleType]:
    """Every module whose raw files hold this fund's snapshots."""
    return [for_fund(fund), *HISTORY.get(fund, ())]
