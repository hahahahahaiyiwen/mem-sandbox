"""Immutable minimal sandbox event envelope."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from mem_sandbox.core import OperationId, OperationKind, SessionId

type EventValue = str | int | float | bool | None


class SandboxEventType(StrEnum):
    SANDBOX_STARTED = "sandbox.started"
    SANDBOX_CLOSING = "sandbox.closing"
    SANDBOX_CLOSED = "sandbox.closed"
    SANDBOX_FAILED = "sandbox.failed"
    OPERATION_STARTED = "operation.started"
    OPERATION_COMPLETED = "operation.completed"
    OPERATION_FAILED = "operation.failed"
    OPERATION_CANCELLED = "operation.cancelled"
    OPERATION_TIMED_OUT = "operation.timed_out"


@dataclass(frozen=True, slots=True)
class SandboxEvent:
    """One session-sequenced event containing only bounded safe metadata."""

    event_type: str
    occurred_at: datetime
    session_id: SessionId
    sequence: int
    operation_id: OperationId | None = None
    parent_operation_id: OperationId | None = None
    operation_kind: OperationKind | None = None
    data: Mapping[str, EventValue] = field(default_factory=lambda: MappingProxyType({}))

    def __post_init__(self) -> None:
        if self.event_type not in SandboxEventType:
            raise ValueError("event_type is not supported by the Milestone 3 envelope")
        _require_utc(self.occurred_at)
        sequence = cast(object, self.sequence)
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise TypeError("sequence must be an integer")
        if sequence <= 0:
            raise ValueError("sequence must be positive")
        if len(self.data) > 32:
            raise ValueError("event data must contain at most 32 entries")
        copied: dict[str, EventValue] = {}
        for key, value in self.data.items():
            key_value = cast(object, key)
            if (
                not isinstance(key_value, str)
                or not key_value
                or len(key_value.encode("utf-8")) > 64
            ):
                raise ValueError("event data keys must be non-empty and at most 64 UTF-8 bytes")
            event_value = cast(object, value)
            if not isinstance(event_value, str | int | float | bool | type(None)):
                raise TypeError("event data values must be bounded scalar values")
            if isinstance(event_value, str) and len(event_value.encode("utf-8")) > 1024:
                raise ValueError("event string data must be at most 1024 UTF-8 bytes")
            copied[key_value] = event_value
        object.__setattr__(self, "data", MappingProxyType(copied))


def _require_utc(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")
    if value.utcoffset() != timedelta(0):
        raise ValueError("occurred_at must use UTC")
