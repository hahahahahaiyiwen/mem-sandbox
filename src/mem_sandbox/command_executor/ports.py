"""Focused workspace ports owned by the command-executor boundary."""

from typing import Protocol

from mem_sandbox.workspace import (
    MakeDirectoryRequest,
    RemovePathRequest,
    SandboxPath,
    WorkspaceAppendRequest,
    WorkspaceEntry,
    WorkspaceMutation,
    WorkspaceTextResult,
    WorkspaceWriteRequest,
)


class CommandWorkspaceReader(Protocol):
    """Read capabilities required by first-wave commands."""

    def resolve_path(self, value: str, *, cwd: SandboxPath | None = None) -> SandboxPath: ...

    async def stat(self, path: SandboxPath) -> WorkspaceEntry: ...

    async def list(self, path: SandboxPath) -> tuple[WorkspaceEntry, ...]: ...

    async def read_text(self, path: SandboxPath) -> WorkspaceTextResult: ...


class CommandWorkspaceMutator(Protocol):
    """Mutation capabilities required by first-wave commands and redirection."""

    def resolve_path(self, value: str, *, cwd: SandboxPath | None = None) -> SandboxPath: ...

    async def mkdir(self, request: MakeDirectoryRequest) -> WorkspaceMutation: ...

    async def write(self, request: WorkspaceWriteRequest) -> WorkspaceMutation: ...

    async def append(self, request: WorkspaceAppendRequest) -> WorkspaceMutation: ...

    async def remove(self, request: RemovePathRequest) -> WorkspaceMutation: ...
