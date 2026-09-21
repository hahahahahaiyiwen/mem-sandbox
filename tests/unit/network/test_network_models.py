from dataclasses import FrozenInstanceError
from uuid import UUID

import pytest

from mem_sandbox.core import OperationId, SessionId
from mem_sandbox.network import (
    CredentialRouteId,
    HttpEventDelivery,
    HttpHeader,
    HttpMethod,
    HttpScheme,
    HttpTransferLimits,
    HttpTransferUsage,
    NetworkOperationContext,
    NetworkPolicyId,
    OutboundHttpBinding,
    OutboundHttpDenied,
    OutboundHttpGrant,
    OutboundHttpLimitExceeded,
    OutboundHttpRequest,
    OutboundHttpRequestInvalid,
    OutboundHttpResponse,
)


class Gateway:
    async def send(
        self,
        request: OutboundHttpRequest,
        context: NetworkOperationContext,
    ) -> OutboundHttpResponse:
        raise AssertionError((request, context))


def limits(**changes: int | float) -> HttpTransferLimits:
    values: dict[str, int | float] = {
        "timeout_seconds": 10.0,
        "max_request_header_bytes": 1024,
        "max_request_body_bytes": 0,
        "max_response_header_bytes": 2048,
        "max_response_body_bytes": 4096,
        "max_decompressed_response_bytes": 8192,
        "max_redirects": 3,
        "max_requests": 4,
        "max_transferred_bytes": 16384,
    }
    values.update(changes)
    return HttpTransferLimits(**values)  # type: ignore[arg-type]


def grant(
    *,
    methods: tuple[HttpMethod, ...] = (HttpMethod.GET, HttpMethod.HEAD),
    schemes: tuple[HttpScheme, ...] = (HttpScheme.HTTPS,),
    transfer_limits: HttpTransferLimits | None = None,
    routes: tuple[CredentialRouteId, ...] = (),
    event_delivery: HttpEventDelivery = HttpEventDelivery.REQUIRED,
) -> OutboundHttpGrant:
    return OutboundHttpGrant(
        policy_id=NetworkPolicyId("public-docs"),
        methods=methods,
        schemes=schemes,
        limits=transfer_limits or limits(),
        credential_routes=routes,
        event_delivery=event_delivery,
    )


def test_http_request_and_response_are_immutable_and_non_revealing() -> None:
    route = CredentialRouteId("docs-token")
    request = OutboundHttpRequest(
        method=HttpMethod.GET,
        url="https://docs.example.test/source?q=secret",
        headers=(HttpHeader("X-Test", "protected-value"),),
        limits=limits(),
        credential_route=route,
    )
    usage = HttpTransferUsage(
        request_count=1,
        request_bytes=20,
        response_bytes=5,
        decompressed_response_bytes=5,
        redirect_count=0,
        duration_ms=1.5,
    )
    response = OutboundHttpResponse(
        status_code=200,
        headers=(HttpHeader("Content-Type", "text/plain"),),
        body=b"value",
        usage=usage,
    )

    assert request.scheme is HttpScheme.HTTPS
    assert request.credential_route is route
    assert response.usage is usage
    assert "protected-value" not in repr(request)
    assert "secret" not in repr(request)
    assert "value" not in repr(response)
    with pytest.raises(FrozenInstanceError):
        request.url = "https://other.test"  # type: ignore[misc]


@pytest.mark.parametrize(
    "request_factory",
    [
        lambda: OutboundHttpRequest(
            method=HttpMethod.GET,
            url="ftp://example.test/a",
            limits=limits(),
        ),
        lambda: OutboundHttpRequest(
            method=HttpMethod.GET,
            url="https://example.test/a",
            body=b"not-allowed",
            limits=limits(max_request_body_bytes=100),
        ),
        lambda: OutboundHttpRequest(
            method=HttpMethod.HEAD,
            url="https://example.test/a",
            headers=(HttpHeader("X-Test", "x" * 1024),),
            limits=limits(max_request_header_bytes=10),
        ),
    ],
)
def test_http_request_rejects_unsupported_or_over_limit_input(
    request_factory: object,
) -> None:
    with pytest.raises((OutboundHttpRequestInvalid, OutboundHttpLimitExceeded)):
        request_factory()  # type: ignore[operator]


def test_transfer_limits_accept_exact_minimums_and_reject_invalid_boundaries() -> None:
    minimum = HttpTransferLimits(
        timeout_seconds=0.001,
        max_request_header_bytes=0,
        max_request_body_bytes=0,
        max_response_header_bytes=0,
        max_response_body_bytes=0,
        max_decompressed_response_bytes=0,
        max_redirects=0,
        max_requests=1,
        max_transferred_bytes=0,
    )

    assert minimum.max_requests == 1
    assert minimum.max_transferred_bytes == 0
    with pytest.raises(ValueError):
        limits(timeout_seconds=0)
    with pytest.raises(ValueError):
        limits(max_request_header_bytes=-1)
    with pytest.raises(ValueError):
        limits(max_requests=0)
    with pytest.raises(ValueError, match="decompressed"):
        limits(max_response_body_bytes=2, max_decompressed_response_bytes=1)


def test_request_and_response_accept_exact_limits_and_reject_one_over() -> None:
    exact_limits = HttpTransferLimits(
        timeout_seconds=1,
        max_request_header_bytes=8,
        max_request_body_bytes=0,
        max_response_header_bytes=0,
        max_response_body_bytes=4,
        max_decompressed_response_bytes=4,
        max_redirects=0,
        max_requests=1,
        max_transferred_bytes=4,
    )
    request = OutboundHttpRequest(
        method=HttpMethod.GET,
        url="https://example.test",
        headers=(HttpHeader("X", "abc"),),
        limits=exact_limits,
    )
    configured = grant(transfer_limits=exact_limits)
    exact_response = OutboundHttpResponse(
        status_code=200,
        headers=(),
        body=b"data",
        usage=HttpTransferUsage(
            request_count=1,
            request_bytes=0,
            response_bytes=4,
            decompressed_response_bytes=4,
            redirect_count=0,
            duration_ms=1000,
        ),
    )

    configured.require_request(request)
    configured.require_response(request, exact_response)
    with pytest.raises(OutboundHttpLimitExceeded):
        OutboundHttpRequest(
            method=HttpMethod.GET,
            url="https://example.test",
            headers=(HttpHeader("X", "abcd"),),
            limits=exact_limits,
        )
    with pytest.raises(OutboundHttpLimitExceeded):
        configured.require_response(
            request,
            OutboundHttpResponse(
                status_code=200,
                headers=(),
                body=b"data!",
                usage=HttpTransferUsage(
                    request_count=1,
                    request_bytes=0,
                    response_bytes=5,
                    decompressed_response_bytes=5,
                    redirect_count=0,
                    duration_ms=1000,
                ),
            ),
        )


def test_grant_partial_order_is_fail_closed() -> None:
    ceiling = grant(
        routes=(CredentialRouteId("a"), CredentialRouteId("b")),
        event_delivery=HttpEventDelivery.BEST_EFFORT,
    )
    narrower = grant(
        methods=(HttpMethod.GET,),
        transfer_limits=limits(
            timeout_seconds=5.0,
            max_response_body_bytes=2048,
            max_decompressed_response_bytes=4096,
            max_requests=2,
            max_transferred_bytes=8192,
        ),
        routes=(CredentialRouteId("a"),),
        event_delivery=HttpEventDelivery.REQUIRED,
    )

    assert narrower.is_within(ceiling)
    assert not ceiling.is_within(narrower)
    assert not OutboundHttpGrant(
        policy_id=NetworkPolicyId("different-policy"),
        methods=narrower.methods,
        schemes=narrower.schemes,
        limits=narrower.limits,
        credential_routes=narrower.credential_routes,
        event_delivery=narrower.event_delivery,
    ).is_within(ceiling)


def test_grant_rejects_request_before_gateway_use() -> None:
    configured = grant(
        methods=(HttpMethod.GET,),
        routes=(CredentialRouteId("approved"),),
    )

    with pytest.raises(OutboundHttpDenied):
        configured.require_request(
            OutboundHttpRequest(
                method=HttpMethod.HEAD,
                url="https://example.test",
                limits=limits(),
            )
        )
    with pytest.raises(OutboundHttpDenied):
        configured.require_request(
            OutboundHttpRequest(
                method=HttpMethod.GET,
                url="http://example.test",
                limits=limits(),
            )
        )
    with pytest.raises(OutboundHttpDenied):
        configured.require_request(
            OutboundHttpRequest(
                method=HttpMethod.GET,
                url="https://example.test",
                limits=limits(),
                credential_route=CredentialRouteId("unapproved"),
            )
        )
    with pytest.raises(OutboundHttpLimitExceeded):
        configured.require_request(
            OutboundHttpRequest(
                method=HttpMethod.GET,
                url="https://example.test",
                limits=limits(max_response_body_bytes=8192),
            )
        )


def test_binding_can_only_create_equal_or_narrower_session_view() -> None:
    gateway = Gateway()
    ceiling = grant()
    binding = OutboundHttpBinding(gateway=gateway, grant=ceiling)
    requested = grant(methods=(HttpMethod.GET,))

    narrowed = binding.with_grant(requested)

    assert narrowed.gateway is gateway
    assert narrowed.grant is requested
    with pytest.raises(OutboundHttpDenied):
        narrowed.with_grant(ceiling)


def test_network_context_hides_grant_details() -> None:
    context = NetworkOperationContext(
        session_id=SessionId(UUID(int=1)),
        operation_id=OperationId(UUID(int=2)),
        grant=grant(routes=(CredentialRouteId("hidden-route"),)),
        deadline_monotonic=10.0,
    )

    assert "hidden-route" not in repr(context)
    assert "public-docs" not in repr(context)
