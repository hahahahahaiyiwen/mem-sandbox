from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

import pytest

from mem_sandbox.command_executor import (
    CommandExecutionContext,
    CommandFailureCode,
    CommandLimits,
    EnvironmentChange,
    ExecuteRequest,
    ExecuteResult,
)
from mem_sandbox.core import OperationLimits, Revision, SessionId
from mem_sandbox.events import (
    EventDeliveryDiagnostic,
    EventDeliveryMode,
    EventDeliveryPolicy,
    EventDispatcher,
    SandboxEvent,
)
from mem_sandbox.policy import AllowAllPolicyEngine, PolicyDecision, PolicyRequest
from mem_sandbox.secrets import (
    NoSecretBroker,
    SecretAccessRequest,
    SecretLease,
)
from mem_sandbox.session import (
    ApplyPatchRequest,
    CreateSnapshotRequest,
    ListEntriesRequest,
    NoOpSessionResourceScope,
    ReadBytesRequest,
    ReadFileRequest,
    SandboxSession,
    SandboxSessionState,
    SessionCleanupFailed,
    SessionClosed,
    SessionEventDeliveryFailed,
    SessionEventSink,
    SessionExecuteRequest,
    SessionFailed,
    SessionNotRunning,
    SessionOperationCancelled,
    SessionOperationTimeout,
    SessionPolicyDenied,
    SessionPolicyEngine,
    SessionResourceScope,
    SessionSecretBroker,
    SessionSnapshotCodec,
    SessionSnapshotStore,
    SessionStartInvalid,
    StatRequest,
    WriteBytesRequest,
    WriteFileRequest,
)
from mem_sandbox.snapshots import (
    JsonSessionSnapshotCodec,
    SandboxSnapshot,
    SessionSnapshotState,
    SnapshotPayload,
    SnapshotRef,
)
from mem_sandbox.workspace import (
    ContentHash,
    NodeKind,
    PatchedFile,
    PreparedWorkspaceRestore,
    SandboxPath,
    WorkspaceBinaryResult,
    WorkspaceEntry,
    WorkspaceMutation,
    WorkspacePatchResult,
    WorkspaceRangeRequest,
    WorkspaceRangeResult,
    WorkspaceSnapshotData,
    WorkspaceStats,
    WorkspaceWriteRequest,
)


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 1, 12, tzinfo=UTC)


class Uuids:
    def __init__(self) -> None:
        self._next = 1

    def new_uuid(self) -> UUID:
        value = UUID(int=self._next)
        self._next += 1
        return value


class Events:
    def __init__(self) -> None:
        self.values: list[SandboxEvent] = []

    async def emit(self, event: SandboxEvent) -> None:
        self.values.append(event)


class Store:
    @property
    def process_local(self) -> bool:
        return True

    async def save(self, snapshot: SandboxSnapshot) -> SnapshotRef:
        raise AssertionError

    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot:
        raise AssertionError


class Workspace:
    def __init__(self) -> None:
        self.revision = Revision(0)
        self.root = SandboxPath.root()
        self.hash = ContentHash.from_bytes(b"")
        self.writes: list[object] = []

    def resolve_path(self, value: str, *, cwd: SandboxPath | None = None) -> SandboxPath:
        return SandboxPath.resolve(value, cwd=cwd)

    async def stats(self) -> WorkspaceStats:
        return WorkspaceStats(0, 1, self.revision, self.hash)

    async def stat(self, path: SandboxPath) -> WorkspaceEntry:
        return WorkspaceEntry(path, NodeKind.FILE, 0, self.hash, self.revision)

    async def list(self, path: SandboxPath) -> tuple[WorkspaceEntry, ...]:
        return ()

    async def read_range(self, request: WorkspaceRangeRequest) -> WorkspaceRangeResult:
        path = request.path
        return WorkspaceRangeResult(path, "line", 1, 1, 1, self.hash, self.revision)

    async def read_bytes(self, path: SandboxPath) -> WorkspaceBinaryResult:
        return WorkspaceBinaryResult(path, b"", self.hash, self.revision)

    async def write(self, request: WorkspaceWriteRequest) -> WorkspaceMutation:
        self.writes.append(request)
        self.revision = self.revision.next()
        path = request.path
        current_hash = ContentHash.from_bytes(request.content)
        return WorkspaceMutation(
            path,
            True,
            True,
            None,
            current_hash,
            WorkspaceStats(1, 2, self.revision, current_hash),
        )

    async def patch(self, request: object) -> WorkspacePatchResult:
        self.revision = self.revision.next()
        path = SandboxPath.resolve("/workspace/file.txt")
        current = ContentHash.from_bytes(b"patched")
        return WorkspacePatchResult(
            (PatchedFile(path, self.hash, current),),
            WorkspaceStats(7, 2, self.revision, current),
        )

    async def export(self) -> WorkspaceSnapshotData:
        raise AssertionError

    async def prepare_restore(
        self,
        data: WorkspaceSnapshotData,
        *,
        required_directory: SandboxPath,
    ) -> PreparedWorkspaceRestore:
        return PreparedWorkspaceRestore(object(), object())

    async def commit_restore(self, candidate: PreparedWorkspaceRestore) -> None:
        raise AssertionError


class Executor:
    def __init__(self) -> None:
        self.requests: list[tuple[ExecuteRequest, CommandExecutionContext]] = []

    async def execute(
        self,
        request: ExecuteRequest,
        context: CommandExecutionContext,
    ) -> ExecuteResult:
        self.requests.append((request, context))
        return ExecuteResult(
            2,
            CommandFailureCode.INVALID_ARGUMENT,
            "",
            "bad\n",
            0,
            4,
            False,
            False,
            1.0,
            SandboxPath.resolve("/workspace/project"),
            (),
        )


def make_session(
    policy_engine: SessionPolicyEngine | None = None,
    *,
    event_sink: SessionEventSink | None = None,
    resource_scope: SessionResourceScope | None = None,
    secret_broker: SessionSecretBroker | None = None,
    snapshot_store: SessionSnapshotStore | None = None,
    snapshot_codec: SessionSnapshotCodec | None = None,
) -> tuple[SandboxSession, Workspace, Executor, Events]:
    workspace = Workspace()
    executor = Executor()
    events = Events()
    return (
        SandboxSession(
            session_id=SessionId(UUID(int=100)),
            workspace_reader=workspace,
            workspace_mutator=workspace,
            workspace_snapshots=workspace,
            command_executor=executor,
            policy_engine=policy_engine or AllowAllPolicyEngine(),
            secret_broker=secret_broker or NoSecretBroker(),
            event_sink=event_sink or events,
            snapshot_store=snapshot_store or Store(),
            snapshot_codec=snapshot_codec or JsonSessionSnapshotCodec(),
            resource_scope=resource_scope or NoOpSessionResourceScope(),
            clock=FixedClock(),
            uuid_generator=Uuids(),
        ),
        workspace,
        executor,
        events,
    )


@pytest.mark.asyncio
async def test_operations_require_explicit_start_and_emit_ordered_events() -> None:
    session, workspace, executor, events = make_session()
    with pytest.raises(SessionNotRunning):
        await session.read_file(ReadFileRequest(path="file.txt"))

    await session.start()
    read = await session.read_file(ReadFileRequest(path="file.txt"))
    write = await session.write_file(WriteFileRequest(path="file.txt", content="new"))
    patch = await session.apply_patch(ApplyPatchRequest(patch="patch"))
    execute = await session.execute(
        SessionExecuteRequest(command="bad", command_limits=CommandLimits(timeout_seconds=5))
    )
    binary = await session.read_bytes(ReadBytesRequest(path="file.txt"))
    binary_write = await session.write_bytes(WriteBytesRequest(path="binary.dat", content=b"\x00"))
    entry = await session.stat(StatRequest(path="file.txt"))
    listing = await session.list_entries(ListEntriesRequest(path="."))

    assert session.state is SandboxSessionState.RUNNING
    assert read.content == "line"
    assert write.metadata.workspace_revision == Revision(1)
    assert patch.metadata.workspace_revision == Revision(2)
    assert execute.exit_code == 2
    assert binary.content == b""
    assert binary_write.current_hash == ContentHash.from_bytes(b"\x00")
    assert entry.path.value == "/workspace/project/file.txt"
    assert listing.entries == ()
    assert session.cwd == SandboxPath.resolve("/workspace/project")
    assert executor.requests[0][0].limits.timeout_seconds <= 5
    assert events.values[0].event_type == "sandbox.started"
    assert [event.event_type for event in events.values[1:]] == [
        item for _ in range(8) for item in ("operation.started", "operation.completed")
    ]
    assert [event.sequence for event in events.values] == list(range(1, 18))
    assert len(workspace.writes) == 2


@pytest.mark.asyncio
async def test_policy_denial_calls_no_protected_collaborator() -> None:
    class Deny:
        async def evaluate(self, request: PolicyRequest) -> PolicyDecision:
            return PolicyDecision(False, "blocked", OperationLimits())

    session, workspace, executor, events = make_session(Deny())
    await session.start()

    with pytest.raises(SessionPolicyDenied) as caught:
        await session.write_file(WriteFileRequest(path="file.txt", content="never"))

    assert caught.value.reason_code == "blocked"
    assert workspace.writes == []
    assert executor.requests == []
    assert events.values[-1].event_type == "operation.failed"


@pytest.mark.asyncio
async def test_close_cutoff_rejects_a_queued_operation_and_closes_once() -> None:
    session, workspace, _, _ = make_session()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_read(
        request: WorkspaceRangeRequest,
        entered_event: asyncio.Event = entered,
    ) -> WorkspaceRangeResult:
        entered_event.set()
        await release.wait()
        return WorkspaceRangeResult(
            request.path,
            "line",
            1,
            1,
            1,
            workspace.hash,
            workspace.revision,
        )

    workspace.read_range = blocked_read  # type: ignore[method-assign]
    await session.start()
    active = asyncio.create_task(session.read_file(ReadFileRequest(path="a")))
    await entered.wait()
    queued = asyncio.create_task(session.read_file(ReadFileRequest(path="b")))
    close = asyncio.create_task(session.close())
    await asyncio.sleep(0)
    release.set()

    await active
    with pytest.raises(Exception) as caught:
        await queued
    assert caught.value.__class__.__name__ in {"SessionClosing", "SessionClosed"}
    await close
    assert session.state is SandboxSessionState.CLOSED


@pytest.mark.asyncio
async def test_lifecycle_rejects_restart_and_operations_after_terminal_states() -> None:
    session, _, _, _ = make_session()
    await session.start()
    with pytest.raises(SessionStartInvalid):
        await session.start()
    await session.close()
    with pytest.raises(SessionStartInvalid):
        await session.start()
    with pytest.raises(SessionClosed):
        await session.read_file(ReadFileRequest(path="file"))

    class FailingStart:
        async def emit(self, event: SandboxEvent) -> None:
            if event.event_type == "sandbox.started":
                raise RuntimeError("unavailable")

    failed, _, _, _ = make_session(event_sink=FailingStart())
    with pytest.raises(SessionEventDeliveryFailed):
        await failed.start()
    with pytest.raises(SessionFailed):
        await failed.read_file(ReadFileRequest(path="file"))


@pytest.mark.asyncio
async def test_close_requested_during_start_preserves_the_admission_cutoff() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    class BlockingStart:
        async def emit(self, event: SandboxEvent) -> None:
            if event.event_type == "sandbox.started":
                entered.set()
                await release.wait()

    session, _, _, _ = make_session(event_sink=BlockingStart())
    starting = asyncio.create_task(session.start())
    await entered.wait()
    closing = asyncio.create_task(session.close())
    await asyncio.sleep(0)
    assert session.state is SandboxSessionState.CLOSING
    release.set()
    await starting
    await closing
    assert session.state is SandboxSessionState.CLOSED


@pytest.mark.asyncio
async def test_concurrent_close_waits_for_terminal_event_and_observes_primary_failure() -> None:
    closed_event_started = asyncio.Event()
    release_closed_event = asyncio.Event()

    class BlockingClosedEvent:
        async def emit(self, event: SandboxEvent) -> None:
            if event.event_type == "sandbox.closed":
                closed_event_started.set()
                await release_closed_event.wait()

    class FailingScope:
        async def close(self) -> None:
            raise RuntimeError("cleanup")

    session, _, _, _ = make_session(
        event_sink=BlockingClosedEvent(),
        resource_scope=FailingScope(),
    )
    await session.start()
    first = asyncio.create_task(session.close())
    await closed_event_started.wait()
    assert session.state is SandboxSessionState.CLOSED
    second = asyncio.create_task(session.close())
    await asyncio.sleep(0)
    assert not second.done()
    release_closed_event.set()

    failures: list[SessionCleanupFailed] = []
    for closing in (first, second):
        with pytest.raises(SessionCleanupFailed) as caught:
            await closing
        failures.append(caught.value)
    assert failures[0] is failures[1]
    await session.close()


@pytest.mark.asyncio
async def test_execute_timeout_during_stats_does_not_commit_transient_state() -> None:
    session, workspace, executor, events = make_session()
    initial_cwd = session.cwd
    initial_environment = session.environment

    async def execute_with_transient_state(
        request: ExecuteRequest,
        context: CommandExecutionContext,
    ) -> ExecuteResult:
        executor.requests.append((request, context))
        return ExecuteResult(
            0,
            None,
            "",
            "",
            0,
            0,
            False,
            False,
            0.0,
            SandboxPath.resolve("/workspace/changed"),
            (EnvironmentChange("CHANGED", "yes"),),
        )

    async def slow_stats() -> WorkspaceStats:
        await asyncio.sleep(10)
        raise AssertionError

    executor.execute = execute_with_transient_state  # type: ignore[method-assign]
    workspace.stats = slow_stats  # type: ignore[method-assign]
    await session.start()
    with pytest.raises(SessionOperationTimeout):
        await session.execute(
            SessionExecuteRequest(
                command="pwd",
                limits=OperationLimits(
                    timeout_seconds=0.2,
                    terminal_event_reserve_seconds=0.05,
                ),
            )
        )

    assert session.cwd == initial_cwd
    assert session.environment == initial_environment
    assert events.values[-1].event_type == "operation.timed_out"


@pytest.mark.asyncio
async def test_snapshot_metadata_comes_from_codec_and_store_capabilities() -> None:
    class CustomCodec(JsonSessionSnapshotCodec):
        def encode(self, state: SessionSnapshotState) -> SnapshotPayload:
            return replace(super().encode(state), format_name="custom")

    class RecordingStore:
        def __init__(self) -> None:
            self.saved: SandboxSnapshot | None = None

        @property
        def process_local(self) -> bool:
            return False

        async def save(self, snapshot: SandboxSnapshot) -> SnapshotRef:
            self.saved = snapshot
            return SnapshotRef(snapshot.snapshot_id)

        async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot:
            raise AssertionError(snapshot_ref)

    store = RecordingStore()
    session, workspace, _, _ = make_session(
        snapshot_store=store,
        snapshot_codec=CustomCodec(),
    )

    async def export() -> WorkspaceSnapshotData:
        return WorkspaceSnapshotData(
            encoded=b"workspace",
            schema_version=1,
            integrity_hash=workspace.hash,
            workspace_revision=workspace.revision,
            root_hash=workspace.hash,
        )

    workspace.export = export  # type: ignore[method-assign]
    await session.start()
    await session.create_snapshot(CreateSnapshotRequest())

    assert store.saved is not None
    assert store.saved.metadata.format_name == "custom"
    assert store.saved.metadata.process_local is False


@pytest.mark.asyncio
async def test_required_snapshot_event_failure_preserves_the_saved_snapshot() -> None:
    class RecordingStore:
        def __init__(self) -> None:
            self.saved: SandboxSnapshot | None = None

        @property
        def process_local(self) -> bool:
            return True

        async def save(self, snapshot: SandboxSnapshot) -> SnapshotRef:
            self.saved = snapshot
            return SnapshotRef(snapshot.snapshot_id)

        async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot:
            raise AssertionError(snapshot_ref)

    class FailingSnapshotEvents:
        def __init__(self) -> None:
            self.values: list[SandboxEvent] = []

        async def emit(self, event: SandboxEvent) -> None:
            self.values.append(event)
            if event.event_type == "snapshot.created":
                raise RuntimeError("sink unavailable")

    store = RecordingStore()
    events = FailingSnapshotEvents()
    session, workspace, _, _ = make_session(
        event_sink=events,
        snapshot_store=store,
    )

    async def export() -> WorkspaceSnapshotData:
        return WorkspaceSnapshotData(
            encoded=b"workspace",
            schema_version=1,
            integrity_hash=workspace.hash,
            workspace_revision=workspace.revision,
            root_hash=workspace.hash,
        )

    workspace.export = export  # type: ignore[method-assign]
    await session.start()

    with pytest.raises(SessionEventDeliveryFailed):
        await session.create_snapshot(CreateSnapshotRequest())

    assert store.saved is not None
    assert [event.event_type for event in events.values[-3:]] == [
        "operation.started",
        "snapshot.created",
        "operation.failed",
    ]


@pytest.mark.asyncio
async def test_snapshot_event_timeout_preserves_budget_for_failed_terminal() -> None:
    class RecordingStore:
        def __init__(self) -> None:
            self.saved: SandboxSnapshot | None = None

        @property
        def process_local(self) -> bool:
            return True

        async def save(self, snapshot: SandboxSnapshot) -> SnapshotRef:
            self.saved = snapshot
            return SnapshotRef(snapshot.snapshot_id)

        async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot:
            raise AssertionError(snapshot_ref)

    class BlockingSnapshotEvents:
        def __init__(self) -> None:
            self.values: list[SandboxEvent] = []

        async def emit(self, event: SandboxEvent) -> None:
            self.values.append(event)
            if event.event_type == "snapshot.created":
                await asyncio.sleep(10)

    store = RecordingStore()
    events = BlockingSnapshotEvents()
    session, workspace, _, _ = make_session(
        event_sink=events,
        snapshot_store=store,
    )

    async def export() -> WorkspaceSnapshotData:
        return WorkspaceSnapshotData(
            encoded=b"workspace",
            schema_version=1,
            integrity_hash=workspace.hash,
            workspace_revision=workspace.revision,
            root_hash=workspace.hash,
        )

    workspace.export = export  # type: ignore[method-assign]
    await session.start()

    with pytest.raises(SessionEventDeliveryFailed):
        await session.create_snapshot(
            CreateSnapshotRequest(
                limits=OperationLimits(
                    timeout_seconds=0.3,
                    terminal_event_reserve_seconds=0.2,
                )
            )
        )

    assert store.saved is not None
    assert [event.event_type for event in events.values[-3:]] == [
        "operation.started",
        "snapshot.created",
        "operation.failed",
    ]


@pytest.mark.asyncio
async def test_best_effort_snapshot_event_failure_keeps_operation_successful() -> None:
    diagnostics: list[EventDeliveryDiagnostic] = []

    class RecordingStore:
        def __init__(self) -> None:
            self.saved: SandboxSnapshot | None = None

        @property
        def process_local(self) -> bool:
            return True

        async def save(self, snapshot: SandboxSnapshot) -> SnapshotRef:
            self.saved = snapshot
            return SnapshotRef(snapshot.snapshot_id)

        async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot:
            raise AssertionError(snapshot_ref)

    class FailingSnapshotSink:
        def __init__(self) -> None:
            self.values: list[SandboxEvent] = []

        async def emit(self, event: SandboxEvent) -> None:
            self.values.append(event)
            if event.event_type == "snapshot.created":
                raise RuntimeError("sink unavailable")

    class Diagnostics:
        def report(self, diagnostic: EventDeliveryDiagnostic) -> None:
            diagnostics.append(diagnostic)

    store = RecordingStore()
    sink = FailingSnapshotSink()
    dispatcher = EventDispatcher(
        sink,
        delivery_policy=EventDeliveryPolicy(
            mode=EventDeliveryMode.BEST_EFFORT,
            max_pending_events=16,
        ),
        diagnostic_handler=Diagnostics(),
    )
    session, workspace, _, _ = make_session(
        event_sink=dispatcher,
        snapshot_store=store,
    )

    async def export() -> WorkspaceSnapshotData:
        return WorkspaceSnapshotData(
            encoded=b"workspace",
            schema_version=1,
            integrity_hash=workspace.hash,
            workspace_revision=workspace.revision,
            root_hash=workspace.hash,
        )

    workspace.export = export  # type: ignore[method-assign]
    await session.start()
    result = await session.create_snapshot(CreateSnapshotRequest())
    await dispatcher.flush()

    assert store.saved is not None
    assert result.snapshot_ref == SnapshotRef(store.saved.snapshot_id)
    assert [event.event_type for event in sink.values[-3:]] == [
        "operation.started",
        "snapshot.created",
        "operation.completed",
    ]
    assert len(diagnostics) == 1
    assert diagnostics[0].failure_code == "event_delivery_failed"

    await session.close()
    await dispatcher.flush()
    await dispatcher.close()


@pytest.mark.asyncio
async def test_best_effort_start_failure_does_not_change_session_or_operation() -> None:
    diagnostics: list[EventDeliveryDiagnostic] = []

    class FailingStartSink:
        def __init__(self) -> None:
            self.values: list[SandboxEvent] = []

        async def emit(self, event: SandboxEvent) -> None:
            self.values.append(event)
            if event.event_type in {"sandbox.started", "operation.started"}:
                raise RuntimeError("sink unavailable")

    class Diagnostics:
        def report(self, diagnostic: EventDeliveryDiagnostic) -> None:
            diagnostics.append(diagnostic)

    sink = FailingStartSink()
    dispatcher = EventDispatcher(
        sink,
        delivery_policy=EventDeliveryPolicy(
            mode=EventDeliveryMode.BEST_EFFORT,
            max_pending_events=8,
        ),
        diagnostic_handler=Diagnostics(),
    )
    session, _, _, _ = make_session(event_sink=dispatcher)

    await session.start()
    result = await session.read_file(ReadFileRequest(path="file"))
    await dispatcher.flush()

    assert session.state is SandboxSessionState.RUNNING
    assert result.content == "line"
    assert [event.event_type for event in sink.values] == [
        "sandbox.started",
        "operation.started",
        "operation.completed",
    ]
    assert len(diagnostics) == 2

    await session.close()
    await dispatcher.flush()
    await dispatcher.close()


@pytest.mark.asyncio
async def test_session_never_owns_event_sink_flush_or_close() -> None:
    class OwnedSink:
        def __init__(self) -> None:
            self.flush_calls = 0
            self.close_calls = 0

        async def emit(self, event: SandboxEvent) -> None:
            _ = event

        async def flush(self) -> None:
            self.flush_calls += 1

        async def close(self) -> None:
            self.close_calls += 1

    sink = OwnedSink()
    session, _, _, _ = make_session(event_sink=sink)

    await session.start()
    await session.close()

    assert sink.flush_calls == 0
    assert sink.close_calls == 0


@pytest.mark.asyncio
async def test_queue_timeout_and_cancellation_allocate_no_operation_or_event() -> None:
    session, workspace, _, events = make_session()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def blocked_read(
        request: WorkspaceRangeRequest,
        entered_event: asyncio.Event = entered,
    ) -> WorkspaceRangeResult:
        entered_event.set()
        await release.wait()
        return WorkspaceRangeResult(
            request.path,
            "line",
            1,
            1,
            1,
            workspace.hash,
            workspace.revision,
        )

    workspace.read_range = blocked_read  # type: ignore[method-assign]
    await session.start()
    active = asyncio.create_task(session.read_file(ReadFileRequest(path="active")))
    await entered.wait()
    before = len(events.values)

    with pytest.raises(SessionOperationTimeout) as timed_out:
        await session.read_file(
            ReadFileRequest(
                path="queued",
                limits=OperationLimits(
                    timeout_seconds=0.03,
                    terminal_event_reserve_seconds=0.01,
                ),
            )
        )
    assert timed_out.value.operation_id is None
    assert len(events.values) == before

    queued = asyncio.create_task(session.read_file(ReadFileRequest(path="cancelled")))
    await asyncio.sleep(0)
    queued.cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued
    assert len(events.values) == before

    release.set()
    await active


@pytest.mark.asyncio
async def test_native_and_cooperative_cancellation_emit_one_terminal_event() -> None:
    for native in (False, True):
        session, workspace, _, events = make_session()
        entered = asyncio.Event()

        async def blocked_read(
            request: WorkspaceRangeRequest,
            entered_event: asyncio.Event = entered,
        ) -> WorkspaceRangeResult:
            entered_event.set()
            await asyncio.Event().wait()
            raise AssertionError

        workspace.read_range = blocked_read  # type: ignore[method-assign]
        await session.start()
        cancellation = asyncio.Event()
        task = asyncio.create_task(
            session.read_file(ReadFileRequest(path="file", cancellation=cancellation))
        )
        await entered.wait()
        if native:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            cancellation.set()
            with pytest.raises(SessionOperationCancelled) as caught:
                await task
            assert caught.value.operation_id is not None
        assert [event.event_type for event in events.values[-2:]] == [
            "operation.started",
            "operation.cancelled",
        ]


@pytest.mark.asyncio
async def test_policy_narrowed_timeout_uses_original_start_and_reserved_terminal_window() -> None:
    class Narrow:
        async def evaluate(self, request: PolicyRequest) -> PolicyDecision:
            await asyncio.sleep(0.015)
            return PolicyDecision(
                True,
                "narrow",
                OperationLimits(
                    timeout_seconds=0.03,
                    terminal_event_reserve_seconds=0.01,
                ),
            )

    session, workspace, _, events = make_session(Narrow())

    async def slow_read(request: WorkspaceRangeRequest) -> WorkspaceRangeResult:
        await asyncio.sleep(10)
        raise AssertionError

    workspace.read_range = slow_read  # type: ignore[method-assign]
    await session.start()
    with pytest.raises(SessionOperationTimeout):
        await session.read_file(
            ReadFileRequest(
                path="file",
                limits=OperationLimits(
                    timeout_seconds=1.0,
                    terminal_event_reserve_seconds=0.01,
                ),
            )
        )
    assert events.values[-1].event_type == "operation.timed_out"


@pytest.mark.asyncio
async def test_required_event_failures_follow_start_and_terminal_rules() -> None:
    class FailingEvents:
        def __init__(self, failed_type: str) -> None:
            self.failed_type = failed_type
            self.values: list[SandboxEvent] = []

        async def emit(self, event: SandboxEvent) -> None:
            self.values.append(event)
            if event.event_type == self.failed_type:
                raise RuntimeError("sink unavailable")

    start_events = FailingEvents("operation.started")
    session, workspace, _, _ = make_session(event_sink=start_events)
    await session.start()
    with pytest.raises(SessionEventDeliveryFailed):
        await session.write_file(WriteFileRequest(path="file", content="never"))
    assert workspace.writes == []
    assert [event.event_type for event in start_events.values] == [
        "sandbox.started",
        "operation.started",
    ]

    terminal_events = FailingEvents("operation.completed")
    session, workspace, _, _ = make_session(event_sink=terminal_events)
    await session.start()
    with pytest.raises(SessionEventDeliveryFailed):
        await session.write_file(WriteFileRequest(path="file", content="committed"))
    assert len(workspace.writes) == 1
    assert terminal_events.values[-1].event_type == "operation.completed"


@pytest.mark.asyncio
async def test_start_failure_marks_failed_and_close_scope_runs_exactly_once() -> None:
    class FailingStart:
        async def emit(self, event: SandboxEvent) -> None:
            if event.event_type == "sandbox.started":
                raise RuntimeError("unavailable")

    class Scope:
        def __init__(self, *, fail: bool = False) -> None:
            self.calls = 0
            self.fail = fail

        async def close(self) -> None:
            self.calls += 1
            if self.fail:
                raise RuntimeError("cleanup")

    scope = Scope()
    session, _, _, _ = make_session(event_sink=FailingStart(), resource_scope=scope)
    with pytest.raises(SessionEventDeliveryFailed):
        await session.start()
    assert session.state is SandboxSessionState.FAILED
    await asyncio.gather(session.close(), session.close())
    assert session.state is SandboxSessionState.CLOSED
    assert scope.calls == 1
    await session.close()
    assert scope.calls == 1

    failing_scope = Scope(fail=True)
    session, _, _, _ = make_session(resource_scope=failing_scope)
    await session.start()
    with pytest.raises(SessionCleanupFailed):
        await session.close()
    assert session.state is SandboxSessionState.CLOSED
    assert failing_scope.calls == 1
    await session.close()
    assert failing_scope.calls == 1


@pytest.mark.asyncio
async def test_public_operations_never_touch_the_no_secret_broker() -> None:
    class SecretSpy:
        def __init__(self) -> None:
            self.calls = 0

        async def lease(self, request: SecretAccessRequest) -> SecretLease:
            self.calls += 1
            raise AssertionError

    spy = SecretSpy()
    session, _, _, _ = make_session(secret_broker=spy)
    await session.start()
    await session.read_file(ReadFileRequest(path="file"))
    await session.write_file(WriteFileRequest(path="file", content="content"))
    await session.execute(SessionExecuteRequest(command="bad"))
    assert spy.calls == 0
