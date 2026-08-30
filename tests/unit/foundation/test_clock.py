from __future__ import annotations

from datetime import UTC, datetime

from mem_sandbox.core.clock import Clock, SystemClock


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self._value = value

    def now(self) -> datetime:
        return self._value


def _read_time(clock: Clock) -> datetime:
    return clock.now()


def test_clock_can_be_substituted_at_the_boundary() -> None:
    expected = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)

    assert _read_time(FixedClock(expected)) == expected


def test_system_clock_returns_an_aware_utc_time() -> None:
    value = SystemClock().now()
    offset = value.utcoffset()

    assert value.tzinfo is not None
    assert offset is not None
    assert offset.total_seconds() == 0
