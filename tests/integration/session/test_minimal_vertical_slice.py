from __future__ import annotations

from datetime import timedelta

import pytest

from mem_sandbox.command_executor import (
    CommandEnvironment,
    CommandLimits,
    EnvironmentValue,
    create_default_executor,
)
from mem_sandbox.core import SessionId, SystemClock, SystemUuidGenerator
from mem_sandbox.events import SandboxEvent
from mem_sandbox.policy import AllowAllPolicyEngine
from mem_sandbox.secrets import NoSecretBroker
from mem_sandbox.session import (
    ApplyPatchRequest,
    CreateSnapshotRequest,
    NoOpSessionResourceScope,
    ReadFileRequest,
    RestoreSnapshotRequest,
    SandboxSession,
    SessionExecuteRequest,
    SessionExpectedFileHash,
    WriteFileRequest,
)
from mem_sandbox.snapshots import (
    InMemorySnapshotStore,
    JsonSessionSnapshotCodec,
    SnapshotStoreLimits,
)
from mem_sandbox.workspace import ContentHashMustEqual, MemoryWorkspace


class CollectingEventSink:
    def __init__(self) -> None:
        self.events: list[SandboxEvent] = []

    async def emit(self, event: SandboxEvent) -> None:
        self.events.append(event)


@pytest.mark.asyncio
async def test_minimal_session_vertical_slice() -> None:
    workspace = MemoryWorkspace()
    events = CollectingEventSink()
    uuid_generator = SystemUuidGenerator()
    session = SandboxSession(
        session_id=SessionId(uuid_generator.new_uuid()),
        workspace_reader=workspace,
        workspace_mutator=workspace,
        workspace_snapshots=workspace,
        command_executor=create_default_executor(workspace, workspace),
        policy_engine=AllowAllPolicyEngine(),
        secret_broker=NoSecretBroker(),
        event_sink=events,
        snapshot_store=InMemorySnapshotStore(
            default_ttl=timedelta(days=1),
            limits=SnapshotStoreLimits(
                max_snapshots=10,
                max_total_payload_bytes=64 * 1024 * 1024,
            ),
            clock=SystemClock(),
        ),
        snapshot_codec=JsonSessionSnapshotCodec(),
        resource_scope=NoOpSessionResourceScope(),
        clock=SystemClock(),
        uuid_generator=uuid_generator,
        initial_environment=CommandEnvironment((EnvironmentValue("PROJECT", "demo"),)),
    )
    await session.start()

    written = await session.write_file(
        WriteFileRequest(
            path="project/app.txt",
            content="alpha\nbeta\ngamma\n",
            create_parents=True,
        )
    )
    assert written.current_hash is not None
    bounded = await session.read_file(
        ReadFileRequest(path="project/app.txt", start_line=2, end_line=3)
    )
    assert bounded.content == "beta\ngamma"

    inspected = await session.execute(
        SessionExecuteRequest(
            command="cd project; pwd; echo $PROJECT; cat app.txt",
            command_limits=CommandLimits(timeout_seconds=5),
        )
    )
    assert inspected.exit_code == 0
    assert inspected.stdout == "/workspace/project\ndemo\nalpha\nbeta\ngamma\n"
    assert session.cwd.value == "/workspace/project"

    patched = await session.apply_patch(
        ApplyPatchRequest(
            patch=(
                "--- /workspace/project/app.txt\n"
                "+++ /workspace/project/app.txt\n"
                "@@ -1,3 +1,3 @@\n"
                " alpha\n"
                "-beta\n"
                "+delta\n"
                " gamma\n"
            ),
            expected_hashes=(
                SessionExpectedFileHash(
                    path="app.txt",
                    content_hash=bounded.content_hash,
                ),
            ),
        )
    )
    assert patched.files[0].current_hash != bounded.content_hash

    snapshot = await session.create_snapshot(CreateSnapshotRequest())
    await session.write_file(
        WriteFileRequest(
            path="app.txt",
            content="mutated\n",
            precondition=ContentHashMustEqual(patched.files[0].current_hash),
        )
    )
    await session.execute(SessionExecuteRequest(command="cd /workspace"))
    mutated_revision = (await workspace.stats()).revision

    restored = await session.restore_snapshot(
        RestoreSnapshotRequest(snapshot_ref=snapshot.snapshot_ref)
    )
    final = await session.read_file(ReadFileRequest(path="app.txt"))

    assert final.content == "alpha\ndelta\ngamma"
    assert final.content_hash == patched.files[0].current_hash
    assert session.cwd.value == "/workspace/project"
    assert session.environment.get("PROJECT") == "demo"
    assert restored.metadata.workspace_revision == snapshot.metadata.workspace_revision
    assert restored.metadata.workspace_revision < mutated_revision
    operation_events = [
        event for event in events.events if event.event_type.startswith("operation.")
    ]
    snapshot_events = [event for event in events.events if event.event_type.startswith("snapshot.")]
    assert [event.sequence for event in events.events] == list(range(1, len(events.events) + 1))
    assert len(operation_events) == 18
    assert [event.event_type for event in snapshot_events] == [
        "snapshot.created",
        "snapshot.restored",
    ]
    for snapshot_event in snapshot_events:
        event_index = events.events.index(snapshot_event)
        assert events.events[event_index - 1].event_type == "operation.started"
        assert events.events[event_index + 1].event_type == "operation.completed"
    assert all(
        operation_events[index].event_type == "operation.started"
        and operation_events[index + 1].event_type == "operation.completed"
        for index in range(0, len(operation_events), 2)
    )

    await session.close()
