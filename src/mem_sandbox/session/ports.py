"""Narrow behavior ports owned by the session boundary."""

from typing import Protocol

from mem_sandbox.command_executor import (
    CommandExecutionContext,
    ExecuteRequest,
    ExecuteResult,
)
from mem_sandbox.events import SandboxEvent
from mem_sandbox.policy import PolicyDecision, PolicyRequest
from mem_sandbox.secrets import SecretAccessRequest, SecretLease
from mem_sandbox.snapshots import (
    SandboxSnapshot,
    SessionSnapshotState,
    SnapshotPayload,
    SnapshotRef,
)
from mem_sandbox.workspace import (
    PreparedWorkspaceRestore,
    SandboxPath,
    WorkspaceBinaryResult,
    WorkspaceEntry,
    WorkspaceMutation,
    WorkspacePatchRequest,
    WorkspacePatchResult,
    WorkspaceRangeRequest,
    WorkspaceRangeResult,
    WorkspaceSnapshotData,
    WorkspaceStats,
    WorkspaceWriteRequest,
)


class SessionWorkspaceReader(Protocol):
    def resolve_path(self, value: str, *, cwd: SandboxPath | None = None) -> SandboxPath: ...
    async def stats(self) -> WorkspaceStats: ...
    async def stat(self, path: SandboxPath) -> WorkspaceEntry: ...
    async def list(self, path: SandboxPath) -> tuple[WorkspaceEntry, ...]: ...
    async def read_range(self, request: WorkspaceRangeRequest) -> WorkspaceRangeResult: ...
    async def read_bytes(self, path: SandboxPath) -> WorkspaceBinaryResult: ...


class SessionWorkspaceMutator(Protocol):
    async def write(self, request: WorkspaceWriteRequest) -> WorkspaceMutation: ...
    async def patch(self, request: WorkspacePatchRequest) -> WorkspacePatchResult: ...


class SessionCommandExecutor(Protocol):
    async def execute(
        self,
        request: ExecuteRequest,
        context: CommandExecutionContext,
    ) -> ExecuteResult: ...


class SessionPolicyEngine(Protocol):
    async def evaluate(self, request: PolicyRequest) -> PolicyDecision: ...


class SessionSecretBroker(Protocol):
    async def lease(self, request: SecretAccessRequest) -> SecretLease: ...


class SessionEventSink(Protocol):
    async def emit(self, event: SandboxEvent) -> None: ...


class SessionSnapshotStore(Protocol):
    @property
    def process_local(self) -> bool: ...

    async def save(self, snapshot: SandboxSnapshot) -> SnapshotRef: ...
    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot: ...


class SessionSnapshotCodec(Protocol):
    def encode(self, state: SessionSnapshotState) -> SnapshotPayload: ...
    def decode(self, snapshot: SandboxSnapshot) -> SessionSnapshotState: ...


class SessionWorkspaceSnapshotPort(Protocol):
    async def export(self) -> WorkspaceSnapshotData: ...
    async def prepare_restore(
        self,
        data: WorkspaceSnapshotData,
        *,
        required_directory: SandboxPath,
    ) -> PreparedWorkspaceRestore: ...
    async def commit_restore(self, candidate: PreparedWorkspaceRestore) -> None: ...


class SessionResourceScope(Protocol):
    async def close(self) -> None: ...
