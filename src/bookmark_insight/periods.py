"""Resolve ``days`` / ``since`` / ``until`` tool arguments into a date range (§2.2).

The package does the date arithmetic so the voice LLM never has to. ``until``
is inclusive and defaults to today; ``days`` counts back from ``until``
inclusive, so ``days=7`` on 2026-11-06 covers 2026-10-31 .. 2026-11-06.
"""

from __future__ import annotations
from datetime import date, timedelta
from dataclasses import dataclass


DEFAULT_DAYS = 7
MAX_DAYS = 3660


class PeriodError(ValueError):
    """Raised for malformed or contradictory period arguments."""


@dataclass(frozen=True)
class Period:
    """Inclusive date range."""

    since: date
    until: date

    @property
    def key(self) -> str:
        """``<since>_<until>`` note stem."""
        return f"{self.since.isoformat()}_{self.until.isoformat()}"

    def contains(self, day: date) -> bool:
        """Return whether ``day`` falls inside the range."""
        return self.since <= day <= self.until

    def to_dict(self) -> dict[str, str]:
        """JSON shape used in tool results."""
        return {"since": self.since.isoformat(), "until": self.until.isoformat()}


def _parse_iso_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise PeriodError(f"{label} must be an ISO date (YYYY-MM-DD), got {value!r}") from exc


def resolve_period(
    *,
    days: int | None = None,
    since: str | None = None,
    until: str | None = None,
    today: date | None = None,
) -> Period:
    """Turn tool arguments into a concrete inclusive ``Period``.

    Rules (§2.2): ``days`` and ``since`` are exclusive; ``until`` alone pairs with
    the default ``days``. Any malformed input raises ``PeriodError`` so tool
    wrappers can return ``{"error": ...}``.
    """
    today = today or date.today()
    if since is not None and days is not None:
        raise PeriodError("use either days or since/until, not both")

    end = _parse_iso_date(until, "until") if until else today
    if since is not None:
        start = _parse_iso_date(since, "since")
    else:
        span = DEFAULT_DAYS if days is None else int(days)
        if span < 1 or span > MAX_DAYS:
            raise PeriodError(f"days must be between 1 and {MAX_DAYS}, got {span}")
        start = end - timedelta(days=span - 1)

    if start > end:
        raise PeriodError(f"since {start.isoformat()} is after until {end.isoformat()}")
    return Period(since=start, until=end)
