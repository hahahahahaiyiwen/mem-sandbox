"""Shared immutable operation contracts."""

import math
from dataclasses import dataclass
from enum import StrEnum


class OperationKind(StrEnum):
    """Milestone 3 model-facing and host operation kinds."""

    EXECUTE = "execute"
    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    APPLY_PATCH = "apply_patch"
    READ_BYTES = "read_bytes"
    WRITE_BYTES = "write_bytes"
    STAT = "stat"
    LIST_ENTRIES = "list_entries"
    CREATE_SNAPSHOT = "create_snapshot"
    RESTORE_SNAPSHOT = "restore_snapshot"


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationLimits:
    """One end-to-end deadline and its protected terminal-delivery reserve."""

    timeout_seconds: float = 30.0
    terminal_event_reserve_seconds: float = 1.0

    def __post_init__(self) -> None:
        _require_positive_finite("timeout_seconds", self.timeout_seconds)
        _require_positive_finite(
            "terminal_event_reserve_seconds",
            self.terminal_event_reserve_seconds,
        )
        if self.terminal_event_reserve_seconds >= self.timeout_seconds:
            raise ValueError(
                "terminal_event_reserve_seconds must be strictly less than timeout_seconds"
            )


def _require_positive_finite(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be positive and finite")
