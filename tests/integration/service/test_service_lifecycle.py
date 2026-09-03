from datetime import timedelta

import pytest

from mem_sandbox.core import SystemClock, SystemUuidGenerator
from mem_sandbox.events import InMemoryEventSink
from mem_sandbox.policy import AllowAllPolicyEngine
from mem_sandbox.secrets import NoSecretBroker
from mem_sandbox.service import (
    CreateSandboxRequest,
    DefaultSessionFactory,
    InMemorySandboxService,
    InMemoryServiceSnapshotGateway,
    OwnerId,
    ResumeSandboxRequest,
    SandboxNotFound,
    WorkspaceSeedFile,
)
from mem_sandbox.session import (
    CreateSnapshotRequest,
    ReadBytesRequest,
    WriteBytesRequest,
)
from mem_sandbox.snapshots import (
    InMemorySnapshotStore,
    JsonSessionSnapshotCodec,
    SnapshotStoreLimits,
)


@pytest.mark.asyncio
async def test_service_resume_creates_independent_workspace_forks() -> None:
    clock = SystemClock()
    uuid_generator = SystemUuidGenerator()
    codec = JsonSessionSnapshotCodec()
    store = InMemorySnapshotStore(
        default_ttl=timedelta(days=1),
        limits=SnapshotStoreLimits(
            max_snapshots=10,
            max_total_payload_bytes=64 * 1024 * 1024,
        ),
        clock=clock,
    )
    gateway = InMemoryServiceSnapshotGateway(store)
    factory = DefaultSessionFactory(
        policy_engine=AllowAllPolicyEngine(),
        secret_broker=NoSecretBroker(),
        event_sink=InMemoryEventSink(max_events=100, max_payload_bytes=1024 * 1024),
        snapshot_codec=codec,
        clock=clock,
        uuid_generator=uuid_generator,
    )
    service = InMemorySandboxService(
        session_factory=factory,
        snapshot_gateway=gateway,
        snapshot_decoder=codec,
        clock=clock,
        uuid_generator=uuid_generator,
    )

    source_handle = await service.create(
        CreateSandboxRequest(
            owner_id=OwnerId("source"),
            initial_files=(WorkspaceSeedFile("/workspace/state.bin", b"original"),),
        )
    )
    source = await service.get_session(source_handle)
    snapshot_result = await source.create_snapshot(CreateSnapshotRequest())

    first_handle = await service.resume(
        ResumeSandboxRequest(
            owner_id=OwnerId("first"),
            snapshot_ref=snapshot_result.snapshot_ref,
        )
    )
    second_handle = await service.resume(
        ResumeSandboxRequest(
            owner_id=OwnerId("second"),
            snapshot_ref=snapshot_result.snapshot_ref,
        )
    )
    first = await service.get_session(first_handle)
    second = await service.get_session(second_handle)

    await first.write_bytes(WriteBytesRequest(path="state.bin", content=b"changed"))
    first_content = await first.read_bytes(ReadBytesRequest(path="state.bin"))
    second_content = await second.read_bytes(ReadBytesRequest(path="state.bin"))
    persisted = await gateway.load(snapshot_result.snapshot_ref)

    assert first_content.content == b"changed"
    assert second_content.content == b"original"
    assert persisted.created_by == "source"
    assert first.session_id != second.session_id != source.session_id

    await service.delete(first_handle)
    with pytest.raises(SandboxNotFound):
        await service.get_session(first_handle)
    assert (await second.read_bytes(ReadBytesRequest(path="state.bin"))).content == b"original"
    await service.close()
