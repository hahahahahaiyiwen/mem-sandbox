from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest

from mem_sandbox.command_executor import CommandFailureCode, CommandSyntaxUnsupported
from mem_sandbox.core import OperationKind, SystemClock, SystemUuidGenerator
from mem_sandbox.events import EventQuery, InMemoryEventSink, SandboxEvent, SandboxEventType
from mem_sandbox.policy import AllowAllPolicyEngine, PolicyDecision, PolicyRequest
from mem_sandbox.secrets import NoSecretBroker
from mem_sandbox.service import (
    CreateSandboxRequest,
    DefaultSessionFactory,
    InMemorySandboxService,
    InMemoryServiceSnapshotGateway,
    OwnerId,
    ResumeSandboxRequest,
    SandboxHandle,
    SandboxNotFound,
)
from mem_sandbox.session import (
    ApplyPatchRequest,
    CreateSnapshotRequest,
    ListEntriesRequest,
    ReadBytesRequest,
    ReadFileRequest,
    RestoreSnapshotRequest,
    SandboxSession,
    SessionClosed,
    SessionExecuteRequest,
    SessionExpectedFileHash,
    SessionPolicyDenied,
    SessionPolicyEngine,
    StatRequest,
    WriteBytesRequest,
    WriteFileRequest,
)
from mem_sandbox.snapshots import (
    InMemorySnapshotStore,
    JsonSessionSnapshotCodec,
    SnapshotStoreLimits,
)
from mem_sandbox.workspace import (
    ContentHash,
    ContentHashMustEqual,
    PathNotFoundError,
    PathOutsideWorkspaceError,
    StaleContentError,
)


@dataclass(frozen=True, slots=True)
class ServiceBundle:
    service: InMemorySandboxService
    store: InMemorySnapshotStore
    gateway: InMemoryServiceSnapshotGateway
    codec: JsonSessionSnapshotCodec
    events: InMemoryEventSink


def create_service_bundle(
    policy_engine: SessionPolicyEngine | None = None,
) -> ServiceBundle:
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
    gateway = InMemoryServiceSnapshotGateway(store)
    events = InMemoryEventSink(max_events=500, max_payload_bytes=4 * 1024 * 1024)
    factory = DefaultSessionFactory(
        policy_engine=policy_engine or AllowAllPolicyEngine(),
        secret_broker=NoSecretBroker(),
        event_sink=events,
        snapshot_codec=codec,
        clock=clock,
        uuid_generator=uuids,
    )
    service = InMemorySandboxService(
        session_factory=factory,
        snapshot_gateway=gateway,
        snapshot_decoder=codec,
        clock=clock,
        uuid_generator=uuids,
    )
    return ServiceBundle(service, store, gateway, codec, events)


@pytest.mark.asyncio
async def test_direct_session_reference_lifecycle() -> None:
    bundle = create_service_bundle()
    source_handle = await bundle.service.create(CreateSandboxRequest(owner_id=OwnerId("source")))
    source = await bundle.service.get_session(source_handle)
    source_session_id = source.session_id

    text = await source.write_file(
        WriteFileRequest(
            path="project/app.txt",
            content="alpha\nbeta\ngamma\n",
            create_parents=True,
        )
    )
    binary = await source.write_bytes(
        WriteBytesRequest(
            path="project/state.bin",
            content=b"\x00\xffstate",
        )
    )
    inspected = await source.execute(
        SessionExecuteRequest(command="cd project; export MODE=base; pwd; cat app.txt")
    )
    bounded = await source.read_file(ReadFileRequest(path="app.txt", start_line=2, end_line=3))
    listed = await source.list_entries(ListEntriesRequest(path="."))
    stated = await source.stat(StatRequest(path="state.bin"))

    assert text.metadata.workspace_revision.value == 1
    assert binary.metadata.workspace_revision.value == 2
    assert inspected.exit_code == 0
    assert inspected.stdout == "/workspace/project\nalpha\nbeta\ngamma\n"
    assert source.cwd.value == "/workspace/project"
    assert source.environment.get("MODE") == "base"
    assert bounded.content == "beta\ngamma"
    assert [entry.path.name for entry in listed.entries] == ["app.txt", "state.bin"]
    assert stated.entry.content_hash == ContentHash.from_bytes(b"\x00\xffstate")

    patched = await source.apply_patch(
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
    patched_hash = patched.files[0].current_hash
    assert patched_hash == ContentHash.from_bytes(b"alpha\ndelta\ngamma\n")
    assert patched.metadata.workspace_revision.value == 3

    with pytest.raises(StaleContentError):
        await source.write_file(
            WriteFileRequest(
                path="app.txt",
                content="stale\n",
                precondition=ContentHashMustEqual(bounded.content_hash),
            )
        )
    after_stale = await source.read_file(ReadFileRequest(path="app.txt"))
    assert after_stale.content == "alpha\ndelta\ngamma"
    assert after_stale.content_hash == patched_hash
    assert after_stale.metadata.workspace_revision.value == 3

    checkpoint = await source.create_snapshot(CreateSnapshotRequest())
    persisted_checkpoint = await bundle.gateway.load(checkpoint.snapshot_ref)
    checkpoint_state = bundle.codec.decode(persisted_checkpoint)
    checkpoint_root_hash = checkpoint_state.workspace.root_hash
    assert checkpoint.content_hash == persisted_checkpoint.content_hash
    assert checkpoint_state.workspace.workspace_revision.value == 3
    assert checkpoint_state.cwd.value == "/workspace/project"
    assert checkpoint_state.approved_environment.get("MODE") == "base"
    assert persisted_checkpoint.source_session_id == source_session_id
    assert persisted_checkpoint.created_by == "source"

    await source.write_file(
        WriteFileRequest(
            path="app.txt",
            content="mutated\n",
            precondition=ContentHashMustEqual(patched_hash),
        )
    )
    await source.write_bytes(WriteBytesRequest(path="state.bin", content=b"mutated"))
    await source.execute(SessionExecuteRequest(command="cd /workspace; export MODE=mutated"))
    restored = await source.restore_snapshot(
        RestoreSnapshotRequest(snapshot_ref=checkpoint.snapshot_ref)
    )

    assert source.session_id == source_session_id
    assert restored.metadata.workspace_revision.value == 3
    assert source.cwd.value == "/workspace/project"
    assert source.environment.get("MODE") == "base"
    assert (await source.read_file(ReadFileRequest(path="app.txt"))).content == (
        "alpha\ndelta\ngamma"
    )
    assert (await source.read_bytes(ReadBytesRequest(path="state.bin"))).content == b"\x00\xffstate"

    restored_again = await source.create_snapshot(CreateSnapshotRequest())
    restored_snapshot = await bundle.gateway.load(restored_again.snapshot_ref)
    restored_state = bundle.codec.decode(restored_snapshot)
    assert restored_again.content_hash == checkpoint.content_hash
    assert restored_state.workspace.root_hash == checkpoint_root_hash

    unsupported = await source.execute(
        SessionExecuteRequest(command='python -c \'open("host-canary", "w")\'')
    )
    assert unsupported.exit_code == 127
    assert unsupported.failure_code is CommandFailureCode.COMMAND_NOT_FOUND
    assert [
        entry.path.name
        for entry in (await source.list_entries(ListEntriesRequest(path="."))).entries
    ] == ["app.txt", "state.bin"]
    assert source.cwd.value == "/workspace/project"
    assert source.environment.get("MODE") == "base"

    source_events = await bundle.events.query(EventQuery(session_id=source_session_id))
    _assert_session_event_contract(source_events)

    await source.close()
    with pytest.raises(SessionClosed):
        await source.read_file(ReadFileRequest(path="app.txt"))
    await bundle.service.delete(source_handle)
    with pytest.raises(SandboxNotFound):
        await bundle.service.get_session(source_handle)

    resumed_handle = await bundle.service.resume(
        ResumeSandboxRequest(
            owner_id=OwnerId("resumed"),
            snapshot_ref=checkpoint.snapshot_ref,
        )
    )
    resumed = await bundle.service.get_session(resumed_handle)
    assert resumed_handle != source_handle
    assert resumed.session_id != source_session_id
    await _assert_checkpoint_state(resumed, patched_hash)

    resumed_write = await resumed.write_file(
        WriteFileRequest(path="continued.txt", content="resumed\n")
    )
    assert resumed_write.metadata.workspace_revision.value == 4
    resumed_snapshot = await resumed.create_snapshot(CreateSnapshotRequest())
    resumed_persisted = await bundle.gateway.load(resumed_snapshot.snapshot_ref)
    assert resumed_persisted.created_by == "resumed"
    assert persisted_checkpoint.created_by == "source"

    first_handle = await bundle.service.resume(
        ResumeSandboxRequest(
            owner_id=OwnerId("first-fork"),
            snapshot_ref=checkpoint.snapshot_ref,
        )
    )
    second_handle = await bundle.service.resume(
        ResumeSandboxRequest(
            owner_id=OwnerId("second-fork"),
            snapshot_ref=checkpoint.snapshot_ref,
        )
    )
    first = await bundle.service.get_session(first_handle)
    second = await bundle.service.get_session(second_handle)
    assert len({source_session_id, resumed.session_id, first.session_id, second.session_id}) == 4
    await _assert_checkpoint_state(first, patched_hash)
    await _assert_checkpoint_state(second, patched_hash)

    await first.write_file(WriteFileRequest(path="fork.txt", content="first\n"))
    await second.write_file(WriteFileRequest(path="fork.txt", content="second\n"))
    assert (await first.read_file(ReadFileRequest(path="fork.txt"))).content == "first"
    assert (await second.read_file(ReadFileRequest(path="fork.txt"))).content == "second"

    assert (await resumed.read_file(ReadFileRequest(path="continued.txt"))).content == "resumed"
    with pytest.raises(PathNotFoundError):
        await resumed.read_file(ReadFileRequest(path="fork.txt"))
    reloaded_checkpoint = bundle.codec.decode(await bundle.gateway.load(checkpoint.snapshot_ref))
    assert reloaded_checkpoint.workspace.root_hash == checkpoint_root_hash
    assert reloaded_checkpoint.workspace.workspace_revision.value == 3

    await _delete_and_assert_missing(bundle, first_handle)
    await _delete_and_assert_missing(bundle, second_handle)
    await _delete_and_assert_missing(bundle, resumed_handle)
    await bundle.store.delete(checkpoint.snapshot_ref)
    await bundle.store.delete(restored_again.snapshot_ref)
    await bundle.store.delete(resumed_snapshot.snapshot_ref)
    assert (await bundle.store.stats()).snapshot_count == 0
    await bundle.service.close()
    await bundle.service.close()


@pytest.mark.asyncio
async def test_policy_denial_precedes_public_mutation() -> None:
    class DenyWrites:
        def __init__(self) -> None:
            self.requests: list[PolicyRequest] = []

        async def evaluate(self, request: PolicyRequest) -> PolicyDecision:
            self.requests.append(request)
            return PolicyDecision(
                request.operation_kind is not OperationKind.WRITE_FILE,
                "writes-blocked",
                request.requested_limits,
            )

    policy = DenyWrites()
    bundle = create_service_bundle(policy)
    handle = await bundle.service.create(CreateSandboxRequest(owner_id=OwnerId("policy-test")))
    session = await bundle.service.get_session(handle)

    with pytest.raises(SessionPolicyDenied) as caught:
        await session.write_file(WriteFileRequest(path="blocked.txt", content="never"))

    assert caught.value.reason_code == "writes-blocked"
    assert [request.operation_kind for request in policy.requests] == [OperationKind.WRITE_FILE]
    assert (await session.list_entries(ListEntriesRequest(path="."))).entries == ()
    events = await bundle.events.query(EventQuery(session_id=session.session_id))
    assert [event.event_type for event in events] == [
        SandboxEventType.SANDBOX_STARTED,
        SandboxEventType.OPERATION_STARTED,
        SandboxEventType.OPERATION_FAILED,
        SandboxEventType.OPERATION_STARTED,
        SandboxEventType.OPERATION_COMPLETED,
    ]

    await bundle.service.delete(handle)
    await bundle.service.close()


@pytest.mark.asyncio
async def test_unsupported_behavior_has_no_host_or_workspace_fallback() -> None:
    bundle = create_service_bundle()
    handle = await bundle.service.create(CreateSandboxRequest(owner_id=OwnerId("unsupported-test")))
    session = await bundle.service.get_session(handle)

    unknown = await session.execute(
        SessionExecuteRequest(command="powershell -Command 'Set-Content host-canary leaked'")
    )
    assert unknown.exit_code == 127
    assert unknown.failure_code is CommandFailureCode.COMMAND_NOT_FOUND

    with pytest.raises(CommandSyntaxUnsupported):
        await session.execute(SessionExecuteRequest(command="touch created || echo fallback"))
    with pytest.raises(PathOutsideWorkspaceError):
        await session.write_file(WriteFileRequest(path="/outside/host-canary", content="leaked"))

    assert (await session.list_entries(ListEntriesRequest(path="."))).entries == ()
    assert session.cwd.value == "/workspace"

    await bundle.service.delete(handle)
    await bundle.service.close()


async def _assert_checkpoint_state(
    session: SandboxSession,
    expected_text_hash: ContentHash,
) -> None:
    text = await session.read_file(ReadFileRequest(path="app.txt"))
    binary = await session.read_bytes(ReadBytesRequest(path="state.bin"))
    assert text.content == "alpha\ndelta\ngamma"
    assert text.content_hash == expected_text_hash
    assert binary.content == b"\x00\xffstate"
    assert text.metadata.workspace_revision.value == 3
    assert binary.metadata.workspace_revision.value == 3
    assert session.cwd.value == "/workspace/project"
    assert session.environment.get("MODE") == "base"


async def _delete_and_assert_missing(
    bundle: ServiceBundle,
    handle: SandboxHandle,
) -> None:
    await bundle.service.delete(handle)
    with pytest.raises(SandboxNotFound):
        await bundle.service.get_session(handle)
    with pytest.raises(SandboxNotFound):
        await bundle.service.delete(handle)


def _assert_session_event_contract(events: tuple[SandboxEvent, ...]) -> None:
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert events[0].event_type is SandboxEventType.SANDBOX_STARTED
    for event_type in (
        SandboxEventType.SNAPSHOT_CREATED,
        SandboxEventType.SNAPSHOT_RESTORED,
    ):
        matching = [index for index, event in enumerate(events) if event.event_type is event_type]
        assert matching
        for index in matching:
            snapshot_event = events[index]
            started = events[index - 1]
            completed = events[index + 1]
            assert started.event_type is SandboxEventType.OPERATION_STARTED
            assert completed.event_type is SandboxEventType.OPERATION_COMPLETED
            assert started.operation_id == snapshot_event.operation_id == completed.operation_id
