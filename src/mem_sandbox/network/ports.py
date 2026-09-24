"""Narrow interfaces owned by the controlled-network boundary."""

from typing import Protocol

from mem_sandbox.network.destination import (
    NetworkPolicyDecision,
    NetworkPolicyRequest,
    NetworkResolution,
)
from mem_sandbox.network.models import (
    NetworkOperationContext,
    OutboundHttpRequest,
    OutboundHttpResponse,
)
from mem_sandbox.network.transport import HttpTransportRequest, HttpTransportResponse


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


class NetworkPolicyEngine(Protocol):
    """Admit normalized HTTP facts before and after controlled resolution."""

    async def evaluate(
        self,
        request: NetworkPolicyRequest,
    ) -> NetworkPolicyDecision: ...


class NetworkResolver(Protocol):
    """Resolve one already-admitted hostname without opening a connection."""

    async def resolve(
        self,
        hostname: str,
        port: int,
        context: NetworkOperationContext,
    ) -> NetworkResolution: ...


class HttpTransport(Protocol):
    """Perform exactly one direct attempt to an admitted numeric peer."""

    async def send(
        self,
        request: HttpTransportRequest,
        context: NetworkOperationContext,
    ) -> HttpTransportResponse: ...
