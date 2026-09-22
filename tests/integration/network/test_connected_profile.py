from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
from typing import cast

import pytest

from mem_sandbox.core import (
    OperationId,
    OperationKind,
    OperationLimits,
    SessionId,
    SystemClock,
    SystemUuidGenerator,
)
from mem_sandbox.events import EventQuery, InMemoryEventSink, SandboxEventType
from mem_sandbox.network import (
    HttpMethod,
    HttpScheme,
    HttpTransferLimits,
    HttpTransferUsage,
    NetworkOperationContext,
    NetworkPolicyId,
    OutboundHttpBinding,
    OutboundHttpCancelled,
    OutboundHttpDenied,
    OutboundHttpGatewayFailed,
    OutboundHttpGrant,
    OutboundHttpLimitExceeded,
    OutboundHttpRequest,
    OutboundHttpRequestInvalid,
    OutboundHttpResolutionFailed,
    OutboundHttpResponse,
    OutboundHttpResponseInvalid,
    OutboundHttpTimeout,
    OutboundHttpTransportFailed,
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
    SandboxSessionState,
    SendHttpRequest,
    SessionFailed,
    SessionOperationCancelled,
    SessionOperationTimeout,
    SessionPolicyDenied,
)
from mem_sandbox.session.session import (
    _send_outbound_http_safely,  # pyright: ignore[reportPrivateUsage]
    _settle_cancelled_task,  # pyright: ignore[reportPrivateUsage]
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


class ProviderAbort(BaseException):
    pass


class BaseExceptionGroupGateway(FakeOutboundHttpGateway):
    def __init__(self, message: str) -> None:
        super().__init__(())
        self._message = message

    async def send(
        self,
        request: OutboundHttpRequest,
        context: NetworkOperationContext,
    ) -> OutboundHttpResponse:
        self.entered.set()

        async def abort() -> None:
            raise ProviderAbort(self._message)

        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(abort())
        raise AssertionError("task group returned after a provider failure")


class CancellationCleanupFailureGateway(FakeOutboundHttpGateway):
    def __init__(self, message: str) -> None:
        super().__init__(())
        self._message = message

    async def send(
        self,
        request: OutboundHttpRequest,
        context: NetworkOperationContext,
    ) -> OutboundHttpResponse:
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise RuntimeError(self._message) from None
        raise AssertionError("blocking gateway returned without cancellation")


class FatalCancellationCleanupGateway(FakeOutboundHttpGateway):
    def __init__(self, failure: BaseException) -> None:
        super().__init__(())
        self._failure = failure

    async def send(
        self,
        request: OutboundHttpRequest,
        context: NetworkOperationContext,
    ) -> OutboundHttpResponse:
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            raise self._failure from None
        raise AssertionError("blocking gateway returned without cancellation")


class SelfCancellingGateway(FakeOutboundHttpGateway):
    def __init__(self) -> None:
        super().__init__(())

    async def send(
        self,
        request: OutboundHttpRequest,
        context: NetworkOperationContext,
    ) -> OutboundHttpResponse:
        self.entered.set()
        current = asyncio.current_task()
        assert current is not None
        current.cancel()
        await asyncio.sleep(0)
        raise AssertionError("self-cancelling gateway returned without cancellation")


class CancellationObservingGateway(FakeOutboundHttpGateway):
    def __init__(self) -> None:
        super().__init__(())
        self.cancelled = asyncio.Event()
        self.finished = asyncio.Event()

    async def send(
        self,
        request: OutboundHttpRequest,
        context: NetworkOperationContext,
    ) -> OutboundHttpResponse:
        self.entered.set()
        try:
            await asyncio.Event().wait()
            raise AssertionError("blocking gateway returned without cancellation")
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            self.finished.set()


class CancellationSuppressingGateway(FakeOutboundHttpGateway):
    def __init__(self, outcome: OutboundHttpResponse) -> None:
        super().__init__(())
        self._outcome = outcome
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.cancel_count = 0

    async def send(
        self,
        request: OutboundHttpRequest,
        context: NetworkOperationContext,
    ) -> OutboundHttpResponse:
        self.entered.set()
        try:
            while not self.release.is_set():
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancel_count += 1
                    self.cancelled.set()
                    continue
            return self._outcome
        finally:
            self.finished.set()


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
    gateway_override: FakeOutboundHttpGateway | None = None,
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
    gateway = gateway_override or FakeOutboundHttpGateway(outcomes, release=release)
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


@pytest.mark.parametrize(
    "provider_failure",
    [
        RuntimeError("provider secret"),
        SessionFailed("provider secret"),
        SessionOperationTimeout("provider secret"),
        SessionOperationCancelled("provider secret"),
        asyncio.CancelledError("provider secret"),
        ProviderAbort("provider secret"),
    ],
    ids=[
        "ordinary",
        "unrelated-domain",
        "forged-session-timeout",
        "forged-session-cancellation",
        "self-cancelled",
        "fatal",
    ],
)
@pytest.mark.asyncio
async def test_unexpected_gateway_failure_is_translated_by_session_boundary(
    provider_failure: BaseException,
) -> None:
    configured = bundle((provider_failure,))
    connected = await connected_session(configured)

    with pytest.raises(
        OutboundHttpGatewayFailed,
        match="gateway failed unexpectedly",
    ) as captured:
        await connected.send_http(http_request())

    _assert_exception_graph_hides(captured.value, "provider secret")
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    events = await configured.events.query(EventQuery(session_id=connected.session_id))
    assert [
        event.event_type for event in events if event.operation_kind is OperationKind.OUTBOUND_HTTP
    ] == [
        SandboxEventType.OPERATION_STARTED,
        SandboxEventType.OPERATION_FAILED,
    ]
    await configured.service.close()


@pytest.mark.asyncio
async def test_gateway_generator_exit_is_translated_by_session_boundary() -> None:
    canary = "provider generator exit secret"
    provider_exit = GeneratorExit(canary)
    configured = bundle((provider_exit,))
    connected = await connected_session(configured)

    with pytest.raises(
        OutboundHttpGatewayFailed,
        match="gateway failed unexpectedly",
    ) as captured:
        await connected.send_http(http_request())

    _assert_exception_graph_hides(captured.value, canary)
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert connected.state is SandboxSessionState.RUNNING
    events = await configured.events.query(EventQuery(session_id=connected.session_id))
    assert [
        event.event_type for event in events if event.operation_kind is OperationKind.OUTBOUND_HTTP
    ] == [
        SandboxEventType.OPERATION_STARTED,
        SandboxEventType.OPERATION_FAILED,
    ]
    await configured.service.close()


@pytest.mark.asyncio
async def test_gateway_task_group_base_exception_is_translated_by_session_boundary() -> None:
    gateway = BaseExceptionGroupGateway("provider group secret")
    configured = bundle((), gateway_override=gateway)
    connected = await connected_session(configured)

    with pytest.raises(
        OutboundHttpGatewayFailed,
        match="gateway failed unexpectedly",
    ) as captured:
        await connected.send_http(http_request())

    _assert_exception_graph_hides(captured.value, "provider group secret")
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    events = await configured.events.query(EventQuery(session_id=connected.session_id))
    assert [
        event.event_type for event in events if event.operation_kind is OperationKind.OUTBOUND_HTTP
    ] == [
        SandboxEventType.OPERATION_STARTED,
        SandboxEventType.OPERATION_FAILED,
    ]
    await configured.service.close()


@pytest.mark.asyncio
async def test_gateway_task_self_cancellation_is_a_stable_gateway_failure() -> None:
    gateway = SelfCancellingGateway()
    configured = bundle((), gateway_override=gateway)
    connected = await connected_session(configured)

    with pytest.raises(
        OutboundHttpGatewayFailed,
        match="gateway failed unexpectedly",
    ) as captured:
        await connected.send_http(http_request())

    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    current = asyncio.current_task()
    assert current is not None
    assert current.cancelling() == 0
    assert connected.state is SandboxSessionState.RUNNING
    events = await configured.events.query(EventQuery(session_id=connected.session_id))
    assert [
        event.event_type for event in events if event.operation_kind is OperationKind.OUTBOUND_HTTP
    ] == [
        SandboxEventType.OPERATION_STARTED,
        SandboxEventType.OPERATION_FAILED,
    ]
    await configured.service.close()


@pytest.mark.asyncio
async def test_gateway_domain_cancellation_has_no_false_secondary_failure() -> None:
    configured = bundle((OutboundHttpCancelled("provider secret"),))
    connected = await connected_session(configured)

    with pytest.raises(SessionOperationCancelled) as captured:
        await connected.send_http(http_request())

    _assert_exception_graph_hides(captured.value, "provider secret")
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert _exception_graph_notes(captured.value) == ()
    events = await configured.events.query(EventQuery(session_id=connected.session_id))
    assert [
        event.event_type for event in events if event.operation_kind is OperationKind.OUTBOUND_HTTP
    ] == [
        SandboxEventType.OPERATION_STARTED,
        SandboxEventType.OPERATION_CANCELLED,
    ]
    await configured.service.close()


@pytest.mark.parametrize(
    ("provider_failure", "expected_type"),
    [
        (OutboundHttpDenied("provider secret"), OutboundHttpDenied),
        (OutboundHttpLimitExceeded("provider secret"), OutboundHttpLimitExceeded),
        (OutboundHttpRequestInvalid("provider secret"), OutboundHttpRequestInvalid),
        (OutboundHttpResolutionFailed("provider secret"), OutboundHttpResolutionFailed),
        (OutboundHttpResponseInvalid("provider secret"), OutboundHttpResponseInvalid),
        (OutboundHttpTimeout("provider secret"), OutboundHttpTimeout),
        (OutboundHttpTransportFailed("provider secret"), OutboundHttpTransportFailed),
        (OutboundHttpGatewayFailed("provider secret"), OutboundHttpGatewayFailed),
    ],
)
@pytest.mark.asyncio
async def test_supported_gateway_failures_keep_stable_types_without_provider_details(
    provider_failure: BaseException,
    expected_type: type[BaseException],
) -> None:
    configured = bundle((provider_failure,))
    connected = await connected_session(configured)

    with pytest.raises(expected_type) as captured:
        await connected.send_http(http_request())

    _assert_exception_graph_hides(captured.value, "provider secret")
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    await configured.service.close()


@pytest.mark.asyncio
async def test_gateway_cleanup_failure_is_hidden_during_timeout_settlement() -> None:
    gateway = CancellationCleanupFailureGateway("provider cleanup secret")
    configured = bundle((), gateway_override=gateway)
    connected = await connected_session(configured)

    with pytest.raises(SessionOperationTimeout) as captured:
        await connected.send_http(
            http_request(
                operation_limits=OperationLimits(
                    timeout_seconds=0.05,
                    terminal_event_reserve_seconds=0.01,
                )
            )
        )

    _assert_exception_graph_hides(captured.value, "provider cleanup secret")
    assert "collaborator also failed during timeout" in _exception_graph_notes(captured.value)
    await configured.service.close()


@pytest.mark.parametrize("grouped", (False, True), ids=("fatal", "fatal-group"))
@pytest.mark.asyncio
async def test_gateway_fatal_cleanup_is_hidden_during_timeout_settlement(
    grouped: bool,
) -> None:
    canary = "provider fatal cleanup secret"
    failure: BaseException = ProviderAbort(canary)
    if grouped:
        failure = BaseExceptionGroup("provider cleanup group", (failure,))
    gateway = FatalCancellationCleanupGateway(failure)
    configured = bundle((), gateway_override=gateway)
    connected = await connected_session(configured)

    with pytest.raises(SessionOperationTimeout) as captured:
        await connected.send_http(
            http_request(
                operation_limits=OperationLimits(
                    timeout_seconds=0.2,
                    terminal_event_reserve_seconds=0.1,
                )
            )
        )

    _assert_exception_graph_hides(captured.value, canary)
    assert "collaborator also failed during timeout" in _exception_graph_notes(captured.value)
    assert connected.state is SandboxSessionState.RUNNING
    events = await configured.events.query(EventQuery(session_id=connected.session_id))
    assert [
        event.event_type for event in events if event.operation_kind is OperationKind.OUTBOUND_HTTP
    ] == [
        SandboxEventType.OPERATION_STARTED,
        SandboxEventType.OPERATION_TIMED_OUT,
    ]
    await configured.service.close()


@pytest.mark.asyncio
async def test_gateway_generator_exit_is_hidden_during_timeout_settlement() -> None:
    canary = "provider generator exit secret"
    provider_exit = GeneratorExit(canary)
    gateway = FatalCancellationCleanupGateway(provider_exit)
    configured = bundle((), gateway_override=gateway)
    connected = await connected_session(configured)

    with pytest.raises(SessionOperationTimeout) as captured:
        await connected.send_http(
            http_request(
                operation_limits=OperationLimits(
                    timeout_seconds=0.2,
                    terminal_event_reserve_seconds=0.1,
                )
            )
        )

    _assert_exception_graph_hides(captured.value, canary)
    assert "collaborator also failed during timeout" in _exception_graph_notes(captured.value)
    assert connected.state is SandboxSessionState.RUNNING
    events = await configured.events.query(EventQuery(session_id=connected.session_id))
    assert [
        event.event_type for event in events if event.operation_kind is OperationKind.OUTBOUND_HTTP
    ] == [
        SandboxEventType.OPERATION_STARTED,
        SandboxEventType.OPERATION_TIMED_OUT,
    ]
    await configured.service.close()


@pytest.mark.parametrize(
    "failure",
    (
        ProviderAbort("provider fatal cleanup secret"),
        BaseExceptionGroup(
            "provider cleanup group",
            (ProviderAbort("provider fatal cleanup secret"),),
        ),
        GeneratorExit("provider fatal cleanup secret"),
    ),
    ids=("fatal", "fatal-group", "generator-exit"),
)
@pytest.mark.asyncio
async def test_gateway_fatal_cleanup_does_not_replace_native_cancellation(
    failure: BaseException,
) -> None:
    canary = "provider fatal cleanup secret"
    gateway = FatalCancellationCleanupGateway(failure)
    configured = bundle((), gateway_override=gateway)
    connected = await connected_session(configured)
    sending = asyncio.create_task(connected.send_http(http_request()))
    await gateway.entered.wait()

    sending.cancel()

    with pytest.raises(asyncio.CancelledError) as captured:
        await sending
    assert sending.cancelled()
    _assert_exception_graph_hides(captured.value, canary)
    assert "collaborator also failed during cancellation" in _exception_graph_notes(captured.value)
    events = await configured.events.query(EventQuery(session_id=connected.session_id))
    assert [
        event.event_type for event in events if event.operation_kind is OperationKind.OUTBOUND_HTTP
    ] == [
        SandboxEventType.OPERATION_STARTED,
        SandboxEventType.OPERATION_CANCELLED,
    ]
    await configured.service.close()


@pytest.mark.asyncio
async def test_gateway_generator_exit_does_not_replace_cooperative_cancellation() -> None:
    canary = "provider generator exit secret"
    gateway = FatalCancellationCleanupGateway(GeneratorExit(canary))
    configured = bundle((), gateway_override=gateway)
    connected = await connected_session(configured)
    signal = Cancellation()
    sending = asyncio.create_task(connected.send_http(http_request(cancellation=signal)))
    await gateway.entered.wait()

    signal.cancelled = True

    with pytest.raises(SessionOperationCancelled) as captured:
        await sending
    _assert_exception_graph_hides(captured.value, canary)
    assert "collaborator also failed during cancellation" in _exception_graph_notes(captured.value)
    assert connected.state is SandboxSessionState.RUNNING
    events = await configured.events.query(EventQuery(session_id=connected.session_id))
    assert [
        event.event_type for event in events if event.operation_kind is OperationKind.OUTBOUND_HTTP
    ] == [
        SandboxEventType.OPERATION_STARTED,
        SandboxEventType.OPERATION_CANCELLED,
    ]
    await configured.service.close()


@pytest.mark.asyncio
async def test_gateway_generator_exit_does_not_replace_close_cancellation() -> None:
    canary = "provider generator exit secret"
    gateway = FatalCancellationCleanupGateway(GeneratorExit(canary))
    configured = bundle((), gateway_override=gateway)
    connected = await connected_session(configured)
    sending = asyncio.create_task(connected.send_http(http_request()))
    closing: asyncio.Task[None] | None = None
    await gateway.entered.wait()

    try:
        closing = asyncio.create_task(configured.service.close())

        with pytest.raises(SessionOperationCancelled) as captured:
            await sending
        _assert_exception_graph_hides(captured.value, canary)
        assert "collaborator also failed during cancellation" in _exception_graph_notes(
            captured.value
        )
        await closing
        events = await configured.events.query(EventQuery(session_id=connected.session_id))
        assert [
            event.event_type
            for event in events
            if event.operation_kind is OperationKind.OUTBOUND_HTTP
        ] == [
            SandboxEventType.OPERATION_STARTED,
            SandboxEventType.OPERATION_CANCELLED,
        ]
    finally:
        await asyncio.gather(sending, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await configured.service.close()


@pytest.mark.asyncio
async def test_gateway_generator_exit_does_not_replace_wait_for_timeout() -> None:
    canary = "provider generator exit secret"
    gateway = FatalCancellationCleanupGateway(GeneratorExit(canary))
    configured = bundle((), gateway_override=gateway)
    connected = await connected_session(configured)

    with pytest.raises(TimeoutError) as captured:
        await asyncio.wait_for(connected.send_http(http_request()), timeout=0.05)

    _assert_exception_graph_hides(captured.value, canary)
    assert connected.state is SandboxSessionState.RUNNING
    events = await configured.events.query(EventQuery(session_id=connected.session_id))
    assert [
        event.event_type for event in events if event.operation_kind is OperationKind.OUTBOUND_HTTP
    ] == [
        SandboxEventType.OPERATION_STARTED,
        SandboxEventType.OPERATION_CANCELLED,
    ]
    await configured.service.close()


@pytest.mark.asyncio
async def test_gateway_generator_exit_does_not_join_task_group_failure() -> None:
    canary = "provider generator exit secret"
    gateway = FatalCancellationCleanupGateway(GeneratorExit(canary))
    configured = bundle((), gateway_override=gateway)
    connected = await connected_session(configured)
    sending: asyncio.Task[object] | None = None

    async def fail_sibling() -> None:
        await gateway.entered.wait()
        raise RuntimeError("sibling failed")

    try:
        with pytest.raises(BaseExceptionGroup) as captured:
            async with asyncio.TaskGroup() as tasks:
                sending = tasks.create_task(connected.send_http(http_request()))
                tasks.create_task(fail_sibling())

        assert type(captured.value) is ExceptionGroup
        assert len(captured.value.exceptions) == 1
        assert isinstance(captured.value.exceptions[0], RuntimeError)
        assert str(captured.value.exceptions[0]) == "sibling failed"
        _assert_exception_graph_hides(captured.value, canary)
        assert sending is not None
        assert sending.cancelled()
        events = await configured.events.query(EventQuery(session_id=connected.session_id))
        assert [
            event.event_type
            for event in events
            if event.operation_kind is OperationKind.OUTBOUND_HTTP
        ] == [
            SandboxEventType.OPERATION_STARTED,
            SandboxEventType.OPERATION_CANCELLED,
        ]
    finally:
        await configured.service.close()


@pytest.mark.asyncio
async def test_generator_exit_injected_into_session_helpers_remains_interpreter_control() -> None:
    gateway = CancellationSuppressingGateway(response())
    effective_grant = grant()
    binding = OutboundHttpBinding(gateway=gateway, grant=effective_grant)
    uuids = SystemUuidGenerator()
    context = NetworkOperationContext(
        session_id=SessionId(uuids.new_uuid()),
        operation_id=OperationId(uuids.new_uuid()),
        grant=effective_grant,
        deadline_monotonic=asyncio.get_running_loop().time() + 1,
    )
    before = asyncio.all_tasks()
    sending = _send_outbound_http_safely(
        binding,
        http_request().request,
        context,
    )
    sending.send(None)
    provider_tasks = asyncio.all_tasks() - before
    assert len(provider_tasks) == 1
    provider_task = provider_tasks.pop()
    await gateway.entered.wait()

    try:
        sending.close()
    finally:
        gateway.release.set()
        await asyncio.wait_for(gateway.finished.wait(), timeout=0.3)
        await provider_task

    pending_task = asyncio.create_task(asyncio.Event().wait())
    settlement = _settle_cancelled_task(pending_task)
    settlement.send(None)
    try:
        settlement.close()
    finally:
        pending_task.cancel()
        await asyncio.gather(pending_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_gateway_that_suppresses_cancellation_cannot_block_timeout_or_close() -> None:
    gateway = CancellationSuppressingGateway(response())
    configured = bundle((), gateway_override=gateway)
    connected = await connected_session(configured)
    sending = asyncio.create_task(
        connected.send_http(
            http_request(
                operation_limits=OperationLimits(
                    timeout_seconds=0.05,
                    terminal_event_reserve_seconds=0.01,
                )
            )
        )
    )
    closing: asyncio.Task[None] | None = None
    await gateway.entered.wait()
    try:
        done, _ = await asyncio.wait((sending,), timeout=0.3)
        assert sending in done
        with pytest.raises(SessionOperationTimeout) as captured:
            await sending
        assert "collaborator also failed during timeout" in _exception_graph_notes(captured.value)
        assert connected.state is SandboxSessionState.FAILED
        events = await configured.events.query(EventQuery(session_id=connected.session_id))
        event_types = [event.event_type for event in events]
        assert event_types[:2] == [
            SandboxEventType.SANDBOX_STARTED,
            SandboxEventType.OPERATION_STARTED,
        ]
        assert SandboxEventType.SANDBOX_FAILED not in event_types

        closing = asyncio.create_task(configured.service.close())
        closed, _ = await asyncio.wait((closing,), timeout=0.3)
        assert closing in closed
        await closing
    finally:
        gateway.release.set()
        await asyncio.wait_for(gateway.finished.wait(), timeout=0.3)
        await asyncio.gather(sending, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await configured.service.close()


@pytest.mark.asyncio
async def test_gateway_that_suppresses_cancellation_cannot_block_close_driven_cancel() -> None:
    gateway = CancellationSuppressingGateway(response())
    configured = bundle((), gateway_override=gateway)
    connected = await connected_session(configured)
    sending = asyncio.create_task(connected.send_http(http_request()))
    closing: asyncio.Task[None] | None = None
    await gateway.entered.wait()
    try:
        closing = asyncio.create_task(configured.service.close())
        finished, _ = await asyncio.wait((sending, closing), timeout=0.3)
        assert sending in finished
        assert closing in finished
        with pytest.raises(SessionOperationCancelled) as captured:
            await sending
        assert "collaborator also failed during cancellation" in _exception_graph_notes(
            captured.value
        )
        await closing
    finally:
        gateway.release.set()
        await asyncio.wait_for(gateway.finished.wait(), timeout=0.3)
        await asyncio.gather(sending, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
        await configured.service.close()


@pytest.mark.asyncio
async def test_native_cancellation_cancels_gateway_and_emits_cancelled_terminal() -> None:
    gateway = CancellationObservingGateway()
    configured = bundle((), gateway_override=gateway)
    connected = await connected_session(configured)
    sending = asyncio.create_task(connected.send_http(http_request()))
    await gateway.entered.wait()

    sending.cancel()

    with pytest.raises(asyncio.CancelledError):
        await sending
    await asyncio.wait_for(gateway.finished.wait(), timeout=0.3)
    assert gateway.cancelled.is_set()
    assert sending.cancelled()
    assert connected.state is SandboxSessionState.RUNNING
    events = await configured.events.query(EventQuery(session_id=connected.session_id))
    assert [
        event.event_type for event in events if event.operation_kind is OperationKind.OUTBOUND_HTTP
    ] == [
        SandboxEventType.OPERATION_STARTED,
        SandboxEventType.OPERATION_CANCELLED,
    ]
    await configured.service.close()


@pytest.mark.asyncio
async def test_repeated_native_cancellation_retains_gateway_and_fails_closed() -> None:
    gateway = CancellationSuppressingGateway(response())
    configured = bundle((), gateway_override=gateway)
    connected = await connected_session(configured)
    sending = asyncio.create_task(connected.send_http(http_request()))
    closing: asyncio.Task[None] | None = None
    await gateway.entered.wait()
    try:
        sending.cancel()
        await gateway.cancelled.wait()
        sending.cancel()

        with pytest.raises(asyncio.CancelledError):
            await sending
        assert connected.state is SandboxSessionState.FAILED
        with pytest.raises(SessionFailed):
            await connected.send_http(http_request())

        closing = asyncio.create_task(configured.service.close())
        closed, _ = await asyncio.wait((closing,), timeout=0.3)
        assert closing in closed
        await closing
        assert gateway.cancel_count >= 2
    finally:
        gateway.release.set()
        await asyncio.wait_for(gateway.finished.wait(), timeout=0.3)
        await asyncio.gather(sending, return_exceptions=True)
        if closing is not None:
            await asyncio.gather(closing, return_exceptions=True)
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


def _assert_exception_graph_hides(error: BaseException, canary: str) -> None:
    pending = [error]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        assert canary not in str(current)
        assert all(canary not in note for note in getattr(current, "__notes__", ()))
        retained_object = getattr(current, "object", None)
        if isinstance(retained_object, str):
            assert canary not in retained_object
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
        if isinstance(current, BaseExceptionGroup):
            group = cast(BaseExceptionGroup[BaseException], current)
            pending.extend(group.exceptions)


def _exception_graph_notes(error: BaseException) -> tuple[str, ...]:
    pending = [error]
    seen: set[int] = set()
    notes: list[str] = []
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        notes.extend(getattr(current, "__notes__", ()))
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)
        if isinstance(current, BaseExceptionGroup):
            group = cast(BaseExceptionGroup[BaseException], current)
            pending.extend(group.exceptions)
    return tuple(notes)
