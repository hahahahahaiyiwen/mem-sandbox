"""Default constructor-injected SandboxSession factory."""

from __future__ import annotations

from mem_sandbox.command_executor import create_default_executor
from mem_sandbox.core import Clock, SandboxError, UuidGenerator
from mem_sandbox.events import EventDispatcher, EventSink
from mem_sandbox.service.errors import InvalidSandboxRequest, SessionFactoryFailed
from mem_sandbox.service.models import SessionFactoryRequest
from mem_sandbox.service.ports import ServiceSessionRuntime
from mem_sandbox.service.resources import (
    CompositeResourceScope,
    DefaultServiceSessionRuntime,
)
from mem_sandbox.session import (
    NoOpSessionResourceScope,
    SandboxSession,
    SessionPolicyEngine,
    SessionSecretBroker,
    SessionSnapshotCodec,
)
from mem_sandbox.workspace import (
    AnyCurrentState,
    MemoryWorkspace,
    SandboxPath,
    WorkspaceWriteRequest,
)


class DefaultSessionFactory:
    """Assemble an isolated workspace and its session collaborators."""

    def __init__(
        self,
        *,
        policy_engine: SessionPolicyEngine,
        secret_broker: SessionSecretBroker,
        event_sink: EventSink,
        snapshot_codec: SessionSnapshotCodec,
        clock: Clock,
        uuid_generator: UuidGenerator,
    ) -> None:
        self._policy_engine = policy_engine
        self._secret_broker = secret_broker
        self._event_sink = event_sink
        self._snapshot_codec = snapshot_codec
        self._clock = clock
        self._uuid_generator = uuid_generator

    async def create(self, request: SessionFactoryRequest) -> ServiceSessionRuntime:
        workspace = MemoryWorkspace(request.options.workspace_limits)
        try:
            await _prepare_initial_state(workspace, request)
        except (SandboxError, ValueError, TypeError):
            raise
        except Exception as error:
            raise SessionFactoryFailed("session factory construction failed") from error

        dispatcher = EventDispatcher(self._event_sink)
        post_session_scope = CompositeResourceScope((dispatcher,))
        try:
            session = SandboxSession(
                session_id=request.session_id,
                workspace_reader=workspace,
                workspace_mutator=workspace,
                workspace_snapshots=workspace,
                command_executor=create_default_executor(workspace, workspace),
                policy_engine=self._policy_engine,
                secret_broker=self._secret_broker,
                event_sink=dispatcher,
                snapshot_store=request.snapshot_store,
                snapshot_codec=self._snapshot_codec,
                resource_scope=NoOpSessionResourceScope(),
                clock=self._clock,
                uuid_generator=self._uuid_generator,
                initial_cwd=(
                    request.restored_state.cwd if request.restored_state is not None else None
                ),
                initial_environment=(
                    request.restored_state.approved_environment
                    if request.restored_state is not None
                    else None
                ),
                lifecycle_limits=request.options.lifecycle_limits,
            )
            return DefaultServiceSessionRuntime(
                session,
                post_session_scope,
                timeout_seconds=request.options.lifecycle_limits.timeout_seconds,
            )
        except (SandboxError, ValueError, TypeError) as error:
            await _close_after_construction_failure(post_session_scope, error)
            raise
        except Exception as error:
            await _close_after_construction_failure(post_session_scope, error)
            raise SessionFactoryFailed("session factory construction failed") from error


async def _prepare_initial_state(
    workspace: MemoryWorkspace,
    request: SessionFactoryRequest,
) -> None:
    seen_paths: set[SandboxPath] = set()
    for seed in request.initial_files:
        path = workspace.resolve_path(seed.path, cwd=SandboxPath.root())
        if path in seen_paths:
            raise InvalidSandboxRequest("initial_files contain duplicate normalized seed paths")
        seen_paths.add(path)
        await workspace.write(
            WorkspaceWriteRequest(
                path=path,
                content=seed.content,
                precondition=AnyCurrentState(),
                create_parents=True,
            )
        )
    if request.restored_state is not None:
        candidate = await workspace.prepare_restore(
            request.restored_state.workspace,
            required_directory=request.restored_state.cwd,
        )
        await workspace.commit_restore(candidate)


async def _close_after_construction_failure(
    scope: CompositeResourceScope,
    primary: BaseException | None = None,
) -> None:
    try:
        await scope.close()
    except BaseException as cleanup_error:
        if primary is None:
            raise
        primary.add_note(f"secondary factory cleanup failure: {cleanup_error}")
