"""Immutable classified event contracts and canonical encoding."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from types import MappingProxyType
from typing import cast

from mem_sandbox.core import OperationId, OperationKind, SessionId

type EventValue = str | int | float | bool | None


class EventCategory(StrEnum):
    """Stable event group used for bounded collection queries."""

    LIFECYCLE = "lifecycle"
    OPERATION = "operation"
    SNAPSHOT = "snapshot"
    SERVICE_LIFECYCLE = "service_lifecycle"


class EventSensitivity(StrEnum):
    """Required field-level event-data classification."""

    PUBLIC = "public"
    INTERNAL = "internal"
    PROTECTED = "protected"
    SECRET = "secret"


class SandboxEventType(StrEnum):
    SANDBOX_CREATED = "sandbox.created"
    SANDBOX_DELETED = "sandbox.deleted"
    SANDBOX_STARTED = "sandbox.started"
    SANDBOX_CLOSING = "sandbox.closing"
    SANDBOX_CLOSED = "sandbox.closed"
    SANDBOX_FAILED = "sandbox.failed"
    OPERATION_STARTED = "operation.started"
    OPERATION_COMPLETED = "operation.completed"
    OPERATION_FAILED = "operation.failed"
    OPERATION_CANCELLED = "operation.cancelled"
    OPERATION_TIMED_OUT = "operation.timed_out"
    SNAPSHOT_CREATED = "snapshot.created"
    SNAPSHOT_RESTORED = "snapshot.restored"

    @property
    def category(self) -> EventCategory:
        """Return the fixed category owned by this event type."""
        if self in (self.SANDBOX_CREATED, self.SANDBOX_DELETED):
            return EventCategory.SERVICE_LIFECYCLE
        if self.value.startswith("sandbox."):
            return EventCategory.LIFECYCLE
        if self.value.startswith("operation."):
            return EventCategory.OPERATION
        return EventCategory.SNAPSHOT


@dataclass(frozen=True, slots=True, order=True)
class EventAttribute:
    """One classified scalar event field."""

    name: str
    value: EventValue
    sensitivity: EventSensitivity

    def __post_init__(self) -> None:
        name = cast(object, self.name)
        if not isinstance(name, str):
            raise TypeError("event attribute name must be a string")
        if not name:
            raise ValueError("event attribute name must not be empty")
        value = cast(object, self.value)
        if not isinstance(value, str | int | float | bool | type(None)):
            raise TypeError("event attribute values must be scalar values")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("event float attribute values must be finite")
        sensitivity_value = cast(object, self.sensitivity)
        if not isinstance(sensitivity_value, EventSensitivity):
            try:
                sensitivity = EventSensitivity(sensitivity_value)
            except (TypeError, ValueError) as error:
                raise ValueError("event attribute sensitivity is not supported") from error
            object.__setattr__(self, "sensitivity", sensitivity)


@dataclass(frozen=True, slots=True, order=True)
class EventId:
    """Identity derived from a session and its event sequence."""

    session_id: SessionId
    sequence: int

    def __post_init__(self) -> None:
        _require_sequence(self.sequence)

    def __str__(self) -> str:
        return f"{self.session_id}:{self.sequence}"


@dataclass(frozen=True, slots=True)
class SandboxEvent:
    """One immutable, classified, session-sequenced event."""

    event_type: SandboxEventType
    occurred_at: datetime
    session_id: SessionId
    sequence: int
    operation_id: OperationId | None = None
    parent_operation_id: OperationId | None = None
    operation_kind: OperationKind | None = None
    attributes: tuple[EventAttribute, ...] = ()
    _data: Mapping[str, EventValue] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        event_type_value = cast(object, self.event_type)
        if not isinstance(event_type_value, SandboxEventType):
            try:
                event_type = SandboxEventType(event_type_value)
            except (TypeError, ValueError) as error:
                raise ValueError("event_type is not supported") from error
            object.__setattr__(self, "event_type", event_type)
        _require_utc(self.occurred_at)
        _require_sequence(self.sequence)

        attributes_value = cast(object, self.attributes)
        if not isinstance(attributes_value, tuple):
            raise TypeError("attributes must contain EventAttribute values")
        attribute_values = cast(tuple[object, ...], attributes_value)
        if any(not isinstance(attribute, EventAttribute) for attribute in attribute_values):
            raise TypeError("attributes must contain EventAttribute values")
        attributes = cast(tuple[EventAttribute, ...], attribute_values)
        ordered = tuple(sorted(attributes, key=lambda attribute: attribute.name.encode("utf-8")))
        if len({attribute.name for attribute in ordered}) != len(ordered):
            raise ValueError("event attribute names must not contain duplicates")
        object.__setattr__(self, "attributes", ordered)
        object.__setattr__(
            self,
            "_data",
            MappingProxyType({attribute.name: attribute.value for attribute in ordered}),
        )

    @property
    def event_id(self) -> EventId:
        """Return identity derived from the immutable envelope."""
        return EventId(self.session_id, self.sequence)

    @property
    def category(self) -> EventCategory:
        """Return this event's fixed category."""
        return self.event_type.category

    @property
    def data(self) -> Mapping[str, EventValue]:
        """Return a read-only convenience view of classified values."""
        return self._data


@dataclass(frozen=True, slots=True, kw_only=True)
class EventPayloadLimits:
    """Finite limits applied after structural redaction."""

    max_attributes: int = 32
    max_attribute_name_bytes: int = 64
    max_string_bytes: int = 1024
    max_event_bytes: int = 16 * 1024

    def __post_init__(self) -> None:
        for name in (
            "max_attributes",
            "max_attribute_name_bytes",
            "max_string_bytes",
            "max_event_bytes",
        ):
            _require_positive_integer(name, getattr(self, name))


@dataclass(frozen=True, slots=True, kw_only=True)
class EventPayloadPolicy:
    """Controls whether protected event attributes may be prepared."""

    allow_protected: bool = False

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.allow_protected), bool):
            raise TypeError("allow_protected must be a boolean")


class EventDeliveryMode(StrEnum):
    """Session event delivery semantics."""

    REQUIRED = "required"
    BEST_EFFORT = "best_effort"


@dataclass(frozen=True, slots=True, kw_only=True)
class EventDeliveryPolicy:
    """Immutable per-session dispatcher configuration."""

    mode: EventDeliveryMode = EventDeliveryMode.REQUIRED
    max_pending_events: int = 256
    sink_timeout_seconds: float = 1.0

    def __post_init__(self) -> None:
        mode_value = cast(object, self.mode)
        if not isinstance(mode_value, EventDeliveryMode):
            try:
                mode = EventDeliveryMode(mode_value)
            except (TypeError, ValueError) as error:
                raise ValueError("event delivery mode is not supported") from error
            object.__setattr__(self, "mode", mode)
        _require_positive_integer("max_pending_events", self.max_pending_events)
        timeout = cast(object, self.sink_timeout_seconds)
        if isinstance(timeout, bool) or not isinstance(timeout, int | float):
            raise TypeError("sink_timeout_seconds must be numeric")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("sink_timeout_seconds must be positive and finite")


@dataclass(frozen=True, slots=True)
class EventDeliveryDiagnostic:
    """Exactly-once report for one best-effort event failure."""

    event_id: EventId
    failure_code: str
    message: str

    def __post_init__(self) -> None:
        for name in ("failure_code", "message"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a string")
            if not value:
                raise ValueError(f"{name} must not be empty")


@dataclass(frozen=True, slots=True, kw_only=True)
class EventQuery:
    """Optional filters for deterministic in-memory event queries."""

    session_id: SessionId | None = None
    operation_id: OperationId | None = None
    category: EventCategory | None = None
    event_type: SandboxEventType | None = None


@dataclass(frozen=True, slots=True)
class EventSinkStats:
    """Current bounded in-memory sink usage."""

    event_count: int
    payload_bytes: int


def canonical_event_bytes(event: SandboxEvent) -> bytes:
    """Encode one event as stable compact UTF-8 JSON."""
    payload = {
        "attributes": [
            {
                "name": attribute.name,
                "sensitivity": attribute.sensitivity.value,
                "value": attribute.value,
            }
            for attribute in event.attributes
        ],
        "event_id": str(event.event_id),
        "event_type": event.event_type.value,
        "occurred_at": _canonical_timestamp(event.occurred_at),
        "operation_id": None if event.operation_id is None else str(event.operation_id),
        "operation_kind": (None if event.operation_kind is None else event.operation_kind.value),
        "parent_operation_id": (
            None if event.parent_operation_id is None else str(event.parent_operation_id)
        ),
        "sequence": event.sequence,
        "session_id": str(event.session_id),
    }
    return json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _canonical_timestamp(value: datetime) -> str:
    utc_value = value.astimezone(UTC)
    return utc_value.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _require_utc(value: datetime) -> None:
    datetime_value = cast(object, value)
    if not isinstance(datetime_value, datetime):
        raise TypeError("occurred_at must be a datetime")
    if datetime_value.tzinfo is None or datetime_value.utcoffset() is None:
        raise ValueError("occurred_at must be timezone-aware")
    if datetime_value.utcoffset() != timedelta(0):
        raise ValueError("occurred_at must use UTC")


def _require_sequence(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError("sequence must be an integer")
    if value <= 0:
        raise ValueError("sequence must be positive")


def _require_positive_integer(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
