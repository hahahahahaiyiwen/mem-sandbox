from __future__ import annotations

import pytest

from mem_sandbox.workspace import (
    AnyCurrentState,
    MakeDirectoryRequest,
    MemoryWorkspace,
    NotADirectoryError,
    PathNotFoundError,
    RestoreCandidateMismatchError,
    SandboxPath,
    WorkspaceWriteRequest,
)


@pytest.mark.asyncio
async def test_prepared_restore_validates_required_directory_without_mutation() -> None:
    source = MemoryWorkspace()
    project = source.resolve_path("/workspace/project")
    await source.mkdir(MakeDirectoryRequest(project))
    await source.write(
        WorkspaceWriteRequest(project.join("file.txt"), b"snapshot", AnyCurrentState())
    )
    snapshot = await source.export()

    target = MemoryWorkspace()
    live = target.resolve_path("/workspace/live.txt")
    await target.write(WorkspaceWriteRequest(live, b"live", AnyCurrentState()))
    before = await target.stats()

    with pytest.raises(PathNotFoundError):
        await target.prepare_restore(
            snapshot,
            required_directory=target.resolve_path("/workspace/missing"),
        )
    assert await target.stats() == before
    assert (await target.read_bytes(live)).content == b"live"

    with pytest.raises(NotADirectoryError):
        await target.prepare_restore(
            snapshot,
            required_directory=target.resolve_path("/workspace/project/file.txt"),
        )
    assert await target.stats() == before


@pytest.mark.asyncio
async def test_prepared_restore_is_bound_to_preparing_workspace_and_can_rewind_revision() -> None:
    source = MemoryWorkspace()
    file_path = source.resolve_path("/workspace/file.txt")
    await source.write(WorkspaceWriteRequest(file_path, b"snapshot", AnyCurrentState()))
    snapshot = await source.export()

    first = MemoryWorkspace()
    second = MemoryWorkspace()
    candidate = await first.prepare_restore(
        snapshot,
        required_directory=SandboxPath.root(),
    )

    with pytest.raises(RestoreCandidateMismatchError):
        await second.commit_restore(candidate)

    await first.write(WorkspaceWriteRequest(file_path, b"newer", AnyCurrentState()))
    await first.write(WorkspaceWriteRequest(file_path, b"newest", AnyCurrentState()))
    assert (await first.stats()).revision.value == 2
    await first.commit_restore(candidate)
    assert (await first.stats()).revision == snapshot.workspace_revision
    assert (await first.read_bytes(file_path)).content == b"snapshot"
