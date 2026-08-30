"""Immutable metadata conventions for operation requests and results."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from mem_sandbox.core.identifiers import OperationId, Revision, SessionId


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationRequestMetadata:
    """Identity shared by concrete operation request types."""

    session_id: SessionId
    operation_id: OperationId


@dataclass(frozen=True, slots=True, kw_only=True)
class OperationResultMetadata:
    """Identity, timing, and revision shared by successful operation results."""

    session_id: SessionId
    operation_id: OperationId
    workspace_revision: Revision
    started_at: datetime
    completed_at: datetime

    def __post_init__(self) -> None:
        _require_aware_utc("started_at", self.started_at)
        _require_aware_utc("completed_at", self.completed_at)
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not be before started_at")

    @property
    def elapsed(self) -> timedelta:
        """Return the elapsed wall-clock duration."""
        return self.completed_at - self.started_at


def _require_aware_utc(name: str, value: datetime) -> None:
    offset = value.utcoffset()
    if value.tzinfo is None or offset is None:
        raise ValueError(f"{name} must be timezone-aware")
    if offset != timedelta(0):
        raise ValueError(f"{name} must use UTC")
