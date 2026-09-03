"""Stable event preparation, collection, and delivery failures."""

from mem_sandbox.core import (
    ConflictError,
    InternalSandboxError,
    InvalidRequestError,
    QuotaExceededError,
)


class EventPayloadRejected(InvalidRequestError):
    code = "event_payload_rejected"


class EventInvalid(InvalidRequestError):
    code = "event_invalid"


class EventBufferLimitExceeded(QuotaExceededError):
    code = "event_buffer_limit_exceeded"


class EventDeliveryFailed(InternalSandboxError):
    code = "event_delivery_failed"


class EventSinkClosed(ConflictError):
    code = "event_sink_closed"
