from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta

import pytest

from mem_sandbox.core import (
    OperationKind,
    OperationLimits,
    SystemClock,
    SystemUuidGenerator,
)
from mem_sandbox.events import EventQuery, InMemoryEventSink, SandboxEventType
from mem_sandbox.network import (
    HttpMethod,
    HttpScheme,
    HttpTransferLimits,
    HttpTransferUsage,
    NetworkPolicyId,
    OutboundHttpBinding,
    OutboundHttpDenied,
    OutboundHttpGatewayFailed,
    OutboundHttpGrant,
    OutboundHttpRequest,
    OutboundHttpResponse,
    OutboundHttpUnavailable,
)
from mem_sandbox.network.testing import FakeOutboundHttpGateway
from mem_sandbox.policy import AllowAllPolicyEngine, PolicyDecision, PolicyRequest
from mem_sandbox.secrets import NoSecretBroker
from mem_sandbox.service import (
    ConnectedSandboxProfile,
    CreateSandboxRequest,
    DefaultSessionFactory,
    InMemorySandboxService,
    InMemoryServiceSnapshotGateway,
    InvalidSandboxRequest,
    OwnerId,
    ResumeSandboxRequest,
    SandboxOptions,
)
from mem_sandbox.session import (
    CreateSnapshotRequest,
    SandboxSession,
    SendHttpRequest,
    SessionOperationCancelled,
    SessionOperationTimeout,
    SessionPolicyDenied,
)
from mem_sandbox.snapshots import (
    InMemorySnapshotStore,
    JsonSessionSnapshotCodec,
    SnapshotStoreLimits,
)


class Cancellation:
    def __init__(self) -> None:
        self.cancelled = False

    def is_set(self) -> bool:
        return self.cancelled


class DenyHttpPolicy:
    async def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        return PolicyDecision(
            allowed=request.operation_kind is not OperationKind.OUTBOUND_HTTP,
            reason_code="http_denied",
            effective_limits=request.requested_limits,
        )


@dataclass(frozen=True, slots=True)
class Bundle:
    service: InMemorySandboxService
    gateway: FakeOutboundHttpGateway
    events: InMemoryEventSink
    snapshots: InMemoryServiceSnapshotGateway


def transfer_limits(**changes: int | float) -> HttpTransferLimits:
    values: dict[str, int | float] = {
        "timeout_seconds": 5.0,
        "max_request_header_bytes": 1024,
        "max_request_body_bytes": 0,
        "max_response_header_bytes": 1024,
        "max_response_body_bytes": 1024,
        "max_decompressed_response_bytes": 2048,
        "max_redirects": 2,
        "max_requests": 4,
        "max_transferred_bytes": 4096,
    }
    values.update(changes)
    return HttpTransferLimits(**values)  # type: ignore[arg-type]


def grant(
    *,
    methods: tuple[HttpMethod, ...] = (HttpMethod.GET, HttpMethod.HEAD),
    schemes: tuple[HttpScheme, ...] = (HttpScheme.HTTPS,),
    limits: HttpTransferLimits | None = None,
) -> OutboundHttpGrant:
    return OutboundHttpGrant(
        policy_id=NetworkPolicyId("public-fixture"),
        methods=methods,
        schemes=schemes,
        limits=limits or transfer_limits(),
    )


def response(body: bytes = b"value") -> OutboundHttpResponse:
    return OutboundHttpResponse(
        status_code=200,
        headers=(),
        body=body,
        usage=HttpTransferUsage(
            request_count=1,
            request_bytes=0,
            response_bytes=len(body),
            decompressed_response_bytes=len(body),
            redirect_count=0,
            duration_ms=1,
        ),
    )


def http_request(
    *,
    method: HttpMethod = HttpMethod.GET,
    url: str = "https://example.test/value",
    limits: HttpTransferLimits | None = None,
    operation_limits: OperationLimits | None = None,
    cancellation: Cancellation | None = None,
) -> SendHttpRequest:
    return SendHttpRequest(
        request=OutboundHttpRequest(
            method=method,
            url=url,
            limits=limits or transfer_limits(),
        ),
        limits=operation_limits or OperationLimits(),
        cancellation=cancellation,
    )


def bundle(
    outcomes: tuple[OutboundHttpResponse | BaseException, ...],
    *,
    ceiling: OutboundHttpGrant | None = None,
    policy: AllowAllPolicyEngine | DenyHttpPolicy | None = None,
    release: asyncio.Event | None = None,
    configure_gateway: bool = True,
) -> Bundle:
    clock = SystemClock()
    uuids = SystemUuidGenerator()
    codec = JsonSessionSnapshotCodec()
    store = InMemorySnapshotStore(
        default_ttl=timedelta(days=1),
        limits=SnapshotStoreLimits(
            max_snapshots=20,
            max_total_payload_bytes=64 * 1024 * 1024,
        ),
        clock=clock,
    )
    snapshots = InMemoryServiceSnapshotGateway(store)
    events = InMemoryEventSink(max_events=500, max_payload_bytes=4 * 1024 * 1024)
    gateway = FakeOutboundHttpGateway(outcomes, release=release)
    factory = DefaultSessionFactory(
        policy_engine=policy or AllowAllPolicyEngine(),
        secret_broker=NoSecretBroker(),
        event_sink=events,
        snapshot_codec=codec,
        clock=clock,
        uuid_generator=uuids,
        outbound_http=(
            OutboundHttpBinding(
                gateway=gateway,
                grant=ceiling or grant(),
            )
            if configure_gateway
            else None
        ),
    )
    service = InMemorySandboxService(
        session_factory=factory,
        snapshot_gateway=snapshots,
        snapshot_decoder=codec,
        clock=clock,
        uuid_generator=uuids,
    )
    return Bundle(service, gateway, events, snapshots)


async def connected_session(
    configured: Bundle,
    *,
    requested: OutboundHttpGrant | None = None,
) -> SandboxSession:
    handle = await configured.service.create(
        CreateSandboxRequest(
            owner_id=OwnerId("owner"),
            options=SandboxOptions(
                profile=ConnectedSandboxProfile(
                    outbound_http=requested or grant(),
                )
            ),
        )
    )
    return await configured.service.get_session(handle)


@pytest.mark.asyncio
async def test_virtual_profile_rejects_before_gateway_and_connected_profile_round_trips() -> None:
    configured = bundle((response(),))
    virtual_handle = await configured.service.create(
        CreateSandboxRequest(owner_id=OwnerId("virtual"))
    )
    virtual = await configured.service.get_session(virtual_handle)

    with pytest.raises(OutboundHttpUnavailable):
        await virtual.send_http(http_request())
    assert configured.gateway.calls == ()

    connected = await connected_session(configured)
    result = await connected.send_http(http_request())

    assert result.response.body == b"value"
    assert result.metadata.workspace_revision.value == 0
    assert configured.gateway.calls[0].context.grant == grant()
    events = await configured.events.query(EventQuery(session_id=connected.session_id))
    assert [
        event.event_type for event in events if event.operation_kind is OperationKind.OUTBOUND_HTTP
    ] == [
        SandboxEventType.OPERATION_STARTED,
        SandboxEventType.OPERATION_COMPLETED,
    ]
    await configured.service.close()


@pytest.mark.asyncio
async def test_grant_and_policy_denials_precede_gateway_use() -> None:
    configured = bundle((response(),), policy=DenyHttpPolicy())
    connected = await connected_session(
        configured,
        requested=grant(methods=(HttpMethod.GET,)),
    )

    with pytest.raises(OutboundHttpDenied):
        await connected.send_http(http_request(method=HttpMethod.HEAD))
    with pytest.raises(OutboundHttpDenied):
        await connected.send_http(http_request(url="http://example.test/value"))
    with pytest.raises(SessionPolicyDenied, match="http_denied"):
        await connected.send_http(http_request())

    assert configured.gateway.calls == ()
    await configured.service.close()


@pytest.mark.asyncio
async def test_connected_profile_requires_host_binding_before_session_publication() -> None:
    configured = bundle((), configure_gateway=False)

    with pytest.raises(InvalidSandboxRequest, match="configured outbound HTTP binding"):
        await connected_session(configured)

    assert configured.gateway.calls == ()
    await configured.service.close()


@pytest.mark.asyncio
async def test_unexpected_gateway_failure_is_translated_by_session_boundary() -> None:
    configured = bundle((RuntimeError("provider secret"),))
    connected = await connected_session(configured)

    with pytest.raises(
        OutboundHttpGatewayFailed,
        match="gateway failed unexpectedly",
    ) as captured:
        await connected.send_http(http_request())

    assert "provider secret" not in str(captured.value)
    assert captured.value.__cause__ is None
    await configured.service.close()


@pytest.mark.asyncio
async def test_timeout_and_caller_cancellation_cancel_blocked_gateway() -> None:
    timeout_release = asyncio.Event()
    timed = bundle((response(),), release=timeout_release)
    timed_session = await connected_session(timed)

    with pytest.raises(SessionOperationTimeout):
        await timed_session.send_http(
            http_request(
                operation_limits=OperationLimits(
                    timeout_seconds=0.05,
                    terminal_event_reserve_seconds=0.01,
                )
            )
        )
    await timed.service.close()

    cancellation_release = asyncio.Event()
    cancelled = bundle((response(),), release=cancellation_release)
    cancelled_session = await connected_session(cancelled)
    signal = Cancellation()
    sending = asyncio.create_task(cancelled_session.send_http(http_request(cancellation=signal)))
    await cancelled.gateway.entered.wait()
    signal.cancelled = True

    with pytest.raises(SessionOperationCancelled):
        await sending
    await cancelled.service.close()


@pytest.mark.asyncio
async def test_http_transfer_timeout_caps_a_larger_session_budget() -> None:
    release = asyncio.Event()
    configured = bundle((response(),), release=release)
    connected = await connected_session(configured)

    with pytest.raises(SessionOperationTimeout):
        await connected.send_http(
            http_request(
                limits=transfer_limits(timeout_seconds=0.05),
                operation_limits=OperationLimits(
                    timeout_seconds=1,
                    terminal_event_reserve_seconds=0.1,
                ),
            )
        )

    await configured.service.close()


@pytest.mark.asyncio
async def test_session_close_cancels_active_http_without_closing_shared_gateway() -> None:
    release = asyncio.Event()
    configured = bundle((response(),), release=release)
    handle = await configured.service.create(
        CreateSandboxRequest(
            owner_id=OwnerId("owner"),
            options=SandboxOptions(profile=ConnectedSandboxProfile(outbound_http=grant())),
        )
    )
    session = await configured.service.get_session(handle)
    sending = asyncio.create_task(session.send_http(http_request()))
    await configured.gateway.entered.wait()

    deleting = asyncio.create_task(configured.service.delete(handle))

    with pytest.raises(SessionOperationCancelled):
        await sending
    await deleting
    assert configured.gateway.close_count == 0
    await configured.service.close()


@pytest.mark.asyncio
async def test_resume_uses_current_host_profile_and_never_snapshot_authority() -> None:
    configured = bundle((response(b"resumed"),))
    source = await connected_session(configured)
    snapshot_result = await source.create_snapshot(CreateSnapshotRequest())
    persisted = await configured.snapshots.load(snapshot_result.snapshot_ref)

    assert b"public-fixture" not in persisted.payload
    assert b"outbound_http" not in persisted.payload

    virtual_handle = await configured.service.resume(
        ResumeSandboxRequest(
            owner_id=OwnerId("virtual-resume"),
            snapshot_ref=snapshot_result.snapshot_ref,
        )
    )
    virtual = await configured.service.get_session(virtual_handle)
    with pytest.raises(OutboundHttpUnavailable):
        await virtual.send_http(http_request())

    narrower = grant(
        methods=(HttpMethod.GET,),
        limits=transfer_limits(
            timeout_seconds=2.0,
            max_response_body_bytes=512,
            max_decompressed_response_bytes=1024,
            max_requests=2,
            max_transferred_bytes=2048,
        ),
    )
    resumed_handle = await configured.service.resume(
        ResumeSandboxRequest(
            owner_id=OwnerId("connected-resume"),
            snapshot_ref=snapshot_result.snapshot_ref,
            options=SandboxOptions(profile=ConnectedSandboxProfile(outbound_http=narrower)),
        )
    )
    resumed = await configured.service.get_session(resumed_handle)
    result = await resumed.send_http(http_request(limits=narrower.limits))
    assert result.response.body == b"resumed"

    wider = grant(schemes=(HttpScheme.HTTP, HttpScheme.HTTPS))
    with pytest.raises(InvalidSandboxRequest, match="grant ceiling"):
        await configured.service.resume(
            ResumeSandboxRequest(
                owner_id=OwnerId("wider"),
                snapshot_ref=snapshot_result.snapshot_ref,
                options=SandboxOptions(profile=ConnectedSandboxProfile(outbound_http=wider)),
            )
        )
    assert len(configured.gateway.calls) == 1
    await configured.service.close()
