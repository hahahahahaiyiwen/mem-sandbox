from __future__ import annotations

import pytest

from mem_sandbox.workspace import (
    AnyCurrentState,
    CopyPathRequest,
    DestinationWithinSourceError,
    MakeDirectoryRequest,
    MemoryWorkspace,
    MovePathRequest,
    PathAlreadyExistsError,
    PathNotFoundError,
    RootModificationError,
    SamePathError,
    SandboxPath,
    WorkspaceLimits,
    WorkspaceSizeLimitExceededError,
    WorkspaceWriteRequest,
)


@pytest.mark.asyncio
async def test_copy_file_and_directory_are_atomic() -> None:
    workspace = MemoryWorkspace()
    source = SandboxPath.resolve("/workspace/src")
    await workspace.mkdir(MakeDirectoryRequest(source))
    await workspace.write(WorkspaceWriteRequest(source.join("a.txt"), b"a", AnyCurrentState()))
    await workspace.write(
        WorkspaceWriteRequest(source.join("nested/b.txt"), b"bb", AnyCurrentState(), True)
    )

    result = await workspace.copy(CopyPathRequest(source, SandboxPath.resolve("/workspace/backup")))

    assert result.stats.revision.value == 4
    assert (
        await workspace.read_bytes(SandboxPath.resolve("/workspace/backup/a.txt"))
    ).content == b"a"
    assert (
        await workspace.read_bytes(SandboxPath.resolve("/workspace/backup/nested/b.txt"))
    ).content == b"bb"


@pytest.mark.asyncio
async def test_move_removes_source_and_commits_once() -> None:
    workspace = MemoryWorkspace()
    source = SandboxPath.resolve("/workspace/source.txt")
    destination = SandboxPath.resolve("/workspace/destination.txt")
    await workspace.write(WorkspaceWriteRequest(source, b"content", AnyCurrentState()))

    result = await workspace.move(MovePathRequest(source, destination))

    assert result.stats.revision.value == 2
    assert (await workspace.read_bytes(destination)).content == b"content"
    with pytest.raises(PathNotFoundError):
        await workspace.stat(source)


@pytest.mark.asyncio
async def test_copy_and_move_validate_conflicts_before_mutation() -> None:
    workspace = MemoryWorkspace()
    source = SandboxPath.resolve("/workspace/source")
    destination = SandboxPath.resolve("/workspace/destination")
    await workspace.write(WorkspaceWriteRequest(source, b"source", AnyCurrentState()))
    await workspace.write(WorkspaceWriteRequest(destination, b"destination", AnyCurrentState()))
    before = await workspace.stats()

    with pytest.raises(PathAlreadyExistsError):
        await workspace.copy(CopyPathRequest(source, destination))

    with pytest.raises(PathAlreadyExistsError):
        await workspace.move(MovePathRequest(source, destination))

    assert await workspace.stats() == before
    assert (await workspace.read_bytes(source)).content == b"source"
    assert (await workspace.read_bytes(destination)).content == b"destination"

    await workspace.copy(CopyPathRequest(source, destination, overwrite=True))
    assert (await workspace.read_bytes(destination)).content == b"source"


@pytest.mark.asyncio
async def test_source_destination_and_descendant_conflicts_are_explicit() -> None:
    workspace = MemoryWorkspace()
    source = SandboxPath.resolve("/workspace/source")
    await workspace.mkdir(MakeDirectoryRequest(source))

    with pytest.raises(SamePathError):
        await workspace.copy(CopyPathRequest(source, source))

    with pytest.raises(SamePathError):
        await workspace.move(MovePathRequest(source, source))

    descendant = source.join("nested")
    with pytest.raises(DestinationWithinSourceError):
        await workspace.copy(CopyPathRequest(source, descendant))

    with pytest.raises(DestinationWithinSourceError):
        await workspace.move(MovePathRequest(source, descendant))


@pytest.mark.asyncio
async def test_root_cannot_be_copy_or_move_source() -> None:
    workspace = MemoryWorkspace()
    destination = SandboxPath.resolve("/workspace/other")

    with pytest.raises(RootModificationError):
        await workspace.copy(CopyPathRequest(SandboxPath.root(), destination))

    with pytest.raises(RootModificationError):
        await workspace.move(MovePathRequest(SandboxPath.root(), destination))


@pytest.mark.asyncio
async def test_copy_quota_failure_leaves_destination_absent() -> None:
    workspace = MemoryWorkspace(WorkspaceLimits(max_file_bytes=4, max_total_bytes=4))
    source = SandboxPath.resolve("/workspace/source")
    destination = SandboxPath.resolve("/workspace/destination")
    await workspace.write(WorkspaceWriteRequest(source, b"1234", AnyCurrentState()))
    before = await workspace.stats()

    with pytest.raises(WorkspaceSizeLimitExceededError):
        await workspace.copy(CopyPathRequest(source, destination))

    assert await workspace.stats() == before
    with pytest.raises(PathNotFoundError):
        await workspace.stat(destination)
