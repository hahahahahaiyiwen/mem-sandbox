"""Public workspace boundary protocols."""

from typing import Protocol

from mem_sandbox.workspace.models import (
    PreparedWorkspaceRestore,
    WorkspaceArchiveData,
)
from mem_sandbox.workspace.paths import SandboxPath


class WorkspaceArchivePort(Protocol):
    """Portable archive export and prepared hydration boundary."""

    async def export_portable_archive(self) -> WorkspaceArchiveData: ...

    async def prepare_archive_restore(
        self,
        data: WorkspaceArchiveData,
        *,
        required_directory: SandboxPath,
    ) -> PreparedWorkspaceRestore: ...

    async def commit_restore(self, candidate: PreparedWorkspaceRestore) -> None: ...
