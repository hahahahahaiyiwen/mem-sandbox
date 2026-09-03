"""Classified, redacted, bounded sandbox event contracts."""

from mem_sandbox.events.delivery import (
    EventDiagnosticHandler,
    EventDispatcher,
    EventSink,
    OwnedEventSink,
)
from mem_sandbox.events.errors import (
    EventBufferLimitExceeded,
    EventDeliveryFailed,
    EventInvalid,
    EventPayloadRejected,
    EventSinkClosed,
)
from mem_sandbox.events.models import (
    EventAttribute,
    EventCategory,
    EventDeliveryDiagnostic,
    EventDeliveryMode,
    EventDeliveryPolicy,
    EventId,
    EventPayloadLimits,
    EventPayloadPolicy,
    EventQuery,
    EventSensitivity,
    EventSinkStats,
    EventValue,
    SandboxEvent,
    SandboxEventType,
    canonical_event_bytes,
)
from mem_sandbox.events.redaction import ProtectedValueRedactor, prepare_event
from mem_sandbox.events.sink import InMemoryEventSink, NoOpEventSink

__all__ = [
    "EventAttribute",
    "EventBufferLimitExceeded",
    "EventCategory",
    "EventDeliveryDiagnostic",
    "EventDeliveryFailed",
    "EventDeliveryMode",
    "EventDeliveryPolicy",
    "EventDiagnosticHandler",
    "EventDispatcher",
    "EventId",
    "EventInvalid",
    "EventPayloadLimits",
    "EventPayloadPolicy",
    "EventPayloadRejected",
    "EventQuery",
    "EventSensitivity",
    "EventSink",
    "EventSinkClosed",
    "EventSinkStats",
    "EventValue",
    "InMemoryEventSink",
    "NoOpEventSink",
    "OwnedEventSink",
    "ProtectedValueRedactor",
    "SandboxEvent",
    "SandboxEventType",
    "canonical_event_bytes",
    "prepare_event",
]
