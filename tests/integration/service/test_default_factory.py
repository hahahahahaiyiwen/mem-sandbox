from datetime import timedelta
from uuid import UUID

import pytest

from mem_sandbox.core import SessionId, SystemClock, SystemUuidGenerator
from mem_sandbox.events import InMemoryEventSink
from mem_sandbox.policy import AllowAllPolicyEngine
from mem_sandbox.secrets import NoSecretBroker
from mem_sandbox.service import (
    DefaultSessionFactory,
    InMemoryServiceSnapshotGateway,
    InvalidSandboxRequest,
    OwnerId,
    SandboxOptions,
    SessionFactoryRequest,
    WorkspaceSeedFile,
)
from mem_sandbox.session import CreateSnapshotRequest, ReadBytesRequest
from mem_sandbox.snapshots import (
    InMemorySnapshotStore,
    JsonSessionSnapshotCodec,
    SnapshotStoreLimits,
)


@pytest.mark.asyncio
async def test_default_factory_seeds_isolated_workspace_and_stamps_snapshot_creator() -> None:
    clock = SystemClock()
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
        snapshot_codec=JsonSessionSnapshotCodec(),
        clock=clock,
        uuid_generator=SystemUuidGenerator(),
    )
    runtime = await factory.create(
        SessionFactoryRequest(
            session_id=SessionId(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
            options=SandboxOptions(),
            snapshot_store=gateway.session_store(OwnerId("owner")),
            initial_files=(WorkspaceSeedFile("/workspace/nested/a.bin", b"value"),),
        )
    )

    await runtime.session.start()
    result = await runtime.session.read_bytes(ReadBytesRequest(path="nested/a.bin"))
    snapshot_result = await runtime.session.create_snapshot(CreateSnapshotRequest())
    snapshot = await gateway.load(snapshot_result.snapshot_ref)
    await runtime.close()

    assert result.content == b"value"
    assert snapshot.created_by == "owner"


@pytest.mark.asyncio
async def test_default_factory_rejects_duplicate_normalized_seed_paths() -> None:
    clock = SystemClock()
    store = InMemorySnapshotStore(
        default_ttl=timedelta(days=1),
        limits=SnapshotStoreLimits(
            max_snapshots=10,
            max_total_payload_bytes=64 * 1024 * 1024,
        ),
        clock=clock,
    )
    factory = DefaultSessionFactory(
        policy_engine=AllowAllPolicyEngine(),
        secret_broker=NoSecretBroker(),
        event_sink=InMemoryEventSink(max_events=100, max_payload_bytes=1024 * 1024),
        snapshot_codec=JsonSessionSnapshotCodec(),
        clock=clock,
        uuid_generator=SystemUuidGenerator(),
    )

    with pytest.raises(InvalidSandboxRequest, match="duplicate normalized seed paths"):
        await factory.create(
            SessionFactoryRequest(
                session_id=SessionId(UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")),
                options=SandboxOptions(),
                snapshot_store=InMemoryServiceSnapshotGateway(store).session_store(
                    OwnerId("owner")
                ),
                initial_files=(
                    WorkspaceSeedFile("a.txt", b"first"),
                    WorkspaceSeedFile("/workspace/a.txt", b"second"),
                ),
            )
        )
