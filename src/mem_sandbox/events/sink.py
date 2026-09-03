"""Bounded event sink implementations."""

import asyncio

from mem_sandbox.events.errors import (
    EventBufferLimitExceeded,
    EventInvalid,
    EventSinkClosed,
)
from mem_sandbox.events.models import (
    EventQuery,
    EventSinkStats,
    SandboxEvent,
    canonical_event_bytes,
)


class NoOpEventSink:
    """Accept and discard every prepared event."""

    __slots__ = ()

    async def emit(self, event: SandboxEvent) -> None:
        _ = event


class InMemoryEventSink:
    """Collect a finite process-local event history without eviction."""

    def __init__(self, *, max_events: int, max_payload_bytes: int) -> None:
        _require_positive_integer("max_events", max_events)
        _require_positive_integer("max_payload_bytes", max_payload_bytes)
        self._max_events = max_events
        self._max_payload_bytes = max_payload_bytes
        self._lock = asyncio.Lock()
        self._events: list[SandboxEvent] = []
        self._payload_bytes = 0
        self._last_sequences: dict[object, int] = {}
        self._closed = False

    async def emit(self, event: SandboxEvent) -> None:
        encoded_size = len(canonical_event_bytes(event))
        async with self._lock:
            if self._closed:
                raise EventSinkClosed("event sink is closed")
            previous = self._last_sequences.get(event.session_id)
            if previous is not None and event.sequence <= previous:
                raise EventInvalid(
                    "accepted event sequences must be strictly increasing per session"
                )
            if (
                len(self._events) + 1 > self._max_events
                or self._payload_bytes + encoded_size > self._max_payload_bytes
            ):
                raise EventBufferLimitExceeded(
                    "accepting the event would exceed the in-memory sink limits"
                )

            self._events.append(event)
            self._payload_bytes += encoded_size
            self._last_sequences[event.session_id] = event.sequence

    async def query(self, query: EventQuery | None = None) -> tuple[SandboxEvent, ...]:
        requested = EventQuery() if query is None else query
        async with self._lock:
            selected = tuple(
                event
                for event in self._events
                if _matches(event, requested)
            )
        return tuple(
            sorted(
                selected,
                key=lambda event: (
                    event.occurred_at,
                    str(event.session_id),
                    event.sequence,
                ),
            )
        )

    async def stats(self) -> EventSinkStats:
        async with self._lock:
            return EventSinkStats(len(self._events), self._payload_bytes)

    async def flush(self) -> None:
        async with self._lock:
            return None

    async def close(self) -> None:
        async with self._lock:
            self._closed = True


def _matches(event: SandboxEvent, query: EventQuery) -> bool:
    return (
        (query.session_id is None or event.session_id == query.session_id)
        and (query.operation_id is None or event.operation_id == query.operation_id)
        and (query.category is None or event.category is query.category)
        and (query.event_type is None or event.event_type is query.event_type)
    )


def _require_positive_integer(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")
