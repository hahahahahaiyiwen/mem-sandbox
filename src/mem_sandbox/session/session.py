"""SandboxSession lifecycle and operation orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import TypeVar, cast

from mem_sandbox.command_executor import (
    CancellationSignal,
    CommandEnvironment,
    CommandExecutionContext,
    EnvironmentChange,
    EnvironmentValue,
    ExecuteRequest,
    ExecuteResult,
)
from mem_sandbox.core import (
    Clock,
    ErrorCategory,
    OperationId,
    OperationKind,
    OperationLimits,
    OperationResultMetadata,
    Revision,
    SandboxError,
    SessionId,
    SnapshotId,
    UuidGenerator,
)
from mem_sandbox.events import (
    EventAttribute,
    EventSensitivity,
    SandboxEvent,
    SandboxEventType,
)
from mem_sandbox.policy import PolicyDecision, PolicyRequest
from mem_sandbox.session.errors import (
    SessionCleanupFailed,
    SessionClosed,
    SessionCloseTimeout,
    SessionClosing,
    SessionEventDeliveryFailed,
    SessionFailed,
    SessionNotRunning,
    SessionOperationCancelled,
    SessionOperationTimeout,
    SessionPolicyDenied,
    SessionRequestInvalid,
    SessionSnapshotRestoreFailed,
    SessionStartInvalid,
)
from mem_sandbox.session.models import (
    ApplyPatchRequest,
    CreateSnapshotRequest,
    CreateSnapshotResult,
    FileMutationResult,
    ListEntriesRequest,
    ListEntriesResult,
    PatchMutationResult,
    ReadBytesRequest,
    ReadBytesResult,
    ReadFileRequest,
    ReadFileResult,
    RestoreSnapshotRequest,
    RestoreSnapshotResult,
    SandboxSessionState,
    SessionExecuteRequest,
    SessionExecuteResult,
    SessionExpectedFileHash,
    StatRequest,
    StatResult,
    WriteBytesRequest,
    WriteFileRequest,
)
from mem_sandbox.session.ports import (
    SessionCommandExecutor,
    SessionEventSink,
    SessionPolicyEngine,
    SessionResourceScope,
    SessionSecretBroker,
    SessionSnapshotCodec,
    SessionSnapshotStore,
    SessionWorkspaceMutator,
    SessionWorkspaceReader,
    SessionWorkspaceSnapshotPort,
)
from mem_sandbox.snapshots import (
    SandboxSnapshotDraft,
    SessionSnapshotState,
    SnapshotCorrupt,
    SnapshotIncompatible,
    SnapshotMetadata,
    SnapshotRef,
    SnapshotSaveFailed,
)
from mem_sandbox.workspace import (
    ContentHash,
    ExpectedFileHash,
    PreparedWorkspaceRestore,
    SandboxPath,
    WorkspaceBinaryResult,
    WorkspaceEntry,
    WorkspaceMutation,
    WorkspacePatchRequest,
    WorkspacePatchResult,
    WorkspaceRangeRequest,
    WorkspaceRangeResult,
    WorkspaceWriteRequest,
    WritePrecondition,
)

_T = TypeVar("_T")
_POLL_SECONDS = 0.01


@dataclass(frozen=True, slots=True)
class _OperationContext:
    operation_id: OperationId
    started_at: datetime
    started_monotonic: float
    original_deadline: float
    collaborator_deadline: float


@dataclass(frozen=True, slots=True)
class _OperationOutcome[T]:
    value: T
    metadata: OperationResultMetadata


class SandboxSession:
    """Serialize and coordinate the approved Milestone 3 sandbox operations."""

    def __init__(
        self,
        *,
        session_id: SessionId,
        workspace_reader: SessionWorkspaceReader,
        workspace_mutator: SessionWorkspaceMutator,
        workspace_snapshots: SessionWorkspaceSnapshotPort,
        command_executor: SessionCommandExecutor,
        policy_engine: SessionPolicyEngine,
        secret_broker: SessionSecretBroker,
        event_sink: SessionEventSink,
        snapshot_store: SessionSnapshotStore,
        snapshot_codec: SessionSnapshotCodec,
        resource_scope: SessionResourceScope,
        clock: Clock,
        uuid_generator: UuidGenerator,
        initial_cwd: SandboxPath | None = None,
        initial_environment: CommandEnvironment | None = None,
        lifecycle_limits: OperationLimits | None = None,
    ) -> None:
        self._session_id = session_id
        self._workspace_reader = workspace_reader
        self._workspace_mutator = workspace_mutator
        self._workspace_snapshots = workspace_snapshots
        self._command_executor = command_executor
        self._policy_engine = policy_engine
        self._secret_broker = secret_broker
        self._event_sink = event_sink
        self._snapshot_store = snapshot_store
        self._snapshot_codec = snapshot_codec
        self._resource_scope = resource_scope
        self._clock = clock
        self._uuid_generator = uuid_generator
        self._cwd = initial_cwd or SandboxPath.root()
        self._environment = initial_environment or CommandEnvironment()
        self._lifecycle_limits = lifecycle_limits or OperationLimits()
        self._state = SandboxSessionState.CREATED
        self._operation_gate = asyncio.Lock()
        self._event_sequence = 0
        self._close_task: asyncio.Task[None] | None = None

    @property
    def session_id(self) -> SessionId:
        return self._session_id

    @property
    def state(self) -> SandboxSessionState:
        return self._state

    @property
    def cwd(self) -> SandboxPath:
        return self._cwd

    @property
    def environment(self) -> CommandEnvironment:
        return self._environment

    async def start(self) -> None:
        if self._state is not SandboxSessionState.CREATED:
            raise SessionStartInvalid(f"session cannot start from state {self._state.value}")
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._lifecycle_limits.timeout_seconds
        try:
            async with asyncio.timeout_at(deadline):
                await self._operation_gate.acquire()
        except TimeoutError as error:
            raise SessionOperationTimeout("session startup timed out") from error
        try:
            if self._state is not SandboxSessionState.CREATED:
                raise SessionStartInvalid(f"session cannot start from state {self._state.value}")
            event = self._new_event(SandboxEventType.SANDBOX_STARTED)
            try:
                await self._emit_event(event, deadline, operation_id=None)
            except (SessionEventDeliveryFailed, SessionOperationTimeout):
                self._state = SandboxSessionState.FAILED
                raise
            except asyncio.CancelledError:
                self._state = SandboxSessionState.FAILED
                raise
            if self._state is SandboxSessionState.CREATED:
                self._state = SandboxSessionState.RUNNING
        finally:
            self._operation_gate.release()

    async def execute(self, request: SessionExecuteRequest) -> SessionExecuteResult:
        async def action(context: _OperationContext) -> tuple[ExecuteResult, Revision]:
            remaining = self._remaining(context.collaborator_deadline, context.operation_id)
            effective_command_limits = replace(
                request.command_limits,
                timeout_seconds=min(request.command_limits.timeout_seconds, remaining),
            )
            result = await self._await_collaborator(
                lambda: self._command_executor.execute(
                    ExecuteRequest(
                        request.command,
                        self._cwd,
                        self._environment,
                        effective_command_limits,
                    ),
                    CommandExecutionContext(
                        session_id=self._session_id,
                        operation_id=context.operation_id,
                        cancellation=request.cancellation,
                    ),
                ),
                context.collaborator_deadline,
                request.cancellation,
                context.operation_id,
            )
            resulting_environment = _apply_environment_changes(
                self._environment,
                result.environment_changes,
            )
            stats = await self._await_collaborator(
                self._workspace_reader.stats,
                context.collaborator_deadline,
                request.cancellation,
                context.operation_id,
            )
            self._cwd = result.resulting_cwd
            self._environment = resulting_environment
            return result, stats.revision

        outcome = await self._run_operation(
            OperationKind.EXECUTE,
            request.limits,
            request.cancellation,
            lambda: None,
            action,
        )
        result = outcome.value
        return SessionExecuteResult(
            outcome.metadata,
            result.exit_code,
            result.failure_code,
            result.stdout,
            result.stderr,
            result.stdout_original_bytes,
            result.stderr_original_bytes,
            result.stdout_truncated,
            result.stderr_truncated,
            result.duration_ms,
            result.resulting_cwd,
            result.environment_changes,
        )

    async def read_file(self, request: ReadFileRequest) -> ReadFileResult:
        path_box: list[SandboxPath] = []

        def normalize() -> SandboxPath:
            path = self._workspace_reader.resolve_path(request.path, cwd=self._cwd)
            path_box.append(path)
            return path

        async def action(context: _OperationContext) -> tuple[WorkspaceRangeResult, Revision]:
            result = await self._await_collaborator(
                lambda: self._workspace_reader.read_range(
                    WorkspaceRangeRequest(path_box[0], request.start_line, request.end_line)
                ),
                context.collaborator_deadline,
                request.cancellation,
                context.operation_id,
            )
            return result, result.revision

        outcome = await self._run_operation(
            OperationKind.READ_FILE,
            request.limits,
            request.cancellation,
            normalize,
            action,
        )
        result = outcome.value
        return ReadFileResult(
            outcome.metadata,
            result.path,
            result.content,
            result.start_line,
            result.end_line,
            result.total_lines,
            result.content_hash,
        )

    async def write_file(self, request: WriteFileRequest) -> FileMutationResult:
        return await self._write(
            OperationKind.WRITE_FILE,
            request.path,
            request.content.encode("utf-8"),
            request.precondition,
            request.create_parents,
            request.limits,
            request.cancellation,
        )

    async def write_bytes(self, request: WriteBytesRequest) -> FileMutationResult:
        return await self._write(
            OperationKind.WRITE_BYTES,
            request.path,
            request.content,
            request.precondition,
            request.create_parents,
            request.limits,
            request.cancellation,
        )

    async def _write(
        self,
        kind: OperationKind,
        path_value: str,
        content: bytes,
        precondition: WritePrecondition,
        create_parents: bool,
        limits: OperationLimits,
        cancellation: CancellationSignal | None,
    ) -> FileMutationResult:
        path_box: list[SandboxPath] = []

        def normalize() -> SandboxPath:
            path = self._workspace_reader.resolve_path(path_value, cwd=self._cwd)
            path_box.append(path)
            return path

        async def action(context: _OperationContext) -> tuple[WorkspaceMutation, Revision]:
            result = await self._await_collaborator(
                lambda: self._workspace_mutator.write(
                    WorkspaceWriteRequest(
                        path_box[0],
                        content,
                        precondition,
                        create_parents,
                    )
                ),
                context.collaborator_deadline,
                cancellation,
                context.operation_id,
            )
            return result, result.stats.revision

        outcome = await self._run_operation(
            kind,
            limits,
            cancellation,
            normalize,
            action,
        )
        result = outcome.value
        return FileMutationResult(
            outcome.metadata,
            result.path,
            result.created,
            result.changed,
            result.previous_hash,
            result.current_hash,
        )

    async def apply_patch(self, request: ApplyPatchRequest) -> PatchMutationResult:
        expected: list[ExpectedFileHash] = []

        def normalize() -> None:
            for item in request.expected_hashes:
                if isinstance(item, SessionExpectedFileHash):
                    path = self._workspace_reader.resolve_path(item.path, cwd=self._cwd)
                    expected.append(ExpectedFileHash(path, item.content_hash))
                else:
                    path = self._workspace_reader.resolve_path(item.path.value, cwd=self._cwd)
                    expected.append(ExpectedFileHash(path, item.content_hash))
            return None

        async def action(context: _OperationContext) -> tuple[WorkspacePatchResult, Revision]:
            result = await self._await_collaborator(
                lambda: self._workspace_mutator.patch(
                    WorkspacePatchRequest(request.patch, tuple(expected))
                ),
                context.collaborator_deadline,
                request.cancellation,
                context.operation_id,
            )
            return result, result.stats.revision

        outcome = await self._run_operation(
            OperationKind.APPLY_PATCH,
            request.limits,
            request.cancellation,
            normalize,
            action,
        )
        result = outcome.value
        return PatchMutationResult(outcome.metadata, result.files)

    async def read_bytes(self, request: ReadBytesRequest) -> ReadBytesResult:
        path_box: list[SandboxPath] = []

        def normalize() -> SandboxPath:
            path = self._workspace_reader.resolve_path(request.path, cwd=self._cwd)
            path_box.append(path)
            return path

        async def action(context: _OperationContext) -> tuple[WorkspaceBinaryResult, Revision]:
            result = await self._await_collaborator(
                lambda: self._workspace_reader.read_bytes(path_box[0]),
                context.collaborator_deadline,
                request.cancellation,
                context.operation_id,
            )
            return result, result.revision

        outcome = await self._run_operation(
            OperationKind.READ_BYTES,
            request.limits,
            request.cancellation,
            normalize,
            action,
        )
        result = outcome.value
        return ReadBytesResult(
            outcome.metadata,
            result.path,
            result.content,
            result.content_hash,
        )

    async def stat(self, request: StatRequest) -> StatResult:
        result = await self._entry_operation(
            OperationKind.STAT,
            request.path,
            request.limits,
            request.cancellation,
            list_entries=False,
        )
        return cast(StatResult, result)

    async def list_entries(self, request: ListEntriesRequest) -> ListEntriesResult:
        result = await self._entry_operation(
            OperationKind.LIST_ENTRIES,
            request.path,
            request.limits,
            request.cancellation,
            list_entries=True,
        )
        assert isinstance(result, ListEntriesResult)
        return result

    async def _entry_operation(
        self,
        kind: OperationKind,
        path_value: str,
        limits: OperationLimits,
        cancellation: CancellationSignal | None,
        *,
        list_entries: bool,
    ) -> StatResult | ListEntriesResult:
        path_box: list[SandboxPath] = []

        def normalize() -> SandboxPath:
            path = self._workspace_reader.resolve_path(path_value, cwd=self._cwd)
            path_box.append(path)
            return path

        async def action(
            context: _OperationContext,
        ) -> tuple[WorkspaceEntry | tuple[WorkspaceEntry, ...], Revision]:
            if list_entries:
                entries = await self._await_collaborator(
                    lambda: self._workspace_reader.list(path_box[0]),
                    context.collaborator_deadline,
                    cancellation,
                    context.operation_id,
                )
                revision = (
                    entries[0].revision
                    if entries
                    else (
                        await self._await_collaborator(
                            self._workspace_reader.stats,
                            context.collaborator_deadline,
                            cancellation,
                            context.operation_id,
                        )
                    ).revision
                )
                return entries, revision
            entry = await self._await_collaborator(
                lambda: self._workspace_reader.stat(path_box[0]),
                context.collaborator_deadline,
                cancellation,
                context.operation_id,
            )
            return entry, entry.revision

        outcome = await self._run_operation(
            kind,
            limits,
            cancellation,
            normalize,
            action,
        )
        if list_entries:
            return ListEntriesResult(
                outcome.metadata,
                cast(tuple[WorkspaceEntry, ...], outcome.value),
            )
        return StatResult(outcome.metadata, cast(WorkspaceEntry, outcome.value))

    async def create_snapshot(self, request: CreateSnapshotRequest) -> CreateSnapshotResult:
        async def action(
            context: _OperationContext,
        ) -> tuple[tuple[SnapshotRef, ContentHash], Revision]:
            workspace = await self._await_collaborator(
                self._workspace_snapshots.export,
                context.collaborator_deadline,
                request.cancellation,
                context.operation_id,
            )
            state = SessionSnapshotState(workspace, self._cwd, self._environment)
            self._remaining(context.collaborator_deadline, context.operation_id)
            payload = self._snapshot_codec.encode(state)
            snapshot = SandboxSnapshotDraft(
                snapshot_id=SnapshotId(self._uuid_generator.new_uuid()),
                schema_version=payload.schema_version,
                created_at=self._clock.now(),
                source_session_id=self._session_id,
                workspace_revision=payload.workspace_revision,
                content_hash=payload.content_hash,
                payload=payload.payload,
                metadata=SnapshotMetadata(
                    payload.format_name,
                    len(payload.payload),
                    self._snapshot_store.process_local,
                ),
            )
            snapshot_ref = await self._await_collaborator(
                lambda: self._snapshot_store.save(snapshot),
                context.collaborator_deadline,
                request.cancellation,
                context.operation_id,
            )
            if snapshot_ref.snapshot_id != snapshot.snapshot_id:
                raise SnapshotSaveFailed("snapshot store changed the allocated snapshot identity")
            return (snapshot_ref, payload.content_hash), workspace.workspace_revision

        outcome = await self._run_operation(
            OperationKind.CREATE_SNAPSHOT,
            request.limits,
            request.cancellation,
            lambda: None,
            action,
            post_commit_event=lambda value, revision: (
                SandboxEventType.SNAPSHOT_CREATED,
                (
                    EventAttribute(
                        "content_hash",
                        str(value[1]),
                        EventSensitivity.INTERNAL,
                    ),
                    EventAttribute(
                        "snapshot_id",
                        str(value[0].snapshot_id),
                        EventSensitivity.INTERNAL,
                    ),
                    EventAttribute(
                        "workspace_revision",
                        revision.value,
                        EventSensitivity.INTERNAL,
                    ),
                ),
            ),
        )
        snapshot_ref, content_hash = outcome.value
        return CreateSnapshotResult(outcome.metadata, snapshot_ref, content_hash)

    async def restore_snapshot(self, request: RestoreSnapshotRequest) -> RestoreSnapshotResult:
        async def action(context: _OperationContext) -> tuple[SnapshotRef, Revision]:
            snapshot = await self._await_collaborator(
                lambda: self._snapshot_store.load(request.snapshot_ref),
                context.collaborator_deadline,
                request.cancellation,
                context.operation_id,
            )
            self._remaining(context.collaborator_deadline, context.operation_id)
            state = self._snapshot_codec.decode(snapshot)
            _validate_snapshot_state(state)
            candidate = await self._await_collaborator(
                lambda: self._workspace_snapshots.prepare_restore(
                    state.workspace,
                    required_directory=state.cwd,
                ),
                context.collaborator_deadline,
                request.cancellation,
                context.operation_id,
            )
            await self._publish_restore(
                candidate,
                state,
                context.collaborator_deadline,
                request.cancellation,
                context.operation_id,
            )
            return request.snapshot_ref, state.workspace.workspace_revision

        outcome = await self._run_operation(
            OperationKind.RESTORE_SNAPSHOT,
            request.limits,
            request.cancellation,
            lambda: None,
            action,
            post_commit_event=lambda value, revision: (
                SandboxEventType.SNAPSHOT_RESTORED,
                (
                    EventAttribute(
                        "snapshot_id",
                        str(value.snapshot_id),
                        EventSensitivity.INTERNAL,
                    ),
                    EventAttribute(
                        "workspace_revision",
                        revision.value,
                        EventSensitivity.INTERNAL,
                    ),
                ),
            ),
        )
        return RestoreSnapshotResult(outcome.metadata, request.snapshot_ref)

    async def close(self) -> None:
        close_task = self._close_task
        if close_task is not None:
            if close_task.done():
                return
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                try:
                    await asyncio.shield(close_task)
                finally:
                    raise
            return
        if self._state is SandboxSessionState.CLOSED:
            return
        self._state = SandboxSessionState.CLOSING
        close_task = asyncio.create_task(self._close_once())
        self._close_task = close_task
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            try:
                await asyncio.shield(close_task)
            finally:
                raise

    async def _close_once(self) -> None:
        await self._operation_gate.acquire()
        primary: SandboxError | None = None
        secondary: SandboxError | None = None
        try:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._lifecycle_limits.timeout_seconds
            collaborator_deadline = deadline - self._lifecycle_limits.terminal_event_reserve_seconds
            try:
                await self._emit_event(
                    self._new_event(SandboxEventType.SANDBOX_CLOSING),
                    collaborator_deadline,
                    operation_id=None,
                    close_timeout=True,
                )
            except SandboxError as error:
                primary = error
            try:
                await self._await_close_resource(collaborator_deadline)
            except SandboxError as error:
                if primary is None:
                    primary = error
                else:
                    secondary = error
            self._state = SandboxSessionState.CLOSED
            try:
                await self._emit_event(
                    self._new_event(SandboxEventType.SANDBOX_CLOSED),
                    deadline,
                    operation_id=None,
                    close_timeout=True,
                )
            except SandboxError as error:
                if primary is None:
                    primary = error
                elif secondary is None:
                    secondary = error
            if primary is not None:
                if secondary is not None:
                    primary.add_note(f"secondary close failure: {secondary}")
                raise primary
        finally:
            if self._state is not SandboxSessionState.CLOSED:
                self._state = SandboxSessionState.CLOSED
            self._operation_gate.release()

    async def _await_close_resource(self, deadline: float) -> None:
        task = asyncio.create_task(self._resource_scope.close())
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            task.cancel()
            timeout = SessionCloseTimeout("session close exceeded its cleanup budget")
            await _finish_timed_out_cleanup(task, timeout)
            raise timeout
        try:
            async with asyncio.timeout(remaining):
                await asyncio.shield(task)
        except TimeoutError as error:
            task.cancel()
            timeout = SessionCloseTimeout("session close exceeded its cleanup budget")
            await _finish_timed_out_cleanup(task, timeout)
            raise timeout from error
        except SandboxError as error:
            raise SessionCleanupFailed("session resource cleanup failed") from error
        except Exception as error:
            raise SessionCleanupFailed("session resource cleanup failed") from error

    async def _run_operation(
        self,
        kind: OperationKind,
        limits: OperationLimits,
        cancellation: CancellationSignal | None,
        normalize: Callable[[], SandboxPath | None],
        action: Callable[[_OperationContext], Awaitable[tuple[_T, Revision]]],
        post_commit_event: Callable[
            [_T, Revision],
            tuple[SandboxEventType, tuple[EventAttribute, ...]],
        ]
        | None = None,
    ) -> _OperationOutcome[_T]:
        self._reject_non_running()
        loop = asyncio.get_running_loop()
        started_monotonic = loop.time()
        original_deadline = started_monotonic + limits.timeout_seconds
        await self._acquire_operation_gate(
            original_deadline,
            cancellation,
        )
        operation_id: OperationId | None = None
        start_delivered = False
        try:
            self._reject_non_running()
            path = normalize()
            self._check_cooperative_cancellation(cancellation, operation_id)
            operation_id = OperationId(self._uuid_generator.new_uuid())
            started_at = self._clock.now()
            try:
                await self._emit_event(
                    self._new_event(
                        SandboxEventType.OPERATION_STARTED,
                        operation_id=operation_id,
                        operation_kind=kind,
                    ),
                    original_deadline,
                    operation_id=operation_id,
                )
            except (SessionEventDeliveryFailed, SessionOperationTimeout):
                raise
            start_delivered = True
            policy_deadline = original_deadline - limits.terminal_event_reserve_seconds
            self._remaining(policy_deadline, operation_id)
            decision = await self._await_collaborator(
                lambda: self._policy_engine.evaluate(
                    PolicyRequest(
                        self._session_id,
                        operation_id,
                        kind,
                        path,
                        None,
                        limits,
                    )
                ),
                policy_deadline,
                cancellation,
                operation_id,
            )
            self._validate_policy_decision(decision, limits, operation_id)
            if not decision.allowed:
                raise SessionPolicyDenied(
                    f"policy denied {kind.value}: {decision.reason_code}",
                    reason_code=decision.reason_code,
                    operation_id=operation_id,
                )
            effective_deadline = started_monotonic + decision.effective_limits.timeout_seconds
            collaborator_deadline = effective_deadline - limits.terminal_event_reserve_seconds
            self._remaining(collaborator_deadline, operation_id)
            context = _OperationContext(
                operation_id,
                started_at,
                started_monotonic,
                original_deadline,
                collaborator_deadline,
            )
            value, revision = await action(context)
            completed_at = self._clock.now()
            metadata = OperationResultMetadata(
                session_id=self._session_id,
                operation_id=operation_id,
                workspace_revision=revision,
                started_at=started_at,
                completed_at=completed_at,
            )
            if post_commit_event is not None:
                post_commit_deadline = original_deadline - limits.terminal_event_reserve_seconds / 2
                try:
                    event_type, attributes = post_commit_event(value, revision)
                    await self._emit_event(
                        self._new_event(
                            event_type,
                            operation_id=operation_id,
                            operation_kind=kind,
                            attributes=attributes,
                        ),
                        post_commit_deadline,
                        operation_id=operation_id,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    raise SessionEventDeliveryFailed(
                        f"required post-commit event delivery failed for {kind.value}",
                        operation_id=operation_id,
                    ) from error
        except asyncio.CancelledError as cancellation_error:
            if start_delivered and operation_id is not None:
                try:
                    await self._emit_terminal(
                        SandboxEventType.OPERATION_CANCELLED,
                        kind,
                        operation_id,
                        original_deadline,
                    )
                except SandboxError as terminal_error:
                    cancellation_error.add_note(
                        f"required cancellation event delivery failed: {terminal_error}"
                    )
            raise
        except SandboxError as error:
            if start_delivered and operation_id is not None:
                terminal = _terminal_for_error(error)
                try:
                    await self._emit_terminal(
                        terminal,
                        kind,
                        operation_id,
                        original_deadline,
                    )
                except SandboxError as terminal_error:
                    raise terminal_error from error
            raise
        except Exception as error:
            unexpected = SessionFailed(
                "operation collaborator failed unexpectedly",
                operation_id=operation_id,
            )
            if start_delivered and operation_id is not None:
                try:
                    await self._emit_terminal(
                        SandboxEventType.OPERATION_FAILED,
                        kind,
                        operation_id,
                        original_deadline,
                    )
                except SandboxError as terminal_error:
                    raise terminal_error from unexpected
            raise unexpected from error
        else:
            await self._emit_terminal(
                SandboxEventType.OPERATION_COMPLETED,
                kind,
                operation_id,
                original_deadline,
                attributes=(
                    EventAttribute(
                        "workspace_revision",
                        revision.value,
                        EventSensitivity.INTERNAL,
                    ),
                ),
            )
            return _OperationOutcome(value, metadata)
        finally:
            self._operation_gate.release()

    async def _acquire_operation_gate(
        self,
        deadline: float,
        cancellation: CancellationSignal | None,
    ) -> None:
        while True:
            self._check_cooperative_cancellation(cancellation, None)
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise SessionOperationTimeout("operation timed out while waiting for admission")
            try:
                await asyncio.wait_for(
                    self._operation_gate.acquire(),
                    timeout=min(remaining, _POLL_SECONDS),
                )
                return
            except TimeoutError:
                if asyncio.get_running_loop().time() >= deadline:
                    raise SessionOperationTimeout(
                        "operation timed out while waiting for admission"
                    ) from None

    async def _await_collaborator(
        self,
        factory: Callable[[], Awaitable[_T]],
        deadline: float,
        cancellation: CancellationSignal | None,
        operation_id: OperationId,
    ) -> _T:
        self._remaining(deadline, operation_id)
        self._check_cooperative_cancellation(cancellation, operation_id)
        task = asyncio.ensure_future(factory())
        try:
            while not task.done():
                self._check_cooperative_cancellation(cancellation, operation_id)
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    task.cancel()
                    await _await_cancelled_task(task)
                    raise SessionOperationTimeout(
                        "operation exceeded its protected collaborator budget",
                        operation_id=operation_id,
                    )
                await asyncio.wait((task,), timeout=min(remaining, _POLL_SECONDS))
            result = await task
            self._remaining(deadline, operation_id)
            self._check_cooperative_cancellation(cancellation, operation_id)
            return result
        except asyncio.CancelledError:
            task.cancel()
            await _await_cancelled_task(task)
            raise
        except SessionOperationCancelled:
            task.cancel()
            await _await_cancelled_task(task)
            raise

    async def _publish_restore(
        self,
        candidate: PreparedWorkspaceRestore,
        state: SessionSnapshotState,
        deadline: float,
        cancellation: CancellationSignal | None,
        operation_id: OperationId,
    ) -> None:
        self._remaining(deadline, operation_id)
        self._check_cooperative_cancellation(cancellation, operation_id)

        async def publish() -> None:
            await self._workspace_snapshots.commit_restore(candidate)
            self._cwd = state.cwd
            self._environment = state.approved_environment

        task = asyncio.create_task(publish())
        pending_error: SandboxError | None = None
        try:
            while not task.done():
                if cancellation is not None and cancellation.is_set():
                    pending_error = SessionOperationCancelled(
                        "operation was cooperatively cancelled",
                        operation_id=operation_id,
                    )
                    task.cancel()
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    pending_error = SessionOperationTimeout(
                        "operation exceeded its protected collaborator budget",
                        operation_id=operation_id,
                    )
                    task.cancel()
                    break
                await asyncio.wait((task,), timeout=_POLL_SECONDS)
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                if pending_error is not None:
                    raise pending_error from None
                raise
        except asyncio.CancelledError as cancellation_error:
            task.cancel()
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                pass
            except SandboxError as error:
                cancellation_error.add_note(
                    f"restore publication also failed during cancellation: {error}"
                )
            except Exception as error:
                self._state = SandboxSessionState.FAILED
                cancellation_error.add_note(
                    "restore publication had an unprovable live-state outcome during "
                    f"cancellation: {error}"
                )
            raise
        except SandboxError:
            raise
        except Exception as error:
            restore_error = SessionSnapshotRestoreFailed(
                "snapshot state publication failed with an unprovable live-state outcome",
                operation_id=operation_id,
            )
            self._state = SandboxSessionState.FAILED
            await self._emit_failed_lifecycle(restore_error)
            raise restore_error from error

    async def _emit_event(
        self,
        event: SandboxEvent,
        deadline: float,
        *,
        operation_id: OperationId | None,
        close_timeout: bool = False,
    ) -> None:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            if close_timeout:
                raise SessionCloseTimeout("session lifecycle event delivery timed out")
            raise SessionOperationTimeout(
                "required event delivery exceeded the operation deadline",
                operation_id=operation_id,
            )
        try:
            async with asyncio.timeout(remaining):
                await self._event_sink.emit(event)
        except TimeoutError as error:
            if close_timeout:
                raise SessionCloseTimeout("session lifecycle event delivery timed out") from error
            raise SessionOperationTimeout(
                "required event delivery exceeded the operation deadline",
                operation_id=operation_id,
            ) from error
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise SessionEventDeliveryFailed(
                f"required event delivery failed for {event.event_type}",
                operation_id=operation_id,
            ) from error

    async def _emit_terminal(
        self,
        event_type: SandboxEventType,
        kind: OperationKind,
        operation_id: OperationId,
        deadline: float,
        attributes: tuple[EventAttribute, ...] = (),
    ) -> None:
        await self._emit_event(
            self._new_event(
                event_type,
                operation_id=operation_id,
                operation_kind=kind,
                attributes=attributes,
            ),
            deadline,
            operation_id=operation_id,
        )

    async def _emit_failed_lifecycle(self, primary: SandboxError) -> None:
        deadline = asyncio.get_running_loop().time() + self._lifecycle_limits.timeout_seconds
        try:
            await self._emit_event(
                self._new_event(SandboxEventType.SANDBOX_FAILED),
                deadline,
                operation_id=None,
            )
        except SandboxError as error:
            primary.add_note(f"sandbox.failed delivery also failed: {error}")

    def _new_event(
        self,
        event_type: SandboxEventType,
        *,
        operation_id: OperationId | None = None,
        operation_kind: OperationKind | None = None,
        attributes: tuple[EventAttribute, ...] = (),
    ) -> SandboxEvent:
        self._event_sequence += 1
        return SandboxEvent(
            event_type,
            self._clock.now(),
            self._session_id,
            self._event_sequence,
            operation_id,
            None,
            operation_kind,
            attributes,
        )

    def _reject_non_running(self) -> None:
        if self._state is SandboxSessionState.RUNNING:
            return
        if self._state is SandboxSessionState.CREATED:
            raise SessionNotRunning("session has not been started")
        if self._state is SandboxSessionState.CLOSING:
            raise SessionClosing("session is closing")
        if self._state is SandboxSessionState.CLOSED:
            raise SessionClosed("session is closed")
        raise SessionFailed("session is failed")

    @staticmethod
    def _validate_policy_decision(
        decision: PolicyDecision,
        requested: OperationLimits,
        operation_id: OperationId,
    ) -> None:
        decision_value = cast(object, decision)
        if not isinstance(decision_value, PolicyDecision):
            raise SessionRequestInvalid(
                "policy engine returned an invalid decision",
                operation_id=operation_id,
            )
        effective = decision.effective_limits
        if effective.timeout_seconds > requested.timeout_seconds:
            raise SessionRequestInvalid(
                "policy may only narrow timeout_seconds",
                operation_id=operation_id,
            )
        if effective.terminal_event_reserve_seconds != requested.terminal_event_reserve_seconds:
            raise SessionRequestInvalid(
                "policy must preserve terminal_event_reserve_seconds",
                operation_id=operation_id,
            )

    @staticmethod
    def _remaining(deadline: float, operation_id: OperationId) -> float:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise SessionOperationTimeout(
                "operation exceeded its protected collaborator budget",
                operation_id=operation_id,
            )
        return remaining

    @staticmethod
    def _check_cooperative_cancellation(
        cancellation: CancellationSignal | None,
        operation_id: OperationId | None,
    ) -> None:
        if cancellation is not None and cancellation.is_set():
            raise SessionOperationCancelled(
                "operation was cooperatively cancelled",
                operation_id=operation_id,
            )


def _terminal_for_error(error: SandboxError) -> SandboxEventType:
    if error.category is ErrorCategory.TIMEOUT:
        return SandboxEventType.OPERATION_TIMED_OUT
    if error.category is ErrorCategory.CANCELLED:
        return SandboxEventType.OPERATION_CANCELLED
    return SandboxEventType.OPERATION_FAILED


async def _await_cancelled_task[T](task: asyncio.Future[T]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        return


async def _finish_timed_out_cleanup(
    task: asyncio.Future[None],
    timeout: SessionCloseTimeout,
) -> None:
    try:
        await task
    except asyncio.CancelledError:
        return
    except Exception as error:
        timeout.add_note(f"resource cleanup also failed after timeout: {error}")


def _apply_environment_changes(
    environment: CommandEnvironment,
    changes: tuple[EnvironmentChange, ...],
) -> CommandEnvironment:
    current = {item.name: item.value for item in environment.values}
    for item in changes:
        name = item.name
        value = item.value
        if value is None:
            current.pop(name, None)
        else:
            current[name] = value
    return CommandEnvironment(
        tuple(EnvironmentValue(name, value) for name, value in sorted(current.items()))
    )


def _validate_snapshot_state(state: SessionSnapshotState) -> None:
    value = cast(object, state)
    if not isinstance(value, SessionSnapshotState):
        raise SnapshotCorrupt("snapshot codec returned an invalid session state")
    if state.schema_version != 1:
        raise SnapshotIncompatible(
            f"session snapshot schema version {state.schema_version} is unsupported"
        )
    if state.capability_profile_version != 1:
        raise SnapshotIncompatible(
            f"capability profile version {state.capability_profile_version} is unsupported"
        )
