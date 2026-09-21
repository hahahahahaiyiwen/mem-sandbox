import asyncio
from uuid import UUID

import pytest

from mem_sandbox.core import OperationId, SessionId
from mem_sandbox.network import (
    HttpMethod,
    HttpScheme,
    HttpTransferLimits,
    HttpTransferUsage,
    NetworkOperationContext,
    NetworkPolicyId,
    OutboundHttpCancelled,
    OutboundHttpDenied,
    OutboundHttpGrant,
    OutboundHttpRequest,
    OutboundHttpResponse,
)
from mem_sandbox.network.testing import (
    FakeOutboundHttpGateway,
    OutboundHttpGatewayConformanceDriver,
)


class Cancellation:
    def __init__(self) -> None:
        self.cancelled = False

    def is_set(self) -> bool:
        return self.cancelled


def limits() -> HttpTransferLimits:
    return HttpTransferLimits(
        timeout_seconds=10,
        max_request_header_bytes=1024,
        max_request_body_bytes=0,
        max_response_header_bytes=1024,
        max_response_body_bytes=1024,
        max_decompressed_response_bytes=1024,
        max_redirects=1,
        max_requests=2,
        max_transferred_bytes=4096,
    )


def request() -> OutboundHttpRequest:
    return OutboundHttpRequest(
        method=HttpMethod.GET,
        url="https://example.test/value",
        limits=limits(),
    )


def response() -> OutboundHttpResponse:
    return OutboundHttpResponse(
        status_code=200,
        headers=(),
        body=b"value",
        usage=HttpTransferUsage(
            request_count=1,
            request_bytes=0,
            response_bytes=5,
            decompressed_response_bytes=5,
            redirect_count=0,
            duration_ms=1,
        ),
    )


def context(cancellation: Cancellation | None = None) -> NetworkOperationContext:
    return NetworkOperationContext(
        session_id=SessionId(UUID(int=1)),
        operation_id=OperationId(UUID(int=2)),
        grant=OutboundHttpGrant(
            policy_id=NetworkPolicyId("fixture"),
            methods=(HttpMethod.GET,),
            schemes=(HttpScheme.HTTPS,),
            limits=limits(),
        ),
        deadline_monotonic=100,
        cancellation=cancellation,
    )


@pytest.mark.asyncio
async def test_conformance_driver_round_trips_and_fake_records_exact_values() -> None:
    expected = response()
    gateway = FakeOutboundHttpGateway((expected,))
    driver = OutboundHttpGatewayConformanceDriver(gateway)
    expected_request = request()
    expected_context = context()

    actual = await driver.assert_round_trip(
        expected_request,
        expected_context,
        expected,
    )

    assert actual is expected
    assert gateway.calls[0].request is expected_request
    assert gateway.calls[0].context is expected_context


@pytest.mark.asyncio
async def test_conformance_driver_observes_stable_failure() -> None:
    gateway = FakeOutboundHttpGateway((OutboundHttpDenied("denied"),))
    driver = OutboundHttpGatewayConformanceDriver(gateway)

    error = await driver.assert_stable_failure(
        request(),
        context(),
        OutboundHttpDenied,
    )

    assert error.code == "outbound_http_denied"


@pytest.mark.asyncio
async def test_fake_gateway_cancels_while_blocked_without_consuming_network() -> None:
    release = asyncio.Event()
    cancellation = Cancellation()
    gateway = FakeOutboundHttpGateway((response(),), release=release)
    sending = asyncio.create_task(gateway.send(request(), context(cancellation)))
    await gateway.entered.wait()

    cancellation.cancelled = True

    with pytest.raises(OutboundHttpCancelled):
        await sending
    assert len(gateway.calls) == 1
