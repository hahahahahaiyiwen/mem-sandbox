from __future__ import annotations

import asyncio
import traceback
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from mem_sandbox.core import ErrorCategory, OperationId, SessionId
from mem_sandbox.secrets import (
    BoundedSecretBroker,
    MappingSecretSource,
    SecretAccessRequest,
    SecretBrokerLimits,
    SecretExpired,
    SecretLeaseClosed,
    SecretLeaseLimitExceeded,
    SecretNotFound,
    SecretRef,
    SecretSourceUnavailable,
    SecretSourceValueInvalid,
    SecretValue,
)

NOW = datetime(2026, 9, 3, 16, tzinfo=UTC)
SESSION_ID = SessionId(UUID("12345678-1234-5678-1234-567812345678"))
OPERATION_ID = OperationId(UUID("87654321-4321-8765-4321-876543218765"))
SECRET_REF = SecretRef("service-token")
CANARY = "secret-\N{LOCK}-canary"


class MutableClock:
    def __init__(self) -> None:
        self.value = NOW
        self.error: Exception | None = None

    def now(self) -> datetime:
        if self.error is not None:
            raise self.error
        return self.value

    def advance(self, delta: timedelta) -> None:
        self.value += delta


class BlockingSource:
    def __init__(self, value: SecretValue) -> None:
        self.value = value
        self.calls = 0
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.block = True

    async def resolve(self, secret_ref: SecretRef) -> SecretValue:
        self.calls += 1
        self.entered.set()
        if self.block:
            await self.release.wait()
        return self.value


class FailingSource:
    async def resolve(self, secret_ref: SecretRef) -> SecretValue:
        raise RuntimeError(CANARY)


class FatalResolution(BaseException):
    pass


class FatalThenValueSource:
    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, secret_ref: SecretRef) -> SecretValue:
        self.calls += 1
        if self.calls == 1:
            raise FatalResolution
        return SecretValue(CANARY)


class SequenceSource:
    def __init__(self, values: list[SecretValue | Exception]) -> None:
        self.values = values

    async def resolve(self, secret_ref: SecretRef) -> SecretValue:
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def request(*, max_lease_seconds: float = 5.0) -> SecretAccessRequest:
    return SecretAccessRequest(
        session_id=SESSION_ID,
        operation_id=OPERATION_ID,
        secret_ref=SECRET_REF,
        command_name=None,
        max_lease_seconds=max_lease_seconds,
    )


def broker(
    source: object,
    clock: MutableClock,
    *,
    max_active_leases: int = 1,
    max_lease_seconds: float = 5.0,
    max_value_bytes: int = 128,
) -> BoundedSecretBroker:
    return BoundedSecretBroker(
        source,  # type: ignore[arg-type]
        limits=SecretBrokerLimits(
            max_active_leases=max_active_leases,
            max_lease_seconds=max_lease_seconds,
            max_value_bytes=max_value_bytes,
        ),
        clock=clock,
    )


@pytest.mark.asyncio
async def test_broker_shortens_duration_and_enforces_expiry_and_close_precedence() -> None:
    clock = MutableClock()
    secrets = broker(
        MappingSecretSource({SECRET_REF: SecretValue(CANARY)}),
        clock,
        max_lease_seconds=2,
    )

    lease = await secrets.lease(request(max_lease_seconds=10))

    assert lease.expires_at == NOW + timedelta(seconds=2)
    assert lease.value.reveal_text() == CANARY
    assert CANARY not in repr(lease)
    clock.advance(timedelta(seconds=2) - timedelta(microseconds=1))
    assert lease.value.reveal_text() == CANARY
    clock.advance(timedelta(microseconds=1))
    with pytest.raises(SecretExpired) as expired:
        _ = lease.value
    assert expired.value.category is ErrorCategory.TIMEOUT
    await lease.close()
    with pytest.raises(SecretLeaseClosed) as closed:
        _ = lease.value
    assert closed.value.category is ErrorCategory.CONFLICT


@pytest.mark.asyncio
async def test_pending_resolution_consumes_capacity_without_holding_the_broker_lock() -> None:
    clock = MutableClock()
    source = BlockingSource(SecretValue(CANARY))
    secrets = broker(source, clock)
    pending = asyncio.create_task(secrets.lease(request()))
    await source.entered.wait()

    with pytest.raises(SecretLeaseLimitExceeded) as raised:
        await secrets.lease(request())

    assert raised.value.category is ErrorCategory.QUOTA_EXCEEDED
    source.release.set()
    lease = await pending
    await lease.close()


@pytest.mark.asyncio
async def test_failed_and_cancelled_source_resolution_release_capacity() -> None:
    clock = MutableClock()
    failing = broker(FailingSource(), clock)

    with pytest.raises(SecretSourceUnavailable) as raised:
        await failing.lease(request())

    assert raised.value.category is ErrorCategory.INTERNAL
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert CANARY not in "".join(traceback.format_exception(raised.value))

    source = BlockingSource(SecretValue(CANARY))
    cancellable = broker(source, clock)
    pending = asyncio.create_task(cancellable.lease(request()))
    await source.entered.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    source.block = False
    lease = await cancellable.lease(request())
    await lease.close()


@pytest.mark.asyncio
async def test_every_source_base_exception_releases_reserved_capacity() -> None:
    clock = MutableClock()
    source = FatalThenValueSource()
    secrets = broker(source, clock)

    with pytest.raises(FatalResolution):
        await secrets.lease(request())

    lease = await secrets.lease(request())
    await lease.close()


@pytest.mark.asyncio
async def test_source_value_byte_limits_are_checked_after_resolution() -> None:
    clock = MutableClock()
    values = ("", "x", "éé", "ééé")
    source = SequenceSource(
        [
            SecretValue(values[0]),
            SecretValue(values[1]),
            SecretValue(values[2]),
            SecretValue(values[3]),
        ]
    )
    secrets = broker(source, clock, max_value_bytes=4)

    with pytest.raises(SecretSourceValueInvalid):
        await secrets.lease(request())

    invalid_utf8 = broker(
        MappingSecretSource({SECRET_REF: SecretValue("\ud800")}),
        clock,
        max_value_bytes=4,
    )
    with pytest.raises(SecretSourceValueInvalid) as raised:
        await invalid_utf8.lease(request())
    assert raised.value.__cause__ is None

    short = await secrets.lease(request())
    assert short.value.reveal_text() == "x"
    await short.close()

    exact = await secrets.lease(request())
    assert exact.value.reveal_text() == "éé"
    await exact.close()

    with pytest.raises(SecretSourceValueInvalid):
        await secrets.lease(request())


@pytest.mark.asyncio
async def test_missing_reference_remains_distinct_and_releases_capacity() -> None:
    clock = MutableClock()
    source = SequenceSource(
        [
            SecretNotFound("safe missing reference"),
            SecretValue(CANARY),
        ]
    )
    secrets = broker(source, clock)

    with pytest.raises(SecretNotFound) as raised:
        await secrets.lease(request())

    assert raised.value.category is ErrorCategory.NOT_FOUND
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    replacement = await secrets.lease(request())
    await replacement.close()


@pytest.mark.asyncio
async def test_expired_records_are_lazily_purged_for_new_admission() -> None:
    clock = MutableClock()
    secrets = broker(
        MappingSecretSource({SECRET_REF: SecretValue(CANARY)}),
        clock,
        max_lease_seconds=1,
    )
    expired = await secrets.lease(request())
    clock.advance(timedelta(seconds=1))

    replacement = await secrets.lease(request())

    with pytest.raises(SecretExpired):
        _ = expired.value
    assert replacement is not expired
    assert replacement.value.reveal_text() == CANARY
    await expired.close()
    with pytest.raises(SecretLeaseLimitExceeded):
        await secrets.lease(request())
    await replacement.close()


@pytest.mark.asyncio
async def test_close_is_concurrent_idempotent_and_releases_capacity_once() -> None:
    clock = MutableClock()
    secrets = broker(
        MappingSecretSource({SECRET_REF: SecretValue(CANARY)}),
        clock,
    )
    lease = await secrets.lease(request())

    await asyncio.gather(*(lease.close() for _ in range(20)))
    await lease.close()

    replacement = await secrets.lease(request())
    await replacement.close()


@pytest.mark.asyncio
async def test_same_reference_leases_are_independent() -> None:
    clock = MutableClock()
    secrets = broker(
        MappingSecretSource({SECRET_REF: SecretValue(CANARY)}),
        clock,
        max_active_leases=2,
    )

    first, second = await asyncio.gather(secrets.lease(request()), secrets.lease(request()))

    assert first is not second
    await first.close()
    assert second.value.reveal_text() == CANARY
    await second.close()


@pytest.mark.asyncio
async def test_lease_value_uses_the_broker_clock_failure_contract() -> None:
    clock = MutableClock()
    secrets = broker(
        MappingSecretSource({SECRET_REF: SecretValue(CANARY)}),
        clock,
    )
    lease = await secrets.lease(request())
    clock.error = RuntimeError(CANARY)

    with pytest.raises(SecretSourceUnavailable) as raised:
        _ = lease.value

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    assert CANARY not in "".join(traceback.format_exception(raised.value))
    clock.error = None
    await lease.close()
