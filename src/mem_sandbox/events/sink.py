"""Minimal event sink implementations."""

from mem_sandbox.events.models import SandboxEvent


class NoOpEventSink:
    """Accept and discard every valid event."""

    __slots__ = ()

    async def emit(self, event: SandboxEvent) -> None:
        _ = event
