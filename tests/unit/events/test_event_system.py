from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from uuid import UUID

import pytest

from mem_sandbox.core import ErrorCategory, OperationId, OperationKind, SandboxError, SessionId
from mem_sandbox.events import (
    EventAttribute,
    EventBufferLimitExceeded,
    EventCategory,
    EventDeliveryDiagnostic,
    EventDeliveryMode,
    EventDeliveryPolicy,
    EventDispatcher,
    EventInvalid,
    EventPayloadLimits,
    EventPayloadPolicy,
    EventPayloadRejected,
    EventQuery,
    EventSensitivity,
    EventSinkClosed,
    InMemoryEventSink,
    ProtectedValueRedactor,
    SandboxEvent,
    SandboxEventType,
    canonical_event_bytes,
    prepare_event,
)

SESSION_A = SessionId(UUID("11111111-1111-1111-1111-111111111111"))
SESSION_B = SessionId(UUID("22222222-2222-2222-2222-222222222222"))
OPERATION_ID = OperationId(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
OCCURRED_AT = datetime(2026, 9, 2, 12, tzinfo=UTC)


def event(
    *,
    session_id: SessionId = SESSION_A,
    sequence: int = 1,
    event_type: SandboxEventType = SandboxEventType.OPERATION_COMPLETED,
    attributes: tuple[EventAttribute, ...] = (),
    occurred_at: datetime = OCCURRED_AT,
) -> SandboxEvent:
    return SandboxEvent(
        event_type=event_type,
        occurred_at=occurred_at,
        session_id=session_id,
        sequence=sequence,
        operation_id=OPERATION_ID,
        operation_kind=OperationKind.EXECUTE,
        attributes=attributes,
    )


def internal(name: str, value: str | int | float | bool | None) -> EventAttribute:
    return EventAttribute(name, value, EventSensitivity.INTERNAL)


def test_event_identity_categories_and_attributes_are_immutable_and_canonical() -> None:
    attributes = (
        internal("zeta", 2),
        internal("alpha", "value"),
    )
    value = event(attributes=attributes)

    assert str(value.event_id) == f"{SESSION_A}:1"
    assert value.event_type.category is EventCategory.OPERATION
    assert SandboxEventType.SNAPSHOT_CREATED.category is EventCategory.SNAPSHOT
    assert (
        SandboxEventType.SANDBOX_CREATED.category
        is EventCategory.SERVICE_LIFECYCLE
    )
    assert [attribute.name for attribute in value.attributes] == ["alpha", "zeta"]
    assert dict(value.data) == {"alpha": "value", "zeta": 2}
    with pytest.raises(FrozenInstanceError):
        value.sequence = 2  # type: ignore[misc]


@pytest.mark.parametrize("value", (float("nan"), float("inf"), float("-inf")))
def test_event_attributes_reject_nonfinite_floats(value: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        EventAttribute("value", value, EventSensitivity.INTERNAL)


def test_event_model_rejects_invalid_identity_time_and_duplicate_attributes() -> None:
    with pytest.raises(ValueError, match="UTC"):
        event(occurred_at=OCCURRED_AT.astimezone(timezone(timedelta(hours=1))))
    with pytest.raises(ValueError, match="positive"):
        event(sequence=0)
    with pytest.raises(ValueError, match="duplicate"):
        event(attributes=(internal("same", 1), internal("same", 2)))


def test_canonical_payload_is_order_independent_and_has_exact_aggregate_limit() -> None:
    first = event(attributes=(internal("b", 2), internal("a", "é")))
    second = event(attributes=(internal("a", "é"), internal("b", 2)))
    encoded = canonical_event_bytes(first)

    assert encoded == canonical_event_bytes(second)
    assert prepare_event(
        first,
        redactor=ProtectedValueRedactor(),
        payload_policy=EventPayloadPolicy(),
        limits=EventPayloadLimits(max_event_bytes=len(encoded)),
    ) == first
    with pytest.raises(EventPayloadRejected, match="payload"):
        prepare_event(
            first,
            redactor=ProtectedValueRedactor(),
            payload_policy=EventPayloadPolicy(),
            limits=EventPayloadLimits(max_event_bytes=len(encoded) - 1),
        )


def test_payload_field_limits_and_sensitivity_are_enforced_after_redaction() -> None:
    protected = EventAttribute("protected", "x", EventSensitivity.PROTECTED)
    secret = EventAttribute("secret", "value", EventSensitivity.SECRET)

    with pytest.raises(EventPayloadRejected, match="protected"):
        prepare_event(
            event(attributes=(protected,)),
            redactor=ProtectedValueRedactor(text_values=("x",)),
            payload_policy=EventPayloadPolicy(),
            limits=EventPayloadLimits(),
        )
    with pytest.raises(EventPayloadRejected, match="structurally"):
        prepare_event(
            event(attributes=(protected,)),
            redactor=ProtectedValueRedactor(),
            payload_policy=EventPayloadPolicy(allow_protected=True),
            limits=EventPayloadLimits(),
        )
    prepared = prepare_event(
        event(attributes=(protected,)),
        redactor=ProtectedValueRedactor(text_values=("x",)),
        payload_policy=EventPayloadPolicy(allow_protected=True),
        limits=EventPayloadLimits(max_string_bytes=len(b"[REDACTED]")),
    )
    assert prepared.data["protected"] == "[REDACTED]"
    with pytest.raises(EventPayloadRejected, match="string"):
        prepare_event(
            event(attributes=(protected,)),
            redactor=ProtectedValueRedactor(text_values=("x",)),
            payload_policy=EventPayloadPolicy(allow_protected=True),
            limits=EventPayloadLimits(max_string_bytes=len(b"[REDACTED]") - 1),
        )
    with pytest.raises(EventPayloadRejected, match="secret"):
        prepare_event(
            event(attributes=(secret,)),
            redactor=ProtectedValueRedactor(text_values=("value",)),
            payload_policy=EventPayloadPolicy(allow_protected=True),
            limits=EventPayloadLimits(),
        )


def test_redactor_handles_overlap_repetition_bytes_and_does_not_mutate_source() -> None:
    source = event(
        attributes=(
            internal("text", "abc ab abc"),
            internal("marker", "[REDACTED]"),
        )
    )
    redactor = ProtectedValueRedactor(
        text_values=("ab", "abc"),
        byte_values=("é".encode(),),
    )

    redacted = redactor.redact_event(source)

    assert source.data["text"] == "abc ab abc"
    assert redacted.data["text"] == "[REDACTED] [REDACTED] [REDACTED]"
    assert redactor.redact_text("é") == "[REDACTED]"
    assert redactor.redact_bytes(b"x\xc3\xa9x") == b"x[REDACTED]x"
    assert redactor.redact_text("616263 YWJj") == "616263 YWJj"
    assert redacted.data["marker"] == "[REDACTED]"
    with pytest.raises(ValueError, match="empty"):
        ProtectedValueRedactor(text_values=("",))


def test_redactor_merges_matches_that_overlap_at_different_offsets() -> None:
    redactor = ProtectedValueRedactor(text_values=("ab", "bcdef"))

    assert redactor.redact_text("abcdef") == "[REDACTED]"
    assert redactor.redact_bytes(b"abcdef") == b"[REDACTED]"


def test_protected_attribute_redaction_never_preserves_a_partial_value() -> None:
    prepared = prepare_event(
        event(
            attributes=(
                EventAttribute(
                    "authorization",
                    "Bearer token trailing-data",
                    EventSensitivity.PROTECTED,
                ),
            )
        ),
        redactor=ProtectedValueRedactor(text_values=("token",)),
        payload_policy=EventPayloadPolicy(allow_protected=True),
        limits=EventPayloadLimits(),
    )

    assert prepared.data["authorization"] == "[REDACTED]"


def test_payload_count_name_and_string_limits_have_exact_boundaries() -> None:
    attributes = (internal("é", "é"), internal("b", 2))
    value = event(attributes=attributes)

    assert prepare_event(
        value,
        redactor=ProtectedValueRedactor(),
        payload_policy=EventPayloadPolicy(),
        limits=EventPayloadLimits(
            max_attributes=2,
            max_attribute_name_bytes=2,
            max_string_bytes=2,
        ),
    ) == value
    with pytest.raises(EventPayloadRejected, match="attributes"):
        prepare_event(
            value,
            redactor=ProtectedValueRedactor(),
            payload_policy=EventPayloadPolicy(),
            limits=EventPayloadLimits(max_attributes=1),
        )
    with pytest.raises(EventPayloadRejected, match="name"):
        prepare_event(
            value,
            redactor=ProtectedValueRedactor(),
            payload_policy=EventPayloadPolicy(),
            limits=EventPayloadLimits(max_attribute_name_bytes=1),
        )
    with pytest.raises(EventPayloadRejected, match="string"):
        prepare_event(
            value,
            redactor=ProtectedValueRedactor(),
            payload_policy=EventPayloadPolicy(),
            limits=EventPayloadLimits(max_string_bytes=1),
        )


@pytest.mark.asyncio
async def test_in_memory_sink_enforces_atomic_count_and_byte_limits() -> None:
    first = event(sequence=1)
    second = event(sequence=2)
    first_bytes = len(canonical_event_bytes(first))
    second_bytes = len(canonical_event_bytes(second))
    sink = InMemoryEventSink(
        max_events=2,
        max_payload_bytes=first_bytes + second_bytes,
    )

    await sink.emit(first)
    await sink.emit(second)
    assert await sink.query() == (first, second)
    assert (await sink.stats()).payload_bytes == first_bytes + second_bytes

    with pytest.raises(EventBufferLimitExceeded):
        await sink.emit(event(sequence=3))
    assert await sink.query() == (first, second)
    assert (await sink.stats()).payload_bytes == first_bytes + second_bytes


@pytest.mark.asyncio
async def test_in_memory_sink_rejects_one_byte_overflow_without_advancing_sequence() -> None:
    first = event(sequence=1)
    second = event(sequence=2)
    oversized = event(sequence=2, attributes=(internal("large", "x" * 10),))
    byte_limit = (
        len(canonical_event_bytes(first)) + len(canonical_event_bytes(oversized)) - 1
    )
    sink = InMemoryEventSink(max_events=3, max_payload_bytes=byte_limit)

    await sink.emit(first)
    before = await sink.stats()
    with pytest.raises(EventBufferLimitExceeded):
        await sink.emit(oversized)

    assert await sink.query() == (first,)
    assert await sink.stats() == before
    await sink.emit(second)
    assert await sink.query() == (first, second)


@pytest.mark.asyncio
async def test_in_memory_sink_validates_sequences_and_deterministic_queries() -> None:
    sink = InMemoryEventSink(max_events=10, max_payload_bytes=100_000)
    later_a = event(session_id=SESSION_A, sequence=3, occurred_at=OCCURRED_AT)
    earlier_b = event(
        session_id=SESSION_B,
        sequence=1,
        occurred_at=OCCURRED_AT - timedelta(seconds=1),
    )
    await sink.emit(later_a)
    await sink.emit(earlier_b)
    await sink.emit(event(session_id=SESSION_A, sequence=5, occurred_at=OCCURRED_AT))

    assert await sink.query() == (
        earlier_b,
        later_a,
        event(session_id=SESSION_A, sequence=5, occurred_at=OCCURRED_AT),
    )
    assert await sink.query(EventQuery(session_id=SESSION_A)) == (
        later_a,
        event(session_id=SESSION_A, sequence=5, occurred_at=OCCURRED_AT),
    )
    assert await sink.query(
        EventQuery(
            operation_id=OPERATION_ID,
            category=EventCategory.OPERATION,
            event_type=SandboxEventType.OPERATION_COMPLETED,
        )
    ) == (
        earlier_b,
        later_a,
        event(session_id=SESSION_A, sequence=5, occurred_at=OCCURRED_AT),
    )
    with pytest.raises(EventInvalid, match="increasing"):
        await sink.emit(event(session_id=SESSION_A, sequence=5))
    with pytest.raises(EventInvalid, match="increasing"):
        await sink.emit(event(session_id=SESSION_A, sequence=4))


@pytest.mark.asyncio
async def test_in_memory_sink_serializes_concurrent_emissions() -> None:
    sink = InMemoryEventSink(max_events=10, max_payload_bytes=100_000)
    values = tuple(
        event(session_id=SessionId(UUID(int=index)), sequence=1)
        for index in range(1, 6)
    )

    await asyncio.gather(*(sink.emit(value) for value in values))

    assert set(await sink.query()) == set(values)
    assert (await sink.stats()).event_count == len(values)


@pytest.mark.asyncio
async def test_in_memory_sink_flush_close_and_post_close_queries() -> None:
    sink = InMemoryEventSink(max_events=2, max_payload_bytes=100_000)
    value = event()
    await sink.emit(value)

    await sink.flush()
    await sink.flush()
    await sink.close()
    await sink.close()

    assert await sink.query() == (value,)
    with pytest.raises(EventSinkClosed):
        await sink.emit(event(sequence=2))


@pytest.mark.asyncio
async def test_required_dispatch_prepares_and_redacts_before_sink_invocation() -> None:
    seen: list[SandboxEvent] = []

    class Sink:
        async def emit(self, event: SandboxEvent) -> None:
            seen.append(event)

    dispatcher = EventDispatcher(
        Sink(),
        delivery_policy=EventDeliveryPolicy(),
        payload_policy=EventPayloadPolicy(allow_protected=True),
        redactor=ProtectedValueRedactor(text_values=("token",)),
    )
    await dispatcher.emit(
        event(
            attributes=(
                EventAttribute("value", "token", EventSensitivity.PROTECTED),
            )
        )
    )

    assert seen[0].data["value"] == "[REDACTED]"
    await dispatcher.close()


@pytest.mark.asyncio
async def test_best_effort_reports_preparation_and_timeout_failures_once_each() -> None:
    diagnostics: list[EventDeliveryDiagnostic] = []

    class SlowSink:
        async def emit(self, event: SandboxEvent) -> None:
            _ = event
            await asyncio.sleep(10)

    class Handler:
        def report(self, diagnostic: EventDeliveryDiagnostic) -> None:
            diagnostics.append(diagnostic)

    dispatcher = EventDispatcher(
        SlowSink(),
        delivery_policy=EventDeliveryPolicy(
            mode=EventDeliveryMode.BEST_EFFORT,
            max_pending_events=2,
            sink_timeout_seconds=0.01,
        ),
        diagnostic_handler=Handler(),
    )
    await dispatcher.emit(
        event(
            sequence=1,
            attributes=(
                EventAttribute("secret", "value", EventSensitivity.SECRET),
            ),
        )
    )
    await dispatcher.emit(event(sequence=2))
    await dispatcher.flush()

    assert [item.event_id.sequence for item in diagnostics] == [1, 2]
    assert [item.failure_code for item in diagnostics] == [
        "event_payload_rejected",
        "event_delivery_failed",
    ]
    await dispatcher.close()


@pytest.mark.asyncio
async def test_best_effort_worker_survives_domain_error_without_a_code() -> None:
    diagnostics: list[EventDeliveryDiagnostic] = []

    class BareSandboxError(SandboxError):
        category = ErrorCategory.INTERNAL

    class FailingSink:
        async def emit(self, event: SandboxEvent) -> None:
            _ = event
            raise BareSandboxError("offline")

    class Handler:
        def report(self, diagnostic: EventDeliveryDiagnostic) -> None:
            diagnostics.append(diagnostic)

    dispatcher = EventDispatcher(
        FailingSink(),
        delivery_policy=EventDeliveryPolicy(mode=EventDeliveryMode.BEST_EFFORT),
        diagnostic_handler=Handler(),
    )
    await dispatcher.emit(event(sequence=1))
    await dispatcher.emit(event(sequence=2))
    await dispatcher.flush()

    assert [item.event_id.sequence for item in diagnostics] == [1, 2]
    assert all(item.failure_code == "event_delivery_failed" for item in diagnostics)
    await dispatcher.close()


@pytest.mark.asyncio
async def test_best_effort_requires_handler_and_reports_sink_failure_once() -> None:
    diagnostics: list[EventDeliveryDiagnostic] = []

    class FailingSink:
        async def emit(self, event: SandboxEvent) -> None:
            _ = event
            raise RuntimeError("offline")

    class Handler:
        def report(self, diagnostic: EventDeliveryDiagnostic) -> None:
            diagnostics.append(diagnostic)

    policy = EventDeliveryPolicy(
        mode=EventDeliveryMode.BEST_EFFORT,
        max_pending_events=2,
        sink_timeout_seconds=0.1,
    )
    with pytest.raises(ValueError, match="diagnostic"):
        EventDispatcher(FailingSink(), delivery_policy=policy)

    class AsyncHandler:
        async def report(self, diagnostic: EventDeliveryDiagnostic) -> None:
            _ = diagnostic

    with pytest.raises(TypeError, match="synchronous"):
        EventDispatcher(
            FailingSink(),
            delivery_policy=policy,
            diagnostic_handler=AsyncHandler(),  # type: ignore[arg-type]
        )

    dispatcher = EventDispatcher(
        FailingSink(),
        delivery_policy=policy,
        diagnostic_handler=Handler(),
    )
    await dispatcher.emit(event())
    await dispatcher.flush()

    assert len(diagnostics) == 1
    assert diagnostics[0].event_id == event().event_id
    assert diagnostics[0].failure_code == "event_delivery_failed"
    await dispatcher.close()


@pytest.mark.asyncio
async def test_best_effort_queue_overflow_and_handler_failure_preserve_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    loop_reports: list[dict[str, object]] = []

    class BlockingSink:
        async def emit(self, event: SandboxEvent) -> None:
            _ = event
            entered.set()
            await release.wait()

    class FailingHandler:
        def report(self, diagnostic: EventDeliveryDiagnostic) -> None:
            raise RuntimeError("diagnostic unavailable")

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "call_exception_handler", loop_reports.append)
    dispatcher = EventDispatcher(
        BlockingSink(),
        delivery_policy=EventDeliveryPolicy(
            mode=EventDeliveryMode.BEST_EFFORT,
            max_pending_events=1,
            sink_timeout_seconds=1.0,
        ),
        diagnostic_handler=FailingHandler(),
    )

    await dispatcher.emit(event(sequence=1))
    await entered.wait()
    await dispatcher.emit(event(sequence=2))
    await dispatcher.emit(event(sequence=3))

    assert len(loop_reports) == 1
    release.set()
    await dispatcher.flush()
    await dispatcher.close()


@pytest.mark.asyncio
async def test_best_effort_reports_handler_awaitables_to_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop_reports: list[dict[str, object]] = []

    class FailingSink:
        async def emit(self, event: SandboxEvent) -> None:
            _ = event
            raise RuntimeError("offline")

    class ReturningAwaitableHandler:
        def report(self, diagnostic: EventDeliveryDiagnostic) -> object:
            _ = diagnostic
            return asyncio.sleep(0)

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "call_exception_handler", loop_reports.append)
    dispatcher = EventDispatcher(
        FailingSink(),
        delivery_policy=EventDeliveryPolicy(mode=EventDeliveryMode.BEST_EFFORT),
        diagnostic_handler=ReturningAwaitableHandler(),  # type: ignore[arg-type]
    )

    await dispatcher.emit(event())
    await dispatcher.flush()

    assert len(loop_reports) == 1
    assert isinstance(loop_reports[0]["exception"], TypeError)
    await dispatcher.close()


@pytest.mark.asyncio
async def test_best_effort_close_is_cancellation_resilient_and_post_close_is_diagnostic() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    seen: list[SandboxEvent] = []
    diagnostics: list[EventDeliveryDiagnostic] = []

    class BlockingSink:
        async def emit(self, event: SandboxEvent) -> None:
            seen.append(event)
            entered.set()
            await release.wait()

    class Handler:
        def report(self, diagnostic: EventDeliveryDiagnostic) -> None:
            diagnostics.append(diagnostic)

    dispatcher = EventDispatcher(
        BlockingSink(),
        delivery_policy=EventDeliveryPolicy(mode=EventDeliveryMode.BEST_EFFORT),
        diagnostic_handler=Handler(),
    )
    await dispatcher.emit(event(sequence=1))
    await entered.wait()
    closing = asyncio.create_task(dispatcher.close())
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done()

    release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    await dispatcher.close()
    await dispatcher.emit(event(sequence=2))

    assert [item.sequence for item in seen] == [1]
    assert diagnostics[-1].failure_code == "event_sink_closed"


@pytest.mark.asyncio
async def test_required_dispatch_propagates_native_cancellation() -> None:
    entered = asyncio.Event()

    class BlockingSink:
        async def emit(self, event: SandboxEvent) -> None:
            _ = event
            entered.set()
            await asyncio.sleep(10)

    dispatcher = EventDispatcher(BlockingSink())
    emitting = asyncio.create_task(dispatcher.emit(event()))
    await entered.wait()
    emitting.cancel()

    with pytest.raises(asyncio.CancelledError):
        await emitting
    await dispatcher.close()
