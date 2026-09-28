"""Controlled system address resolution for admitted HTTP hostnames."""

from __future__ import annotations

import asyncio
import socket
from typing import cast

from mem_sandbox.network.destination import NetworkResolution, ResolvedHttpAddress
from mem_sandbox.network.errors import OutboundHttpCancelled, OutboundHttpTimeout
from mem_sandbox.network.models import NetworkOperationContext


class SystemNetworkResolver:
    """Resolve final stream addresses through the host's configured resolver."""

    async def resolve(
        self,
        hostname: str,
        port: int,
        context: NetworkOperationContext,
    ) -> NetworkResolution:
        if not isinstance(cast(object, hostname), str) or not hostname:
            raise TypeError("hostname must be a non-empty string")
        port_value = cast(object, port)
        if isinstance(port_value, bool) or not isinstance(port_value, int):
            raise TypeError("port must be an integer")
        if port < 1 or port > 65535:
            raise ValueError("port must be between 1 and 65535")
        if not isinstance(cast(object, context), NetworkOperationContext):
            raise TypeError("context must be NetworkOperationContext")
        _require_not_cancelled(context)
        timed_out = False
        results: list[tuple[int, int, int, str, tuple[object, ...]]] | None = None
        try:
            async with asyncio.timeout_at(context.deadline_monotonic):
                raw = await asyncio.get_running_loop().getaddrinfo(
                    hostname,
                    port,
                    family=socket.AF_UNSPEC,
                    type=socket.SOCK_STREAM,
                    proto=socket.IPPROTO_TCP,
                    flags=socket.AI_CANONNAME,
                )
                results = cast(
                    list[tuple[int, int, int, str, tuple[object, ...]]],
                    raw,
                )
        except TimeoutError:
            timed_out = True
        if timed_out:
            raise OutboundHttpTimeout("controlled destination resolution timed out")
        assert results is not None
        _require_not_cancelled(context)
        addresses: list[ResolvedHttpAddress] = []
        canonical_hostname: str | None = None
        for family, _kind, _protocol, canonical, sockaddr in results:
            if family not in (socket.AF_INET, socket.AF_INET6) or not sockaddr:
                continue
            if canonical and canonical_hostname is None:
                canonical_hostname = canonical
            address = ResolvedHttpAddress(cast(str, sockaddr[0]))
            if address not in addresses:
                addresses.append(address)
        return NetworkResolution(
            hostname=hostname,
            addresses=tuple(addresses),
            canonical_hostname=canonical_hostname,
        )


def _require_not_cancelled(context: NetworkOperationContext) -> None:
    cancellation = context.cancellation
    if cancellation is not None and cancellation.is_set():
        raise OutboundHttpCancelled("outbound HTTP resolution was cancelled")
