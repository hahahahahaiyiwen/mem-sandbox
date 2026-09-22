"""Narrow interfaces owned by the controlled-network boundary."""

from typing import Protocol

from mem_sandbox.network.models import (
    NetworkOperationContext,
    OutboundHttpRequest,
    OutboundHttpResponse,
)


class NetworkCancellationSignal(Protocol):
    """Minimal cooperative cancellation boundary for network operations."""

    def is_set(self) -> bool: ...


class OutboundHttpGateway(Protocol):
    """Framework-neutral bounded outbound HTTP boundary."""

    async def send(
        self,
        request: OutboundHttpRequest,
        context: NetworkOperationContext,
    ) -> OutboundHttpResponse: ...
