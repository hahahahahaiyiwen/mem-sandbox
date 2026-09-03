import asyncio
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

from mem_sandbox.core import SnapshotId
from mem_sandbox.service import (
    CreateSandboxRequest,
    InMemorySandboxService,
    OwnerId,
    ResumeSandboxRequest,
    SandboxDeleteFailed,
    SandboxHandle,
    SandboxIdentifierConflict,
    SandboxNotFound,
    SandboxServiceClosed,
    SandboxStartupFailed,
    ServiceSnapshotGateway,
    SessionFactoryRequest,
)
from mem_sandbox.session import SandboxSession, SandboxSessionState
from mem_sandbox.snapshots import SandboxSnapshot, SessionSnapshotState, SnapshotRef


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


class SequenceUuidGenerator:
    def __init__(self, values: Iterable[UUID]) -> None:
        self._values = iter(values)

    def new_uuid(self) -> UUID:
        return next(self._values)


class FakeSession:
    def __init__(
        self,
        *,
        start_gate: asyncio.Event | None = None,
        start_error: BaseException | None = None,
    ) -> None:
        self.state = SandboxSessionState.CREATED
        self.start_gate = start_gate
        self.start_error = start_error
        self.start_entered = asyncio.Event()
        self.start_count = 0

    async def start(self) -> None:
        self.start_count += 1
        self.start_entered.set()
        if self.start_gate is not None:
            await self.start_gate.wait()
        if self.start_error is not None:
            raise self.start_error
        self.state = SandboxSessionState.RUNNING


class FakeRuntime:
    def __init__(
        self,
        session: FakeSession,
        *,
        close_gate: asyncio.Event | None = None,
        close_error: BaseException | None = None,
    ) -> None:
        self._session = session
        self.close_gate = close_gate
        self.close_error = close_error
        self.close_entered = asyncio.Event()
        self.close_count = 0

    @property
    def session(self) -> SandboxSession:
        return cast(SandboxSession, self._session)

    async def close(self) -> None:
        self.close_count += 1
        self.close_entered.set()
        if self.close_gate is not None:
            await self.close_gate.wait()
        self._session.state = SandboxSessionState.CLOSED
        if self.close_error is not None:
            raise self.close_error


class RecordingFactory:
    def __init__(self, runtimes: Iterable[FakeRuntime]) -> None:
        self._runtimes = iter(runtimes)
        self.requests: list[SessionFactoryRequest] = []

    async def create(self, request: SessionFactoryRequest) -> FakeRuntime:
        self.requests.append(request)
        return next(self._runtimes)


class FakeGateway:
    def __init__(self, snapshot: SandboxSnapshot | None = None) -> None:
        self.snapshot = snapshot
        self.owners: list[OwnerId] = []

    def session_store(self, owner_id: OwnerId) -> object:
        self.owners.append(owner_id)
        return _SnapshotStore()

    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot:
        _ = snapshot_ref
        if self.snapshot is None:
            raise AssertionError("snapshot was not configured")
        return self.snapshot


class _SnapshotStore:
    process_local = True


class FakeDecoder:
    def __init__(self, state: SessionSnapshotState | None = None) -> None:
        self.state = state
        self.seen: list[SandboxSnapshot] = []

    def decode(self, snapshot: SandboxSnapshot) -> SessionSnapshotState:
        self.seen.append(snapshot)
        if self.state is None:
            raise AssertionError("state was not configured")
        return self.state


def _service(
    factory: RecordingFactory,
    gateway: FakeGateway,
    decoder: FakeDecoder,
    *identifiers: str,
) -> InMemorySandboxService:
    return InMemorySandboxService(
        session_factory=factory,
        snapshot_gateway=cast(ServiceSnapshotGateway, gateway),
        snapshot_decoder=decoder,
        clock=FixedClock(datetime(2026, 9, 3, tzinfo=UTC)),
        uuid_generator=SequenceUuidGenerator(UUID(value) for value in identifiers),
    )


@pytest.mark.asyncio
async def test_create_publishes_only_after_start_and_lookup_returns_same_session() -> None:
    release = asyncio.Event()
    session = FakeSession(start_gate=release)
    runtime = FakeRuntime(session)
    service = _service(
        RecordingFactory((runtime,)),
        FakeGateway(),
        FakeDecoder(),
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )

    creating = asyncio.create_task(service.create(CreateSandboxRequest(owner_id=OwnerId("o"))))
    await session.start_entered.wait()
    with pytest.raises(SandboxNotFound):
        await service.get_session(SandboxHandle(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")))
    release.set()
    handle = await creating

    assert await service.get_session(handle) is runtime.session
    await service.close()


@pytest.mark.asyncio
async def test_concurrent_delete_waiters_share_cleanup_and_lookup_is_blocked() -> None:
    release = asyncio.Event()
    runtime = FakeRuntime(FakeSession(), close_gate=release)
    service = _service(
        RecordingFactory((runtime,)),
        FakeGateway(),
        FakeDecoder(),
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )
    handle = await service.create(CreateSandboxRequest(owner_id=OwnerId("o")))

    first = asyncio.create_task(service.delete(handle))
    await runtime.close_entered.wait()
    second = asyncio.create_task(service.delete(handle))
    with pytest.raises(SandboxNotFound):
        await service.get_session(handle)
    release.set()
    await asyncio.gather(first, second)

    assert runtime.close_count == 1
    with pytest.raises(SandboxNotFound):
        await service.delete(handle)
    await service.close()


@pytest.mark.asyncio
async def test_resume_decodes_before_factory_and_uses_new_owner_provenance(
    empty_snapshot_state: SessionSnapshotState,
) -> None:
    runtime = FakeRuntime(FakeSession())
    snapshot = cast(SandboxSnapshot, object())
    gateway = FakeGateway(snapshot)
    decoder = FakeDecoder(empty_snapshot_state)
    factory = RecordingFactory((runtime,))
    service = _service(
        factory,
        gateway,
        decoder,
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )
    snapshot_ref = SnapshotRef(SnapshotId(UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")))

    handle = await service.resume(
        ResumeSandboxRequest(
            owner_id=OwnerId("new-owner"),
            snapshot_ref=snapshot_ref,
        )
    )

    assert handle == SandboxHandle(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
    assert decoder.seen == [snapshot]
    assert factory.requests[0].restored_state is empty_snapshot_state
    assert gateway.owners == [OwnerId("new-owner")]
    await service.close()


@pytest.mark.asyncio
async def test_close_racing_startup_prevents_publication_and_closes_runtime() -> None:
    release = asyncio.Event()
    session = FakeSession(start_gate=release)
    runtime = FakeRuntime(session)
    service = _service(
        RecordingFactory((runtime,)),
        FakeGateway(),
        FakeDecoder(),
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )
    creating = asyncio.create_task(service.create(CreateSandboxRequest(owner_id=OwnerId("o"))))
    await session.start_entered.wait()

    closing = asyncio.create_task(service.close())
    await asyncio.sleep(0)
    release.set()
    await closing

    with pytest.raises(SandboxServiceClosed):
        await creating
    assert runtime.close_count == 1
    with pytest.raises(SandboxServiceClosed):
        await service.create(CreateSandboxRequest(owner_id=OwnerId("o")))


@pytest.mark.asyncio
async def test_cancelled_delete_waiter_does_not_cancel_shared_cleanup() -> None:
    release = asyncio.Event()
    runtime = FakeRuntime(FakeSession(), close_gate=release)
    service = _service(
        RecordingFactory((runtime,)),
        FakeGateway(),
        FakeDecoder(),
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )
    handle = await service.create(CreateSandboxRequest(owner_id=OwnerId("o")))

    first = asyncio.create_task(service.delete(handle))
    await runtime.close_entered.wait()
    cancelled = asyncio.create_task(service.delete(handle))
    cancelled.cancel()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await cancelled
    await first
    assert runtime.close_count == 1
    await service.close()


@pytest.mark.asyncio
async def test_lookup_of_failed_session_drives_shared_cleanup() -> None:
    session = FakeSession()
    runtime = FakeRuntime(session)
    service = _service(
        RecordingFactory((runtime,)),
        FakeGateway(),
        FakeDecoder(),
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )
    handle = await service.create(CreateSandboxRequest(owner_id=OwnerId("o")))
    session.state = SandboxSessionState.FAILED

    with pytest.raises(SandboxNotFound):
        await service.get_session(handle)

    assert runtime.close_count == 1
    await service.close()


@pytest.mark.asyncio
async def test_generated_identifier_collision_does_not_construct_second_session() -> None:
    runtime = FakeRuntime(FakeSession())
    factory = RecordingFactory((runtime,))
    service = _service(
        factory,
        FakeGateway(),
        FakeDecoder(),
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "cccccccc-cccc-cccc-cccc-cccccccccccc",
    )

    await service.create(CreateSandboxRequest(owner_id=OwnerId("first")))
    with pytest.raises(SandboxIdentifierConflict):
        await service.create(CreateSandboxRequest(owner_id=OwnerId("second")))

    assert len(factory.requests) == 1
    await service.close()


@pytest.mark.asyncio
async def test_startup_cleanup_failure_does_not_mask_primary_or_leak_reservations() -> None:
    failed_runtime = FakeRuntime(
        FakeSession(start_error=RuntimeError("start")),
        close_error=RuntimeError("close"),
    )
    successful_runtime = FakeRuntime(FakeSession())
    factory = RecordingFactory((failed_runtime, successful_runtime))
    service = _service(
        factory,
        FakeGateway(),
        FakeDecoder(),
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )

    with pytest.raises(SandboxStartupFailed, match="sandbox startup failed") as captured:
        await service.create(CreateSandboxRequest(owner_id=OwnerId("first")))
    handle = await service.create(CreateSandboxRequest(owner_id=OwnerId("second")))

    assert captured.value.__notes__ == ["secondary runtime cleanup failure: close"]
    assert handle == SandboxHandle(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
    await service.close()


@pytest.mark.asyncio
async def test_close_waits_for_lifecycle_operation_not_its_caller_task() -> None:
    release = asyncio.Event()
    session = FakeSession(start_gate=release)
    runtime = FakeRuntime(session)
    service = _service(
        RecordingFactory((runtime,)),
        FakeGateway(),
        FakeDecoder(),
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )

    async def create_then_close() -> None:
        with pytest.raises(SandboxServiceClosed):
            await service.create(CreateSandboxRequest(owner_id=OwnerId("o")))
        await service.close()

    caller = asyncio.create_task(create_then_close())
    await session.start_entered.wait()
    closing = asyncio.create_task(service.close())
    await asyncio.sleep(0)
    release.set()

    await asyncio.wait_for(asyncio.gather(caller, closing), timeout=1)


@pytest.mark.asyncio
async def test_close_observes_in_flight_deletion_failure() -> None:
    release = asyncio.Event()
    runtime = FakeRuntime(
        FakeSession(),
        close_gate=release,
        close_error=RuntimeError("cleanup"),
    )
    service = _service(
        RecordingFactory((runtime,)),
        FakeGateway(),
        FakeDecoder(),
        "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
    )
    handle = await service.create(CreateSandboxRequest(owner_id=OwnerId("o")))
    deleting = asyncio.create_task(service.delete(handle))
    await runtime.close_entered.wait()

    closing = asyncio.create_task(service.close())
    await asyncio.sleep(0)
    release.set()

    with pytest.raises(SandboxDeleteFailed, match="cleanup failed"):
        await deleting
    with pytest.raises(SandboxDeleteFailed, match="service cleanup failed"):
        await closing
    await service.close()
