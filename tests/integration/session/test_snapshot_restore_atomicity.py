from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from mem_sandbox.command_executor import CommandEnvironment, create_default_executor
from mem_sandbox.core import OperationLimits, SessionId, SystemClock, SystemUuidGenerator
from mem_sandbox.events import NoOpEventSink, SandboxEvent
from mem_sandbox.policy import AllowAllPolicyEngine
from mem_sandbox.secrets import NoSecretBroker
from mem_sandbox.session import (
    CreateSnapshotRequest,
    NoOpSessionResourceScope,
    ReadFileRequest,
    RestoreSnapshotRequest,
    SandboxSession,
    SessionEventDeliveryFailed,
    SessionEventSink,
    SessionExecuteRequest,
    SessionOperationTimeout,
    WriteFileRequest,
)
from mem_sandbox.snapshots import (
    InMemorySnapshotStore,
    JsonSessionSnapshotCodec,
    SandboxSnapshot,
    SnapshotCorrupt,
    SnapshotRef,
)
from mem_sandbox.workspace import MemoryWorkspace


class ControllableSnapshotStore:
    def __init__(self) -> None:
        self.delegate = InMemorySnapshotStore()
        self.corrupt_load = False
        self.block_load = False
        self.load_started = asyncio.Event()
        self.load_release = asyncio.Event()

    @property
    def process_local(self) -> bool:
        return self.delegate.process_local

    async def save(self, snapshot: SandboxSnapshot) -> SnapshotRef:
        return await self.delegate.save(snapshot)

    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot:
        if self.block_load:
            self.load_started.set()
            await self.load_release.wait()
        snapshot = await self.delegate.load(snapshot_ref)
        if not self.corrupt_load:
            return snapshot
        return replace(
            snapshot,
            payload=snapshot.payload[:-1] + bytes([snapshot.payload[-1] ^ 1]),
        )


def make_session(
    workspace: MemoryWorkspace,
    store: ControllableSnapshotStore,
    event_sink: SessionEventSink | None = None,
) -> SandboxSession:
    ids = SystemUuidGenerator()
    return SandboxSession(
        session_id=SessionId(ids.new_uuid()),
        workspace_reader=workspace,
        workspace_mutator=workspace,
        workspace_snapshots=workspace,
        command_executor=create_default_executor(workspace, workspace),
        policy_engine=AllowAllPolicyEngine(),
        secret_broker=NoSecretBroker(),
        event_sink=event_sink or NoOpEventSink(),
        snapshot_store=store,
        snapshot_codec=JsonSessionSnapshotCodec(),
        resource_scope=NoOpSessionResourceScope(),
        clock=SystemClock(),
        uuid_generator=ids,
        initial_environment=CommandEnvironment(),
    )


@pytest.mark.asyncio
async def test_corrupt_restore_leaves_workspace_cwd_and_environment_unchanged() -> None:
    workspace = MemoryWorkspace()
    store = ControllableSnapshotStore()
    session = make_session(workspace, store)
    await session.start()
    await session.write_file(
        WriteFileRequest(path="project/file.txt", content="snapshot", create_parents=True)
    )
    await session.execute(SessionExecuteRequest(command="cd project"))
    snapshot = await session.create_snapshot(CreateSnapshotRequest())
    await session.write_file(WriteFileRequest(path="file.txt", content="live"))
    before_stats = await workspace.stats()
    before_cwd = session.cwd
    before_environment = session.environment
    before_content = await session.read_file(ReadFileRequest(path="file.txt"))
    store.corrupt_load = True

    with pytest.raises(SnapshotCorrupt):
        await session.restore_snapshot(RestoreSnapshotRequest(snapshot_ref=snapshot.snapshot_ref))

    assert await workspace.stats() == before_stats
    assert session.cwd == before_cwd
    assert session.environment == before_environment
    assert (await session.read_file(ReadFileRequest(path="file.txt"))).content == (
        before_content.content
    )


@pytest.mark.asyncio
async def test_restore_holds_the_session_gate_until_complete() -> None:
    workspace = MemoryWorkspace()
    store = ControllableSnapshotStore()
    session = make_session(workspace, store)
    await session.start()
    await session.write_file(WriteFileRequest(path="file.txt", content="snapshot"))
    snapshot = await session.create_snapshot(CreateSnapshotRequest())
    await session.write_file(WriteFileRequest(path="file.txt", content="mutated"))
    store.block_load = True

    restoring = asyncio.create_task(
        session.restore_snapshot(RestoreSnapshotRequest(snapshot_ref=snapshot.snapshot_ref))
    )
    await store.load_started.wait()
    queued_write = asyncio.create_task(
        session.write_file(WriteFileRequest(path="file.txt", content="after"))
    )
    await asyncio.sleep(0)
    assert not queued_write.done()

    store.load_release.set()
    restored = await restoring
    written = await queued_write

    assert restored.metadata.workspace_revision.value == 1
    assert written.metadata.workspace_revision.value == 2
    assert (await session.read_file(ReadFileRequest(path="file.txt"))).content == "after"


@pytest.mark.asyncio
async def test_restore_timeout_cancels_publication_before_live_state_changes() -> None:
    class SlowCommitWorkspace(MemoryWorkspace):
        async def commit_restore(self, candidate: object) -> None:
            await asyncio.sleep(10)
            await super().commit_restore(candidate)

    workspace = SlowCommitWorkspace()
    store = ControllableSnapshotStore()
    session = make_session(workspace, store)
    await session.start()
    await session.write_file(WriteFileRequest(path="file.txt", content="snapshot"))
    snapshot = await session.create_snapshot(CreateSnapshotRequest())
    await session.write_file(WriteFileRequest(path="file.txt", content="live"))
    before_stats = await workspace.stats()
    before_cwd = session.cwd
    before_environment = session.environment

    with pytest.raises(SessionOperationTimeout):
        await session.restore_snapshot(
            RestoreSnapshotRequest(
                snapshot_ref=snapshot.snapshot_ref,
                limits=OperationLimits(
                    timeout_seconds=0.05,
                    terminal_event_reserve_seconds=0.02,
                ),
            )
        )

    assert await workspace.stats() == before_stats
    assert session.cwd == before_cwd
    assert session.environment == before_environment
    assert (await session.read_file(ReadFileRequest(path="file.txt"))).content == "live"


@pytest.mark.asyncio
async def test_required_restore_event_failure_preserves_published_state() -> None:
    class FailingRestoreEvents:
        def __init__(self) -> None:
            self.values: list[SandboxEvent] = []

        async def emit(self, event: SandboxEvent) -> None:
            self.values.append(event)
            if event.event_type == "snapshot.restored":
                raise RuntimeError("sink unavailable")

    workspace = MemoryWorkspace()
    store = ControllableSnapshotStore()
    events = FailingRestoreEvents()
    session = make_session(workspace, store, events)
    await session.start()
    await session.write_file(WriteFileRequest(path="file.txt", content="snapshot"))
    snapshot = await session.create_snapshot(CreateSnapshotRequest())
    await session.write_file(WriteFileRequest(path="file.txt", content="live"))

    with pytest.raises(SessionEventDeliveryFailed):
        await session.restore_snapshot(
            RestoreSnapshotRequest(snapshot_ref=snapshot.snapshot_ref)
        )

    assert [event.event_type for event in events.values[-3:]] == [
        "operation.started",
        "snapshot.restored",
        "operation.failed",
    ]
    assert (await session.read_file(ReadFileRequest(path="file.txt"))).content == "snapshot"
