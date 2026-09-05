from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import cast
from uuid import UUID

import pytest

from mem_sandbox.command_executor import (
    CommandExecutionContext,
    ExecuteRequest,
    ExecuteResult,
    create_default_executor,
)
from mem_sandbox.core import OperationKind, OperationLimits, Revision, SessionId
from mem_sandbox.events import SandboxEvent, SandboxEventType
from mem_sandbox.policy import PathMutationPolicyContext, PolicyDecision, PolicyRequest
from mem_sandbox.secrets import NoSecretBroker
from mem_sandbox.session import (
    CreateDirectoryRequest,
    FileMutationResult,
    NoOpSessionResourceScope,
    PathMutationResult,
    RemoveEntryRequest,
    SandboxSession,
    SessionClosed,
    SessionCommandExecutor,
    SessionExecuteRequest,
    SessionFailed,
    SessionOperationCancelled,
    SessionOperationTimeout,
    SessionPolicyDenied,
    SessionWorkspaceMutator,
    WriteBytesRequest,
)
from mem_sandbox.snapshots import (
    JsonSessionSnapshotCodec,
    SandboxSnapshot,
    SandboxSnapshotDraft,
    SnapshotRef,
)
from mem_sandbox.workspace import (
    DirectoryNotEmptyError,
    InvalidPathError,
    MakeDirectoryRequest,
    MemoryWorkspace,
    PathAlreadyExistsError,
    PathNotFoundError,
    RootModificationError,
    SandboxPath,
    WorkspaceMutation,
    WorkspacePatchRequest,
    WorkspacePatchResult,
    WorkspaceStats,
    WorkspaceWriteRequest,
)
from mem_sandbox.workspace import RemovePathRequest as WorkspaceRemovePathRequest


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 9, 4, 12, tzinfo=UTC)


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


class BlockingCompletionEvents(Events):
    def __init__(self) -> None:
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def emit(self, event: SandboxEvent) -> None:
        self.values.append(event)
        if event.event_type == "operation.completed":
            self.entered.set()
            await self.release.wait()


class Store:
    @property
    def process_local(self) -> bool:
        return True

    async def save(self, draft: SandboxSnapshotDraft) -> SnapshotRef:
        _ = draft
        raise AssertionError("snapshot save is not expected")

    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot:
        _ = snapshot_ref
        raise AssertionError("snapshot load is not expected")


class ForbiddenExecutor:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        request: ExecuteRequest,
        context: CommandExecutionContext,
    ) -> ExecuteResult:
        _ = (request, context)
        self.calls += 1
        raise AssertionError("native path operations must not invoke the command executor")


class RecordingPolicy:
    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.requests: list[PolicyRequest] = []

    async def evaluate(self, request: PolicyRequest) -> PolicyDecision:
        self.requests.append(request)
        return PolicyDecision(
            self.allowed,
            "allowed" if self.allowed else "blocked",
            request.requested_limits,
        )


class DelegatingMutator:
    def __init__(self, workspace: MemoryWorkspace) -> None:
        self.workspace = workspace
        self.mkdir_calls: list[MakeDirectoryRequest] = []
        self.remove_calls: list[WorkspaceRemovePathRequest] = []

    async def mkdir(self, request: MakeDirectoryRequest) -> WorkspaceMutation:
        self.mkdir_calls.append(request)
        return await self.workspace.mkdir(request)

    async def write(self, request: WorkspaceWriteRequest) -> WorkspaceMutation:
        return await self.workspace.write(request)

    async def patch(self, request: WorkspacePatchRequest) -> WorkspacePatchResult:
        return await self.workspace.patch(request)

    async def remove(self, request: WorkspaceRemovePathRequest) -> WorkspaceMutation:
        self.remove_calls.append(request)
        return await self.workspace.remove(request)


class BlockingMutator(DelegatingMutator):
    def __init__(self, workspace: MemoryWorkspace, *, operation: OperationKind) -> None:
        super().__init__(workspace)
        self.operation = operation
        self.entered = asyncio.Event()

    async def _block(self) -> None:
        self.entered.set()
        await asyncio.Event().wait()

    async def mkdir(self, request: MakeDirectoryRequest) -> WorkspaceMutation:
        if self.operation is OperationKind.CREATE_DIRECTORY:
            await self._block()
        return await super().mkdir(request)

    async def remove(self, request: WorkspaceRemovePathRequest) -> WorkspaceMutation:
        if self.operation is OperationKind.REMOVE_PATH:
            await self._block()
        return await super().remove(request)


class FailingMutator(DelegatingMutator):
    async def mkdir(self, request: MakeDirectoryRequest) -> WorkspaceMutation:
        _ = request
        raise RuntimeError("directory backend unavailable")

    async def remove(self, request: WorkspaceRemovePathRequest) -> WorkspaceMutation:
        _ = request
        raise RuntimeError("directory backend unavailable")


class CompletingAfterCommitMutator(DelegatingMutator):
    def __init__(
        self,
        workspace: MemoryWorkspace,
        *,
        operation: OperationKind,
        cancellation: asyncio.Event | None = None,
        delay_seconds: float = 0.0,
    ) -> None:
        super().__init__(workspace)
        self.operation = operation
        self.cancellation = cancellation
        self.delay_seconds = delay_seconds

    def _finish_commit(self) -> None:
        if self.cancellation is not None:
            self.cancellation.set()
        if self.delay_seconds:
            time.sleep(self.delay_seconds)

    async def mkdir(self, request: MakeDirectoryRequest) -> WorkspaceMutation:
        result = await super().mkdir(request)
        if self.operation is OperationKind.CREATE_DIRECTORY:
            self._finish_commit()
        return result

    async def remove(self, request: WorkspaceRemovePathRequest) -> WorkspaceMutation:
        result = await super().remove(request)
        if self.operation is OperationKind.REMOVE_PATH:
            self._finish_commit()
        return result


class CancellingFailingMutator(DelegatingMutator):
    def __init__(self, workspace: MemoryWorkspace, *, operation: OperationKind) -> None:
        super().__init__(workspace)
        self.operation = operation
        self.owner_task: asyncio.Task[PathMutationResult] | None = None

    def _cancel_owner_and_fail(self) -> None:
        assert self.owner_task is not None
        self.owner_task.cancel()
        raise RuntimeError("directory backend failed during cancellation")

    async def mkdir(self, request: MakeDirectoryRequest) -> WorkspaceMutation:
        if self.operation is OperationKind.CREATE_DIRECTORY:
            self._cancel_owner_and_fail()
        return await super().mkdir(request)

    async def remove(self, request: WorkspaceRemovePathRequest) -> WorkspaceMutation:
        if self.operation is OperationKind.REMOVE_PATH:
            self._cancel_owner_and_fail()
        return await super().remove(request)


def make_session(
    *,
    workspace: MemoryWorkspace | None = None,
    mutator: SessionWorkspaceMutator | None = None,
    policy: RecordingPolicy | None = None,
    executor: SessionCommandExecutor | None = None,
    events: Events | None = None,
    initial_cwd: SandboxPath | None = None,
) -> tuple[SandboxSession, MemoryWorkspace, SessionCommandExecutor, Events, RecordingPolicy]:
    actual_workspace = workspace or MemoryWorkspace()
    actual_executor = executor or ForbiddenExecutor()
    actual_events = events or Events()
    actual_policy = policy or RecordingPolicy()
    return (
        SandboxSession(
            session_id=SessionId(UUID(int=100)),
            workspace_reader=actual_workspace,
            workspace_mutator=mutator or actual_workspace,
            workspace_snapshots=actual_workspace,
            command_executor=actual_executor,
            policy_engine=actual_policy,
            secret_broker=NoSecretBroker(),
            event_sink=actual_events,
            snapshot_store=Store(),
            snapshot_codec=JsonSessionSnapshotCodec(),
            resource_scope=NoOpSessionResourceScope(),
            clock=FixedClock(),
            uuid_generator=Uuids(),
            initial_cwd=initial_cwd,
        ),
        actual_workspace,
        actual_executor,
        actual_events,
        actual_policy,
    )


def test_native_path_requests_validate_types_at_construction() -> None:
    with pytest.raises(TypeError, match="path must be a string"):
        CreateDirectoryRequest(path=cast(str, object()))
    with pytest.raises(TypeError, match="path must be a string"):
        RemoveEntryRequest(path=cast(str, object()))
    with pytest.raises(TypeError, match="create_parents must be a boolean"):
        CreateDirectoryRequest(path="/workspace/path", create_parents=cast(bool, 1))
    with pytest.raises(TypeError, match="exist_ok must be a boolean"):
        CreateDirectoryRequest(path="/workspace/path", exist_ok=cast(bool, 1))
    with pytest.raises(TypeError, match="recursive must be a boolean"):
        RemoveEntryRequest(path="/workspace/path", recursive=cast(bool, 1))
    with pytest.raises(TypeError, match="missing_ok must be a boolean"):
        RemoveEntryRequest(path="/workspace/path", missing_ok=cast(bool, 1))


def test_native_path_request_defaults_are_strict() -> None:
    create = CreateDirectoryRequest(path="/workspace/path")
    remove = RemoveEntryRequest(path="/workspace/path")

    assert not create.create_parents
    assert not create.exist_ok
    assert not remove.recursive
    assert not remove.missing_ok


def test_path_mutation_result_preserves_file_result_compatibility() -> None:
    assert FileMutationResult is PathMutationResult


@pytest.mark.asyncio
async def test_file_mutations_use_the_canonical_result_without_path_policy_facts() -> None:
    session, _, _, _, policy = make_session()
    await session.start()

    result = await session.write_bytes(
        WriteBytesRequest(path="/workspace/file.txt", content=b"content")
    )

    assert isinstance(result, PathMutationResult)
    assert policy.requests[-1].path_mutation is None


@pytest.mark.asyncio
async def test_create_directory_and_remove_path_return_stable_session_metadata() -> None:
    workspace = MemoryWorkspace()
    mutator = DelegatingMutator(workspace)
    session, _, executor, events, policy = make_session(
        workspace=workspace,
        mutator=mutator,
    )
    await session.start()

    created = await session.create_directory(
        CreateDirectoryRequest(
            path="/workspace/cache/artifacts",
            create_parents=True,
            exist_ok=False,
        )
    )
    created_entry = await workspace.stat(SandboxPath.resolve("/workspace/cache/artifacts"))
    removed = await session.remove_path(RemoveEntryRequest(path="/workspace/cache/artifacts"))

    assert isinstance(created, PathMutationResult)
    assert created.path == SandboxPath.resolve("/workspace/cache/artifacts")
    assert created.created
    assert created.changed
    assert created.previous_hash is None
    assert created.current_hash == created_entry.content_hash
    assert created.metadata.workspace_revision == Revision(1)
    assert removed.path == created.path
    assert not removed.created
    assert removed.changed
    assert removed.previous_hash == created.current_hash
    assert removed.current_hash is None
    assert removed.metadata.workspace_revision == Revision(2)
    assert mutator.mkdir_calls == [
        MakeDirectoryRequest(
            SandboxPath.resolve("/workspace/cache/artifacts"),
            create_parents=True,
            exist_ok=False,
        )
    ]
    assert mutator.remove_calls == [
        WorkspaceRemovePathRequest(
            SandboxPath.resolve("/workspace/cache/artifacts"),
            recursive=False,
            missing_ok=False,
        )
    ]
    assert [request.operation_kind for request in policy.requests] == [
        OperationKind.CREATE_DIRECTORY,
        OperationKind.REMOVE_PATH,
    ]
    assert [request.path_mutation for request in policy.requests] == [
        PathMutationPolicyContext(create_parents=True, exist_ok=False),
        PathMutationPolicyContext(recursive=False, missing_ok=False),
    ]
    assert [
        event.operation_kind for event in events.values if event.operation_kind is not None
    ] == [
        OperationKind.CREATE_DIRECTORY,
        OperationKind.CREATE_DIRECTORY,
        OperationKind.REMOVE_PATH,
        OperationKind.REMOVE_PATH,
    ]
    assert isinstance(executor, ForbiddenExecutor)
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_remove_path_preserves_state_for_boundaries_and_missing_noop() -> None:
    session, workspace, executor, _, _ = make_session()
    await session.start()
    await session.create_directory(CreateDirectoryRequest(path="/workspace/tree"))
    await session.write_bytes(
        WriteBytesRequest(path="/workspace/tree/file.txt", content=b"content")
    )
    before = await workspace.stats()

    with pytest.raises(DirectoryNotEmptyError):
        await session.remove_path(RemoveEntryRequest(path="/workspace/tree"))
    assert await workspace.stats() == before

    with pytest.raises(PathNotFoundError):
        await session.remove_path(RemoveEntryRequest(path="/workspace/missing"))
    assert await workspace.stats() == before

    missing = await session.remove_path(
        RemoveEntryRequest(path="/workspace/missing", missing_ok=True)
    )
    assert not missing.created
    assert not missing.changed
    assert missing.previous_hash is None
    assert missing.current_hash is None
    assert missing.metadata.workspace_revision == before.revision
    assert await workspace.stats() == before

    with pytest.raises(RootModificationError):
        await session.remove_path(RemoveEntryRequest(path="/workspace", recursive=True))
    assert await workspace.stats() == before

    removed = await session.remove_path(RemoveEntryRequest(path="/workspace/tree", recursive=True))
    assert removed.metadata.workspace_revision == before.revision.next()
    assert (await workspace.stats()).node_count == 1
    assert isinstance(executor, ForbiddenExecutor)
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_create_directory_honors_parent_and_existing_path_options() -> None:
    session, workspace, executor, _, _ = make_session()
    await session.start()
    await session.create_directory(CreateDirectoryRequest(path="/workspace/existing"))
    existing_entry = await workspace.stat(SandboxPath.resolve("/workspace/existing"))
    before = await workspace.stats()

    with pytest.raises(PathAlreadyExistsError):
        await session.create_directory(CreateDirectoryRequest(path="/workspace/existing"))
    assert await workspace.stats() == before

    existing = await session.create_directory(
        CreateDirectoryRequest(
            path="/workspace/existing",
            create_parents=True,
            exist_ok=True,
        )
    )
    assert not existing.created
    assert not existing.changed
    assert existing.previous_hash == existing.current_hash == existing_entry.content_hash
    assert existing.metadata.workspace_revision == before.revision
    assert await workspace.stats() == before

    with pytest.raises(PathNotFoundError):
        await session.create_directory(CreateDirectoryRequest(path="/workspace/missing/child"))
    assert await workspace.stats() == before

    await session.write_bytes(
        WriteBytesRequest(path="/workspace/existing-file", content=b"content")
    )
    before_file_conflict = await workspace.stats()
    with pytest.raises(PathAlreadyExistsError):
        await session.create_directory(
            CreateDirectoryRequest(path="/workspace/existing-file", exist_ok=True)
        )
    assert await workspace.stats() == before_file_conflict
    assert isinstance(executor, ForbiddenExecutor)
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_create_directory_treats_existing_root_as_a_directory() -> None:
    session, workspace, executor, _, _ = make_session()
    await session.start()
    root = SandboxPath.root()
    root_entry = await workspace.stat(root)
    before = await workspace.stats()

    with pytest.raises(PathAlreadyExistsError):
        await session.create_directory(CreateDirectoryRequest(path="/workspace"))

    existing = await session.create_directory(
        CreateDirectoryRequest(path="/workspace", exist_ok=True)
    )

    assert not existing.created
    assert not existing.changed
    assert existing.previous_hash == existing.current_hash == root_entry.content_hash
    assert existing.metadata.workspace_revision == before.revision
    assert await workspace.stats() == before
    assert isinstance(executor, ForbiddenExecutor)
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_native_and_command_operations_share_workspace_directory_semantics() -> None:
    workspace = MemoryWorkspace()
    command_executor = create_default_executor(workspace, workspace)
    session, _, _, _, _ = make_session(
        workspace=workspace,
        executor=command_executor,
    )
    await session.start()

    before_noops = await workspace.stats()
    native_missing = await session.remove_path(
        RemoveEntryRequest(path="/workspace/missing", missing_ok=True)
    )
    command_missing = await session.execute(
        SessionExecuteRequest(command="rm -f /workspace/missing")
    )
    command_root = await session.execute(SessionExecuteRequest(command="mkdir -p /workspace"))
    assert not native_missing.changed
    assert command_missing.exit_code == 0
    assert command_root.exit_code == 0
    assert await workspace.stats() == before_noops

    await session.create_directory(CreateDirectoryRequest(path="/workspace/shared"))
    before_existing = await workspace.stats()
    repeated = await session.execute(SessionExecuteRequest(command="mkdir -p /workspace/shared"))
    assert repeated.exit_code == 0
    assert await workspace.stats() == before_existing

    command = await session.execute(
        SessionExecuteRequest(command="touch /workspace/shared/file.txt")
    )

    assert command.exit_code == 0
    with pytest.raises(DirectoryNotEmptyError):
        await session.remove_path(RemoveEntryRequest(path="/workspace/shared"))

    removed = await session.execute(SessionExecuteRequest(command="rm -r /workspace/shared"))
    assert removed.exit_code == 0
    stats = await workspace.stats()
    assert stats.node_count == 1
    assert stats.revision == Revision(3)

    created = await session.execute(
        SessionExecuteRequest(command="mkdir -p /workspace/command/child")
    )
    assert created.exit_code == 0
    await session.remove_path(RemoveEntryRequest(path="/workspace/command", recursive=True))
    assert (await workspace.stats()).revision == Revision(5)

    await session.write_bytes(
        WriteBytesRequest(path="/workspace/existing-file", content=b"content")
    )
    before_file_conflict = await workspace.stats()
    file_conflict = await session.execute(
        SessionExecuteRequest(command="mkdir -p /workspace/existing-file")
    )
    assert file_conflict.exit_code == 1
    assert "already exists" in file_conflict.stderr
    assert await workspace.stats() == before_file_conflict


@pytest.mark.asyncio
async def test_invalid_paths_and_closed_sessions_fail_before_dependencies() -> None:
    session, workspace, executor, events, policy = make_session()
    await session.start()

    with pytest.raises(InvalidPathError):
        await session.create_directory(CreateDirectoryRequest(path="../escape"))
    with pytest.raises(InvalidPathError):
        await session.remove_path(RemoveEntryRequest(path="../escape"))

    assert policy.requests == []
    assert len(events.values) == 1
    assert (await workspace.stats()).revision == Revision(0)

    await session.close()
    with pytest.raises(SessionClosed):
        await session.create_directory(CreateDirectoryRequest(path="/workspace/after-close"))
    with pytest.raises(SessionClosed):
        await session.remove_path(RemoveEntryRequest(path="/workspace/after-close"))

    assert policy.requests == []
    assert isinstance(executor, ForbiddenExecutor)
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_native_paths_resolve_against_the_session_working_directory() -> None:
    workspace = MemoryWorkspace()
    await workspace.mkdir(MakeDirectoryRequest(SandboxPath.resolve("/workspace/nested")))
    mutator = DelegatingMutator(workspace)
    session, _, executor, _, _ = make_session(
        workspace=workspace,
        mutator=mutator,
        initial_cwd=SandboxPath.resolve("/workspace/nested"),
    )
    await session.start()

    created = await session.create_directory(CreateDirectoryRequest(path="child"))
    removed = await session.remove_path(RemoveEntryRequest(path="child"))

    expected_path = SandboxPath.resolve("/workspace/nested/child")
    assert created.path == expected_path
    assert removed.path == expected_path
    assert mutator.mkdir_calls == [
        MakeDirectoryRequest(expected_path, create_parents=False, exist_ok=False)
    ]
    assert mutator.remove_calls == [
        WorkspaceRemovePathRequest(expected_path, recursive=False, missing_ok=False)
    ]
    assert isinstance(executor, ForbiddenExecutor)
    assert executor.calls == 0


@pytest.mark.asyncio
async def test_policy_denial_prevents_native_path_mutations() -> None:
    policy = RecordingPolicy(allowed=False)
    session, workspace, executor, events, _ = make_session(policy=policy)
    await session.start()

    with pytest.raises(SessionPolicyDenied):
        await session.create_directory(
            CreateDirectoryRequest(
                path="/workspace/denied",
                create_parents=True,
                exist_ok=True,
            )
        )
    with pytest.raises(SessionPolicyDenied):
        await session.remove_path(
            RemoveEntryRequest(
                path="/workspace/denied",
                recursive=True,
                missing_ok=True,
            )
        )

    stats = await workspace.stats()
    assert stats.revision == Revision(0)
    assert stats.node_count == 1
    assert [request.operation_kind for request in policy.requests] == [
        OperationKind.CREATE_DIRECTORY,
        OperationKind.REMOVE_PATH,
    ]
    assert [request.path_mutation for request in policy.requests] == [
        PathMutationPolicyContext(create_parents=True, exist_ok=True),
        PathMutationPolicyContext(recursive=True, missing_ok=True),
    ]
    assert [event.event_type for event in events.values[1:]] == [
        "operation.started",
        "operation.failed",
        "operation.started",
        "operation.failed",
    ]
    assert isinstance(executor, ForbiddenExecutor)
    assert executor.calls == 0


async def _run_native_path_operation(
    session: SandboxSession,
    operation: OperationKind,
    *,
    limits: OperationLimits | None = None,
    cancellation: asyncio.Event | None = None,
) -> PathMutationResult:
    if operation is OperationKind.CREATE_DIRECTORY:
        return await session.create_directory(
            CreateDirectoryRequest(
                path="/workspace/target",
                limits=limits or OperationLimits(),
                cancellation=cancellation,
            )
        )
    return await session.remove_path(
        RemoveEntryRequest(
            path="/workspace/target",
            limits=limits or OperationLimits(),
            cancellation=cancellation,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [OperationKind.CREATE_DIRECTORY, OperationKind.REMOVE_PATH],
)
async def test_native_path_timeout_leaves_state_unchanged(
    operation: OperationKind,
) -> None:
    workspace = MemoryWorkspace()
    mutator = BlockingMutator(workspace, operation=operation)
    session, _, executor, events, _ = make_session(
        workspace=workspace,
        mutator=mutator,
    )
    await session.start()

    with pytest.raises(SessionOperationTimeout):
        await _run_native_path_operation(
            session,
            operation,
            limits=OperationLimits(
                timeout_seconds=1.0,
                terminal_event_reserve_seconds=0.5,
            ),
        )

    assert mutator.entered.is_set()
    assert (await workspace.stats()).revision == Revision(0)
    assert events.values[-1].event_type == "operation.timed_out"
    assert isinstance(executor, ForbiddenExecutor)
    assert executor.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [OperationKind.CREATE_DIRECTORY, OperationKind.REMOVE_PATH],
)
async def test_native_path_cancellation_leaves_state_unchanged(
    operation: OperationKind,
) -> None:
    workspace = MemoryWorkspace()
    before = await workspace.stats()
    mutator = BlockingMutator(workspace, operation=operation)
    session, _, executor, events, _ = make_session(
        workspace=workspace,
        mutator=mutator,
    )
    await session.start()
    cancellation = asyncio.Event()
    task = asyncio.create_task(
        _run_native_path_operation(
            session,
            operation,
            cancellation=cancellation,
        )
    )
    await mutator.entered.wait()
    cancellation.set()

    with pytest.raises(SessionOperationCancelled):
        await task

    assert await workspace.stats() == before
    assert events.values[-1].event_type == "operation.cancelled"
    assert isinstance(executor, ForbiddenExecutor)
    assert executor.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [OperationKind.CREATE_DIRECTORY, OperationKind.REMOVE_PATH],
)
async def test_cancellation_requested_after_commit_reports_success(
    operation: OperationKind,
) -> None:
    workspace = MemoryWorkspace()
    if operation is OperationKind.REMOVE_PATH:
        await workspace.mkdir(MakeDirectoryRequest(SandboxPath.resolve("/workspace/target")))
    cancellation = asyncio.Event()
    mutator = CompletingAfterCommitMutator(
        workspace,
        operation=operation,
        cancellation=cancellation,
    )
    session, _, executor, events, _ = make_session(
        workspace=workspace,
        mutator=mutator,
    )
    await session.start()

    result = await _run_native_path_operation(
        session,
        operation,
        cancellation=cancellation,
    )

    assert cancellation.is_set()
    assert result.changed
    assert events.values[-1].event_type == "operation.completed"
    assert isinstance(executor, ForbiddenExecutor)
    assert executor.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [OperationKind.CREATE_DIRECTORY, OperationKind.REMOVE_PATH],
)
async def test_completed_mutation_past_collaborator_deadline_reports_success(
    operation: OperationKind,
) -> None:
    workspace = MemoryWorkspace()
    if operation is OperationKind.REMOVE_PATH:
        await workspace.mkdir(MakeDirectoryRequest(SandboxPath.resolve("/workspace/target")))
    mutator = CompletingAfterCommitMutator(
        workspace,
        operation=operation,
        delay_seconds=0.5,
    )
    session, _, executor, events, _ = make_session(
        workspace=workspace,
        mutator=mutator,
    )
    await session.start()

    result = await _run_native_path_operation(
        session,
        operation,
        limits=OperationLimits(
            timeout_seconds=1.0,
            terminal_event_reserve_seconds=0.6,
        ),
    )

    assert result.changed
    assert events.values[-1].event_type == "operation.completed"
    assert isinstance(executor, ForbiddenExecutor)
    assert executor.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [OperationKind.CREATE_DIRECTORY, OperationKind.REMOVE_PATH],
)
async def test_failed_collaborator_does_not_replace_native_cancellation(
    operation: OperationKind,
) -> None:
    workspace = MemoryWorkspace()
    mutator = CancellingFailingMutator(workspace, operation=operation)
    session, _, executor, events, _ = make_session(
        workspace=workspace,
        mutator=mutator,
    )
    await session.start()
    task = asyncio.create_task(_run_native_path_operation(session, operation))
    mutator.owner_task = task

    with pytest.raises(asyncio.CancelledError):
        await task

    assert (await workspace.stats()).revision == Revision(0)
    assert events.values[-1].event_type == "operation.cancelled"
    assert isinstance(executor, ForbiddenExecutor)
    assert executor.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "operation",
    [OperationKind.CREATE_DIRECTORY, OperationKind.REMOVE_PATH],
)
async def test_native_cancellation_during_completion_delivery_preserves_result(
    operation: OperationKind,
) -> None:
    workspace = MemoryWorkspace()
    if operation is OperationKind.REMOVE_PATH:
        await workspace.mkdir(MakeDirectoryRequest(SandboxPath.resolve("/workspace/target")))
    events = BlockingCompletionEvents()
    session, _, executor, _, _ = make_session(
        workspace=workspace,
        events=events,
    )
    await session.start()
    task = asyncio.create_task(_run_native_path_operation(session, operation))
    await events.entered.wait()

    task.cancel()
    events.release.set()
    result = await task

    assert result.changed
    assert [event.event_type for event in events.values].count(
        SandboxEventType.OPERATION_COMPLETED
    ) == 1
    assert isinstance(executor, ForbiddenExecutor)
    assert executor.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", [OperationKind.CREATE_DIRECTORY, OperationKind.REMOVE_PATH])
async def test_dependency_failure_is_wrapped_without_native_path_mutation(
    operation: OperationKind,
) -> None:
    workspace = MemoryWorkspace()
    mutator = FailingMutator(workspace)
    session, _, executor, events, _ = make_session(
        workspace=workspace,
        mutator=mutator,
    )
    await session.start()

    with pytest.raises(SessionFailed):
        if operation is OperationKind.CREATE_DIRECTORY:
            await session.create_directory(CreateDirectoryRequest(path="/workspace/failure"))
        else:
            await session.remove_path(RemoveEntryRequest(path="/workspace/failure"))

    stats: WorkspaceStats = await workspace.stats()
    assert stats.revision == Revision(0)
    assert stats.node_count == 1
    assert events.values[-1].event_type == "operation.failed"
    assert isinstance(executor, ForbiddenExecutor)
    assert executor.calls == 0
