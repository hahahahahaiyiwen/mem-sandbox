"""Clock boundary used by deterministic core behavior."""

from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Provides the current time to a consuming core module."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC time."""
        ...


class SystemClock:
    """Production clock backed by the system UTC time."""

    __slots__ = ()

    def now(self) -> datetime:
        """Return the current timezone-aware UTC time."""
        return datetime.now(UTC)
