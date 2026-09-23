import asyncio
import gzip
from collections.abc import Callable, Coroutine
from typing import Any, cast
from uuid import UUID

import pytest

from mem_sandbox.core import OperationId, SessionId
from mem_sandbox.network import (
    BoundedOutboundHttpGateway,
    CredentialRouteId,
    HttpHeader,
    HttpMethod,
    HttpScheme,
    HttpTransferLimits,
    NetworkOperationContext,
    NetworkPolicyDecision,
    NetworkPolicyId,
    NetworkPolicyPhase,
    NetworkPolicyReason,
    NetworkPolicyRequest,
    NetworkResolution,
    OutboundHttpCancelled,
    OutboundHttpDenied,
    OutboundHttpGrant,
    OutboundHttpLimitExceeded,
    OutboundHttpRequest,
    OutboundHttpResolutionFailed,
    OutboundHttpResponseInvalid,
    OutboundHttpTimeout,
    OutboundHttpTransportFailed,
    ResolvedHttpAddress,
)
from mem_sandbox.network.transport import (
    HttpTransportRequest,
    HttpTransportResponse,
)


class Cancellation:
    def __init__(self, cancelled: bool = False) -> None:
        self.cancelled = cancelled

    def is_set(self) -> bool:
        return self.cancelled


class CheckCountCancellation(Cancellation):
    def __init__(self, cancel_on_check: int) -> None:
        super().__init__()
        self._cancel_on_check = cancel_on_check
        self.check_count = 0

    def is_set(self) -> bool:
        self.check_count += 1
        return self.check_count >= self._cancel_on_check


class RecordingPolicy:
    def __init__(
        self,
        decide: Callable[[NetworkPolicyRequest], NetworkPolicyDecision]
        | BaseException
        | None = None,
    ) -> None:
        self._decide = decide
        self.calls: list[NetworkPolicyRequest] = []

    async def evaluate(self, request: NetworkPolicyRequest) -> NetworkPolicyDecision:
        self.calls.append(request)
        decision = self._decide
        if isinstance(decision, BaseException):
            raise decision
        if decision is None:
            return NetworkPolicyDecision.allow()
        return decision(request)


class BlockingPolicy:
    def __init__(self) -> None:
        self.entered = asyncio.Event()

    async def evaluate(self, request: NetworkPolicyRequest) -> NetworkPolicyDecision:
        self.entered.set()
        await asyncio.Event().wait()
        raise AssertionError(request)


class PhaseBlockingPolicy:
    def __init__(self, phase: NetworkPolicyPhase) -> None:
        self._phase = phase
        self.calls: list[NetworkPolicyRequest] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def evaluate(self, request: NetworkPolicyRequest) -> NetworkPolicyDecision:
        self.calls.append(request)
        if request.phase is self._phase:
            self.entered.set()
            await self.release.wait()
        return NetworkPolicyDecision.allow()


class CoroutineTrackingPolicy:
    def __init__(self) -> None:
        self.calls = 0
        self.operation: Coroutine[Any, Any, NetworkPolicyDecision] | None = None

    def evaluate(
        self,
        request: NetworkPolicyRequest,
    ) -> Coroutine[Any, Any, NetworkPolicyDecision]:
        self.calls += 1

        async def decide() -> NetworkPolicyDecision:
            raise AssertionError(request)

        self.operation = decide()
        return self.operation


class CancellationResistantPolicy:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    async def evaluate(self, request: NetworkPolicyRequest) -> NetworkPolicyDecision:
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            await self.release.wait()
            return NetworkPolicyDecision.allow()
        raise AssertionError(request)


class RecordingResolver:
    def __init__(
        self,
        resolutions: dict[str, NetworkResolution | BaseException],
    ) -> None:
        self._resolutions = resolutions
        self.calls: list[tuple[str, int, NetworkOperationContext]] = []

    async def resolve(
        self,
        hostname: str,
        port: int,
        context: NetworkOperationContext,
    ) -> NetworkResolution:
        self.calls.append((hostname, port, context))
        outcome = self._resolutions[hostname]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class RecordingTransport:
    def __init__(
        self,
        outcomes: tuple[HttpTransportResponse | BaseException | object, ...],
    ) -> None:
        self._outcomes = outcomes
        self.calls: list[tuple[HttpTransportRequest, NetworkOperationContext]] = []

    async def send(
        self,
        request: HttpTransportRequest,
        context: NetworkOperationContext,
    ) -> HttpTransportResponse:
        index = len(self.calls)
        self.calls.append((request, context))
        outcome = self._outcomes[index]
        if isinstance(outcome, BaseException):
            raise outcome
        return cast(HttpTransportResponse, outcome)


def limits(**changes: int | float) -> HttpTransferLimits:
    values: dict[str, int | float] = {
        "timeout_seconds": 1.0,
        "max_request_header_bytes": 1024,
        "max_request_body_bytes": 0,
        "max_response_header_bytes": 1024,
        "max_response_body_bytes": 1024,
        "max_decompressed_response_bytes": 2048,
        "max_redirects": 3,
        "max_requests": 4,
        "max_transferred_bytes": 4096,
    }
    values.update(changes)
    return HttpTransferLimits(**values)  # type: ignore[arg-type]


def request(
    url: str = "https://example.test/start",
    *,
    method: HttpMethod = HttpMethod.GET,
    headers: tuple[HttpHeader, ...] = (),
    transfer_limits: HttpTransferLimits | None = None,
    credential_route: CredentialRouteId | None = None,
) -> OutboundHttpRequest:
    return OutboundHttpRequest(
        method=method,
        url=url,
        headers=headers,
        limits=transfer_limits or limits(),
        credential_route=credential_route,
    )


def context(
    transfer_limits: HttpTransferLimits | None = None,
    *,
    cancellation: Cancellation | None = None,
    deadline: float | None = None,
    routes: tuple[CredentialRouteId, ...] = (),
) -> NetworkOperationContext:
    effective_limits = transfer_limits or limits()
    return NetworkOperationContext(
        session_id=SessionId(UUID(int=1)),
        operation_id=OperationId(UUID(int=2)),
        grant=OutboundHttpGrant(
            policy_id=NetworkPolicyId("docs"),
            methods=(HttpMethod.GET, HttpMethod.HEAD),
            schemes=(HttpScheme.HTTP, HttpScheme.HTTPS),
            limits=effective_limits,
            credential_routes=routes,
        ),
        deadline_monotonic=deadline or asyncio.get_running_loop().time() + 10,
        cancellation=cancellation,
    )


def resolution(hostname: str, *addresses: str) -> NetworkResolution:
    return NetworkResolution(
        hostname=hostname,
        addresses=tuple(ResolvedHttpAddress(address) for address in addresses),
    )


def transport_response(
    status: int = 200,
    *,
    headers: tuple[HttpHeader, ...] = (),
    body: bytes = b"value",
    wire_bytes: int | None = None,
) -> HttpTransportResponse:
    return HttpTransportResponse(
        status_code=status,
        headers=headers,
        body=body,
        wire_bytes=len(body) if wire_bytes is None else wire_bytes,
    )


def gateway(
    *,
    policy: object | None = None,
    resolver: RecordingResolver | None = None,
    transport: RecordingTransport | None = None,
) -> tuple[
    BoundedOutboundHttpGateway,
    object,
    RecordingResolver,
    RecordingTransport,
]:
    effective_policy = policy or RecordingPolicy()
    effective_resolver = resolver or RecordingResolver(
        {"example.test": resolution("example.test", "8.8.8.8")}
    )
    effective_transport = transport or RecordingTransport((transport_response(),))
    return (
        BoundedOutboundHttpGateway(
            policy=effective_policy,  # type: ignore[arg-type]
            resolver=effective_resolver,
            transport=effective_transport,
        ),
        effective_policy,
        effective_resolver,
        effective_transport,
    )


@pytest.mark.asyncio
async def test_pre_resolution_denial_performs_no_dns_or_transport_access() -> None:
    policy = RecordingPolicy(
        lambda _: NetworkPolicyDecision.deny(NetworkPolicyReason.DESTINATION_NOT_ALLOWED)
    )
    subject, _, resolver, transport = gateway(policy=policy)

    with pytest.raises(OutboundHttpDenied):
        await subject.send(request(), context())

    assert [call.phase for call in policy.calls] == [NetworkPolicyPhase.PRE_RESOLUTION]
    assert resolver.calls == []
    assert transport.calls == []


@pytest.mark.asyncio
async def test_mixed_dns_answer_denies_every_address_before_transport() -> None:
    resolver = RecordingResolver(
        {"example.test": resolution("example.test", "8.8.8.8", "127.0.0.1")}
    )
    subject, policy, _, transport = gateway(resolver=resolver)

    with pytest.raises(OutboundHttpDenied):
        await subject.send(request(), context())

    assert isinstance(policy, RecordingPolicy)
    assert [call.phase for call in policy.calls] == [NetworkPolicyPhase.PRE_RESOLUTION]
    assert len(resolver.calls) == 1
    assert transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "127.0.0.1",
        "10.0.0.1",
        "169.254.1.1",
        "224.0.0.1",
        "240.0.0.1",
        "169.254.169.254",
        "::",
        "::1",
        "fc00::1",
        "fe80::1",
        "ff02::1",
        "100::1",
        "fd00:ec2::254",
    ],
)
async def test_every_non_global_address_class_is_denied_before_transport(
    address: str,
) -> None:
    resolver = RecordingResolver({"example.test": resolution("example.test", address)})
    subject, _, _, transport = gateway(resolver=resolver)

    with pytest.raises(OutboundHttpDenied):
        await subject.send(request(), context())

    assert transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "address",
    [
        "2001:4860:0:0:0:5efe:a00:1",
        "2001:4860:0:0:200:5efe:a00:1",
    ],
)
async def test_isatap_resolution_is_denied_before_transport(address: str) -> None:
    resolver = RecordingResolver({"example.test": resolution("example.test", address)})
    subject, _, _, transport = gateway(resolver=resolver)

    with pytest.raises(OutboundHttpDenied):
        await subject.send(request(), context())

    assert transport.calls == []


@pytest.mark.asyncio
async def test_ip_literal_skips_dns_and_transport_receives_only_admitted_peer() -> None:
    subject, policy, resolver, transport = gateway()

    response = await subject.send(request("https://8.8.8.8/value"), context())

    assert response.body == b"value"
    assert resolver.calls == []
    assert isinstance(policy, RecordingPolicy)
    assert [call.phase for call in policy.calls] == [
        NetworkPolicyPhase.PRE_RESOLUTION,
        NetworkPolicyPhase.POST_RESOLUTION,
    ]
    attempt = transport.calls[0][0]
    assert attempt.destination.address.value == "8.8.8.8"
    assert attempt.destination.url.hostname == "8.8.8.8"


@pytest.mark.asyncio
async def test_redirect_repeats_admission_resolution_and_pinned_transport() -> None:
    resolver = RecordingResolver(
        {
            "example.test": resolution("example.test", "8.8.8.8"),
            "redirect.test": resolution("redirect.test", "1.1.1.1"),
        }
    )
    transport = RecordingTransport(
        (
            transport_response(
                302,
                headers=(HttpHeader("Location", "https://redirect.test/final"),),
                body=b"",
            ),
            transport_response(200, body=b"complete"),
        )
    )
    subject, policy, _, _ = gateway(resolver=resolver, transport=transport)

    response = await subject.send(request(), context())

    assert response.body == b"complete"
    assert response.usage.request_count == 2
    assert response.usage.redirect_count == 1
    assert [call[0] for call in resolver.calls] == ["example.test", "redirect.test"]
    assert isinstance(policy, RecordingPolicy)
    assert [call.phase for call in policy.calls] == [
        NetworkPolicyPhase.PRE_RESOLUTION,
        NetworkPolicyPhase.POST_RESOLUTION,
        NetworkPolicyPhase.PRE_RESOLUTION,
        NetworkPolicyPhase.POST_RESOLUTION,
    ]
    assert [call[0].destination.address.value for call in transport.calls] == [
        "8.8.8.8",
        "1.1.1.1",
    ]


@pytest.mark.asyncio
async def test_post_resolution_policy_receives_resolver_canonical_hostname() -> None:
    resolver = RecordingResolver(
        {
            "example.test": NetworkResolution(
                hostname="example.test",
                canonical_hostname="edge.example.test",
                addresses=(ResolvedHttpAddress("8.8.8.8"),),
            )
        }
    )
    subject, policy, _, _ = gateway(resolver=resolver)

    await subject.send(request(), context())

    assert isinstance(policy, RecordingPolicy)
    assert policy.calls[0].canonical_hostname is None
    assert policy.calls[1].canonical_hostname == "edge.example.test"


@pytest.mark.asyncio
async def test_head_redirect_preserves_method_without_publishing_a_body() -> None:
    resolver = RecordingResolver(
        {
            "example.test": resolution("example.test", "8.8.8.8"),
            "redirect.test": resolution("redirect.test", "1.1.1.1"),
        }
    )
    transport = RecordingTransport(
        (
            transport_response(
                303,
                headers=(HttpHeader("Location", "https://redirect.test/final"),),
                body=b"",
            ),
            transport_response(200, body=b""),
        )
    )
    subject, _, _, _ = gateway(resolver=resolver, transport=transport)

    response = await subject.send(
        request(method=HttpMethod.HEAD),
        context(),
    )

    assert response.body == b""
    assert [attempt.method for attempt, _ in transport.calls] == [
        HttpMethod.HEAD,
        HttpMethod.HEAD,
    ]


@pytest.mark.asyncio
async def test_denied_redirect_target_is_not_resolved_or_sent() -> None:
    def decide(facts: NetworkPolicyRequest) -> NetworkPolicyDecision:
        if facts.hostname == "denied.test":
            return NetworkPolicyDecision.deny(NetworkPolicyReason.DESTINATION_NOT_ALLOWED)
        return NetworkPolicyDecision.allow()

    resolver = RecordingResolver(
        {
            "example.test": resolution("example.test", "8.8.8.8"),
            "denied.test": resolution("denied.test", "1.1.1.1"),
        }
    )
    transport = RecordingTransport(
        (
            transport_response(
                302,
                headers=(HttpHeader("Location", "https://denied.test/final"),),
                body=b"",
            ),
        )
    )
    subject, policy, _, _ = gateway(
        policy=RecordingPolicy(decide),
        resolver=resolver,
        transport=transport,
    )

    with pytest.raises(OutboundHttpDenied):
        await subject.send(request(), context())

    assert isinstance(policy, RecordingPolicy)
    assert [call.hostname for call in policy.calls] == [
        "example.test",
        "example.test",
        "denied.test",
    ]
    assert [call[0] for call in resolver.calls] == ["example.test"]
    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_redirect_body_omission_does_not_trust_or_read_declared_length() -> None:
    transport = RecordingTransport(
        (
            transport_response(
                302,
                headers=(
                    HttpHeader("Location", "/final"),
                    HttpHeader("Content-Length", "1000"),
                ),
                body=b"",
            ),
            transport_response(body=b"complete"),
        )
    )
    subject, _, _, _ = gateway(transport=transport)

    response = await subject.send(request(), context())

    assert response.body == b"complete"
    assert response.usage.request_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location",
    [
        "/" + ("a" * 9000),
        "/" + ("é" * 1400),
        "https://[::1",
    ],
    ids=["raw-limit", "canonical-expansion-limit", "malformed-authority"],
)
async def test_invalid_redirect_location_is_stable_before_another_attempt(
    location: str,
) -> None:
    transfer_limits = limits(max_response_header_bytes=20_000)
    transport = RecordingTransport(
        (
            transport_response(
                302,
                headers=(HttpHeader("Location", location),),
                body=b"",
            ),
        )
    )
    subject, _, resolver, _ = gateway(transport=transport)

    with pytest.raises(OutboundHttpResponseInvalid) as captured:
        await subject.send(
            request(transfer_limits=transfer_limits),
            context(transfer_limits),
        )

    assert len(resolver.calls) == 1
    assert len(transport.calls) == 1
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_gateway_uses_first_admitted_address_without_failure_retry() -> None:
    resolver = RecordingResolver({"example.test": resolution("example.test", "8.8.8.8", "1.1.1.1")})
    transport = RecordingTransport((OSError("provider detail"), transport_response()))
    subject, _, _, _ = gateway(resolver=resolver, transport=transport)

    with pytest.raises(OutboundHttpTransportFailed) as captured:
        await subject.send(request(), context())

    assert len(transport.calls) == 1
    assert transport.calls[0][0].destination.address.value == "8.8.8.8"
    assert "provider detail" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_resolver_failure_is_stable_and_performs_no_transport() -> None:
    resolver = RecordingResolver({"example.test": OSError("resolver detail")})
    subject, _, _, transport = gateway(resolver=resolver)

    with pytest.raises(OutboundHttpResolutionFailed) as captured:
        await subject.send(request(), context())

    assert len(resolver.calls) == 1
    assert transport.calls == []
    assert "resolver detail" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (OutboundHttpCancelled("resolver detail"), OutboundHttpCancelled),
        (OutboundHttpTimeout("resolver detail"), OutboundHttpTimeout),
        (OutboundHttpResolutionFailed("resolver detail"), OutboundHttpResolutionFailed),
    ],
)
async def test_resolver_stable_failures_preserve_category_without_details(
    failure: Exception,
    expected: type[Exception],
) -> None:
    resolver = RecordingResolver({"example.test": failure})
    subject, _, _, transport = gateway(resolver=resolver)

    with pytest.raises(expected) as captured:
        await subject.send(request(), context())

    assert transport.calls == []
    assert "resolver detail" not in str(captured.value)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "header",
    [
        HttpHeader("Host", "other.test"),
        HttpHeader("Connection", "keep-alive"),
        HttpHeader("Transfer-Encoding", "chunked"),
        HttpHeader("Content-Length", "0"),
        HttpHeader("Proxy-Authorization", "value"),
        HttpHeader("Authorization", "value"),
        HttpHeader("Cookie", "value"),
        HttpHeader("Accept-Encoding", "br"),
    ],
)
async def test_controlled_or_sensitive_request_headers_fail_before_policy(
    header: HttpHeader,
) -> None:
    subject, policy, resolver, transport = gateway()

    with pytest.raises(OutboundHttpDenied):
        await subject.send(request(headers=(header,)), context())

    assert isinstance(policy, RecordingPolicy)
    assert policy.calls == []
    assert resolver.calls == []
    assert transport.calls == []


@pytest.mark.asyncio
async def test_credential_route_fails_before_policy_until_credential_slice_exists() -> None:
    route = CredentialRouteId("docs")
    subject, policy, resolver, transport = gateway()

    with pytest.raises(OutboundHttpDenied):
        await subject.send(
            request(credential_route=route),
            context(routes=(route,)),
        )

    assert isinstance(policy, RecordingPolicy)
    assert policy.calls == []
    assert resolver.calls == []
    assert transport.calls == []


@pytest.mark.asyncio
async def test_gateway_counts_controlled_headers_against_request_header_limit() -> None:
    transfer_limits = limits(max_request_header_bytes=1)
    subject, policy, resolver, transport = gateway()

    with pytest.raises(OutboundHttpLimitExceeded):
        await subject.send(
            request(transfer_limits=transfer_limits),
            context(transfer_limits),
        )

    assert isinstance(policy, RecordingPolicy)
    assert len(policy.calls) == 2
    assert len(resolver.calls) == 1
    assert transport.calls == []


@pytest.mark.asyncio
async def test_gateway_enforces_redirect_and_request_count_without_extra_attempt() -> None:
    transfer_limits = limits(max_redirects=0, max_requests=1)
    transport = RecordingTransport(
        (
            transport_response(
                302,
                headers=(HttpHeader("Location", "/again"),),
                body=b"",
            ),
        )
    )
    subject, _, _, _ = gateway(transport=transport)

    with pytest.raises(OutboundHttpLimitExceeded):
        await subject.send(
            request(transfer_limits=transfer_limits),
            context(transfer_limits),
        )

    assert len(transport.calls) == 1


@pytest.mark.asyncio
async def test_gateway_narrows_each_attempt_to_the_remaining_encoded_body_budget() -> None:
    transfer_limits = limits(
        max_response_body_bytes=5,
        max_decompressed_response_bytes=5,
        max_transferred_bytes=20,
    )
    transport = RecordingTransport(
        (
            transport_response(
                302,
                headers=(HttpHeader("Location", "/final"),),
                body=b"1234",
            ),
            transport_response(body=b"12"),
        )
    )
    subject, _, _, _ = gateway(transport=transport)

    with pytest.raises(OutboundHttpLimitExceeded):
        await subject.send(
            request(transfer_limits=transfer_limits),
            context(transfer_limits),
        )

    assert len(transport.calls) == 2
    assert transport.calls[1][0].max_response_body_bytes == 1


@pytest.mark.asyncio
async def test_encoded_and_decompressed_limits_are_enforced_before_publication() -> None:
    compressed = gzip.compress(b"a" * 40)
    transfer_limits = limits(
        max_response_body_bytes=len(compressed),
        max_decompressed_response_bytes=40,
        max_transferred_bytes=len(compressed),
    )
    transport = RecordingTransport(
        (
            transport_response(
                headers=(
                    HttpHeader("Content-Encoding", "gzip"),
                    HttpHeader("Content-Length", str(len(compressed))),
                    HttpHeader("Content-Type", "text/plain"),
                ),
                body=compressed,
            ),
        )
    )
    subject, _, _, _ = gateway(transport=transport)

    response = await subject.send(
        request(transfer_limits=transfer_limits),
        context(transfer_limits),
    )

    assert response.body == b"a" * 40
    assert response.usage.response_bytes == len(compressed)
    assert response.usage.decompressed_response_bytes == 40
    assert response.headers == (HttpHeader("Content-Type", "text/plain"),)

    too_small = limits(
        max_response_body_bytes=len(compressed),
        max_decompressed_response_bytes=39,
        max_transferred_bytes=len(compressed),
    )
    subject, _, _, _ = gateway(
        transport=RecordingTransport(
            (
                transport_response(
                    headers=(HttpHeader("Content-Encoding", "gzip"),),
                    body=compressed,
                ),
            )
        )
    )
    with pytest.raises(OutboundHttpLimitExceeded):
        await subject.send(request(transfer_limits=too_small), context(too_small))


@pytest.mark.asyncio
async def test_malformed_content_encoding_is_a_stable_response_failure() -> None:
    transport = RecordingTransport(
        (
            transport_response(
                headers=(HttpHeader("Content-Encoding", "gzip"),),
                body=b"not-gzip",
            ),
        )
    )
    subject, _, _, _ = gateway(transport=transport)

    with pytest.raises(OutboundHttpResponseInvalid) as captured:
        await subject.send(request(), context())

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_gateway_rejects_non_decimal_content_length_from_injected_transport() -> None:
    subject, _, _, _ = gateway(
        transport=RecordingTransport(
            (
                transport_response(
                    headers=(HttpHeader("Content-Length", "+2"),),
                    body=b"ok",
                ),
            )
        )
    )

    with pytest.raises(OutboundHttpResponseInvalid):
        await subject.send(request(), context())


@pytest.mark.asyncio
async def test_gateway_rejects_pathological_content_length_without_exception_leak() -> None:
    transfer_limits = limits(max_response_header_bytes=10_000)
    subject, _, _, _ = gateway(
        transport=RecordingTransport(
            (
                transport_response(
                    headers=(HttpHeader("Content-Length", "1" * 5000),),
                    body=b"",
                ),
            )
        )
    )

    with pytest.raises(OutboundHttpResponseInvalid) as captured:
        await subject.send(
            request(transfer_limits=transfer_limits),
            context(transfer_limits),
        )

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_gateway_enforces_reported_response_wire_bytes() -> None:
    transfer_limits = limits(max_transferred_bytes=5)
    subject, _, _, _ = gateway(
        transport=RecordingTransport((transport_response(body=b"x", wire_bytes=6),))
    )

    with pytest.raises(OutboundHttpLimitExceeded):
        await subject.send(
            request(transfer_limits=transfer_limits),
            context(transfer_limits),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "status"),
    [
        (HttpMethod.HEAD, 200),
        (HttpMethod.GET, 204),
        (HttpMethod.GET, 205),
        (HttpMethod.GET, 304),
    ],
)
async def test_bodyless_response_skips_content_decoding(
    method: HttpMethod,
    status: int,
) -> None:
    subject, _, _, _ = gateway(
        transport=RecordingTransport(
            (
                transport_response(
                    status,
                    headers=(
                        HttpHeader("Content-Encoding", "gzip"),
                        HttpHeader("Content-Length", "100"),
                    ),
                    body=b"",
                ),
            )
        )
    )

    response = await subject.send(request(method=method), context())

    assert response.status_code == status
    assert response.headers == ()
    assert response.body == b""
    assert response.usage.decompressed_response_bytes == 0


@pytest.mark.asyncio
async def test_gateway_rejects_provisional_response_from_injected_transport() -> None:
    provisional = object.__new__(HttpTransportResponse)
    object.__setattr__(provisional, "status_code", 103)
    object.__setattr__(provisional, "headers", ())
    object.__setattr__(provisional, "body", b"")
    subject, _, _, _ = gateway(transport=RecordingTransport((provisional,)))

    with pytest.raises(OutboundHttpResponseInvalid):
        await subject.send(request(), context())


@pytest.mark.asyncio
async def test_sensitive_response_headers_are_not_published_or_reused() -> None:
    transport = RecordingTransport(
        (
            transport_response(
                headers=(
                    HttpHeader("Set-Cookie", "session=protected"),
                    HttpHeader("WWW-Authenticate", "Bearer protected"),
                    HttpHeader("X-Public", "value"),
                )
            ),
        )
    )
    subject, _, _, _ = gateway(transport=transport)

    response = await subject.send(request(), context())

    assert response.headers == (HttpHeader("X-Public", "value"),)
    assert all(
        header.name.lower() not in {"cookie", "authorization"}
        for attempt, _ in transport.calls
        for header in attempt.headers
    )


@pytest.mark.asyncio
async def test_gateway_honors_cooperative_cancellation_before_collaborator_use() -> None:
    cancellation = Cancellation(cancelled=True)
    subject, policy, resolver, transport = gateway()

    with pytest.raises(OutboundHttpCancelled):
        await subject.send(request(), context(cancellation=cancellation))

    assert isinstance(policy, RecordingPolicy)
    assert policy.calls == []
    assert resolver.calls == []
    assert transport.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase",
    [NetworkPolicyPhase.PRE_RESOLUTION, NetworkPolicyPhase.POST_RESOLUTION],
)
async def test_gateway_preserves_cooperative_cancellation_during_policy_evaluation(
    phase: NetworkPolicyPhase,
) -> None:
    cancellation = Cancellation()
    policy = PhaseBlockingPolicy(phase)
    subject, _, resolver, transport = gateway(policy=policy)
    sending = asyncio.create_task(subject.send(request(), context(cancellation=cancellation)))
    await asyncio.wait_for(policy.entered.wait(), timeout=1)

    cancellation.cancelled = True
    policy.release.set()

    with pytest.raises(OutboundHttpCancelled) as captured:
        await sending

    expected_phases = (
        (NetworkPolicyPhase.PRE_RESOLUTION,)
        if phase is NetworkPolicyPhase.PRE_RESOLUTION
        else (
            NetworkPolicyPhase.PRE_RESOLUTION,
            NetworkPolicyPhase.POST_RESOLUTION,
        )
    )
    assert tuple(call.phase for call in policy.calls) == expected_phases
    assert len(resolver.calls) == (0 if phase is NetworkPolicyPhase.PRE_RESOLUTION else 1)
    assert transport.calls == []
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_gateway_does_not_create_collaborator_after_entry_cancellation() -> None:
    cancellation = CheckCountCancellation(cancel_on_check=4)
    policy = CoroutineTrackingPolicy()
    subject, _, resolver, transport = gateway(policy=policy)

    with pytest.raises(OutboundHttpCancelled):
        await subject.send(
            request(),
            context(cancellation=cancellation),
        )

    assert cancellation.check_count == 4
    assert policy.calls == 0
    assert policy.operation is None
    assert resolver.calls == []
    assert transport.calls == []


@pytest.mark.asyncio
async def test_gateway_enforces_effective_deadline_and_removes_timeout_context() -> None:
    policy = BlockingPolicy()
    deadline = asyncio.get_running_loop().time() + 0.02
    subject, _, resolver, transport = gateway(policy=policy)

    with pytest.raises(OutboundHttpTimeout) as captured:
        await subject.send(request(), context(deadline=deadline))

    assert policy.entered.is_set()
    assert resolver.calls == []
    assert transport.calls == []
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None


@pytest.mark.asyncio
async def test_gateway_deadline_detaches_cancellation_resistant_policy() -> None:
    policy = CancellationResistantPolicy()
    deadline = asyncio.get_running_loop().time() + 0.02
    subject, _, resolver, transport = gateway(policy=policy)
    task = asyncio.create_task(subject.send(request(), context(deadline=deadline)))

    await asyncio.sleep(0.08)
    completed_by_deadline = task.done()
    policy.release.set()
    with pytest.raises(OutboundHttpTimeout):
        await task
    await asyncio.sleep(0)

    assert completed_by_deadline
    assert policy.entered.is_set()
    assert policy.cancelled.is_set()
    assert resolver.calls == []
    assert transport.calls == []


@pytest.mark.asyncio
async def test_policy_failure_and_invalid_decision_fail_closed_before_dns() -> None:
    for policy in (
        RecordingPolicy(RuntimeError("policy detail")),
        RecordingPolicy(lambda _: cast(NetworkPolicyDecision, object())),
    ):
        subject, _, resolver, transport = gateway(policy=policy)

        with pytest.raises(OutboundHttpDenied) as captured:
            await subject.send(request(), context())

        assert "policy detail" not in str(captured.value)
        assert captured.value.__cause__ is None
        assert captured.value.__context__ is None
        assert resolver.calls == []
        assert transport.calls == []


@pytest.mark.asyncio
async def test_invalid_transport_value_and_head_body_fail_as_response_errors() -> None:
    invalid_transport = RecordingTransport((object(),))
    subject, _, _, _ = gateway(transport=invalid_transport)
    with pytest.raises(OutboundHttpResponseInvalid):
        await subject.send(request(), context())

    head_transport = RecordingTransport((transport_response(body=b"unexpected"),))
    subject, _, _, _ = gateway(transport=head_transport)
    with pytest.raises(OutboundHttpResponseInvalid):
        await subject.send(
            request(method=HttpMethod.HEAD),
            context(),
        )
