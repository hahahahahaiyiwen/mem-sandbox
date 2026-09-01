from __future__ import annotations

import asyncio

import pytest

from mem_sandbox.workspace import (
    AnyCurrentState,
    ContentHashMustEqual,
    DirectoryNotEmptyError,
    FileSizeLimitExceededError,
    MakeDirectoryRequest,
    MemoryWorkspace,
    NodeLimitExceededError,
    NotAFileError,
    PathAlreadyExistsError,
    PathMustNotExist,
    PathNotFoundError,
    RemovePathRequest,
    RootModificationError,
    SandboxPath,
    StaleContentError,
    WorkspaceAppendRequest,
    WorkspaceLimits,
    WorkspaceSizeLimitExceededError,
    WorkspaceWriteRequest,
)


@pytest.mark.asyncio
async def test_mkdir_and_write_can_create_parents_in_one_revision() -> None:
    workspace = MemoryWorkspace()

    directory = await workspace.mkdir(
        MakeDirectoryRequest(
            SandboxPath.resolve("/workspace/a/b"),
            create_parents=True,
        )
    )
    file_mutation = await workspace.write(
        WorkspaceWriteRequest(
            SandboxPath.resolve("/workspace/c/d/file.txt"),
            b"content",
            PathMustNotExist(),
            create_parents=True,
        )
    )

    assert directory.stats.revision.value == 1
    assert directory.stats.node_count == 3
    assert file_mutation.stats.revision.value == 2
    assert file_mutation.stats.node_count == 6
    assert file_mutation.created
    assert file_mutation.previous_hash is None


@pytest.mark.asyncio
async def test_write_preconditions_prevent_lost_updates() -> None:
    workspace = MemoryWorkspace()
    path = SandboxPath.resolve("/workspace/file.txt")

    created = await workspace.write(WorkspaceWriteRequest(path, b"first", PathMustNotExist()))
    created_hash = created.current_hash
    assert created_hash is not None

    with pytest.raises(PathAlreadyExistsError):
        await workspace.write(WorkspaceWriteRequest(path, b"duplicate", PathMustNotExist()))

    replaced = await workspace.write(
        WorkspaceWriteRequest(
            path,
            b"second",
            ContentHashMustEqual(created_hash),
        )
    )

    with pytest.raises(StaleContentError):
        await workspace.write(
            WorkspaceWriteRequest(
                path,
                b"stale",
                ContentHashMustEqual(created_hash),
            )
        )

    assert (await workspace.read_bytes(path)).content == b"second"
    assert replaced.previous_hash == created_hash
    assert (await workspace.stats()).revision.value == 2


@pytest.mark.asyncio
async def test_append_is_atomic_quota_aware_and_stale_safe() -> None:
    workspace = MemoryWorkspace(WorkspaceLimits(max_file_bytes=5, max_total_bytes=5))
    path = SandboxPath.resolve("/workspace/file.txt")
    created = await workspace.append(WorkspaceAppendRequest(path, b"ab", PathMustNotExist()))
    created_hash = created.current_hash
    assert created_hash is not None

    appended = await workspace.append(
        WorkspaceAppendRequest(path, b"cd", ContentHashMustEqual(created_hash))
    )

    assert appended.previous_hash == created_hash
    assert appended.stats.revision.value == 2
    assert (await workspace.read_bytes(path)).content == b"abcd"

    before = await workspace.stats()
    with pytest.raises(StaleContentError):
        await workspace.append(
            WorkspaceAppendRequest(path, b"x", ContentHashMustEqual(created_hash))
        )
    with pytest.raises(FileSizeLimitExceededError):
        await workspace.append(WorkspaceAppendRequest(path, b"xy", AnyCurrentState()))

    assert await workspace.stats() == before
    assert (await workspace.read_bytes(path)).content == b"abcd"


@pytest.mark.asyncio
async def test_append_rejects_directory_targets_without_mutation() -> None:
    workspace = MemoryWorkspace()
    directory = SandboxPath.resolve("/workspace/directory")
    await workspace.mkdir(MakeDirectoryRequest(directory))
    before = await workspace.stats()

    with pytest.raises(NotAFileError):
        await workspace.append(WorkspaceAppendRequest(directory, b"content", AnyCurrentState()))

    assert await workspace.stats() == before


@pytest.mark.asyncio
async def test_two_serialized_writers_with_one_hash_produce_one_conflict() -> None:
    workspace = MemoryWorkspace()
    path = SandboxPath.resolve("/workspace/file.txt")
    created = await workspace.write(WorkspaceWriteRequest(path, b"base", PathMustNotExist()))
    created_hash = created.current_hash
    assert created_hash is not None

    async def replace(content: bytes) -> str:
        try:
            await workspace.write(
                WorkspaceWriteRequest(
                    path,
                    content,
                    ContentHashMustEqual(created_hash),
                )
            )
        except StaleContentError:
            return "conflict"
        return "committed"

    outcomes = await asyncio.gather(replace(b"one"), replace(b"two"))

    assert sorted(outcomes) == ["committed", "conflict"]
    assert (await workspace.stats()).revision.value == 2


@pytest.mark.asyncio
async def test_failed_quota_mutations_preserve_complete_state() -> None:
    workspace = MemoryWorkspace(
        WorkspaceLimits(
            max_file_bytes=4,
            max_total_bytes=5,
            max_nodes=3,
        )
    )
    first = SandboxPath.resolve("/workspace/a")
    await workspace.write(WorkspaceWriteRequest(first, b"1234", AnyCurrentState()))
    before = await workspace.stats()

    with pytest.raises(FileSizeLimitExceededError):
        await workspace.write(
            WorkspaceWriteRequest(
                SandboxPath.resolve("/workspace/too-big"),
                b"12345",
                AnyCurrentState(),
            )
        )

    with pytest.raises(WorkspaceSizeLimitExceededError):
        await workspace.write(
            WorkspaceWriteRequest(
                SandboxPath.resolve("/workspace/b"),
                b"12",
                AnyCurrentState(),
            )
        )

    await workspace.write(
        WorkspaceWriteRequest(
            SandboxPath.resolve("/workspace/b"),
            b"1",
            AnyCurrentState(),
        )
    )
    full = await workspace.stats()

    with pytest.raises(NodeLimitExceededError):
        await workspace.mkdir(MakeDirectoryRequest(SandboxPath.resolve("/workspace/c")))

    assert await workspace.stats() == full
    assert (await workspace.read_bytes(first)).content == b"1234"
    assert before.revision.value == 1


@pytest.mark.asyncio
async def test_replacement_quota_accounts_for_previous_content() -> None:
    workspace = MemoryWorkspace(WorkspaceLimits(max_file_bytes=5, max_total_bytes=5))
    path = SandboxPath.resolve("/workspace/file")
    await workspace.write(WorkspaceWriteRequest(path, b"12345", AnyCurrentState()))

    replaced = await workspace.write(WorkspaceWriteRequest(path, b"12", AnyCurrentState()))

    assert replaced.stats.total_bytes == 2
    assert replaced.stats.node_count == 2


@pytest.mark.asyncio
async def test_remove_rules_and_idempotent_missing_remove() -> None:
    workspace = MemoryWorkspace()
    directory = SandboxPath.resolve("/workspace/dir")
    file_path = SandboxPath.resolve("/workspace/dir/file")
    await workspace.mkdir(MakeDirectoryRequest(directory))
    await workspace.write(WorkspaceWriteRequest(file_path, b"x", AnyCurrentState()))

    with pytest.raises(DirectoryNotEmptyError):
        await workspace.remove(RemovePathRequest(directory))

    before = await workspace.stats()
    missing = await workspace.remove(
        RemovePathRequest(
            SandboxPath.resolve("/workspace/missing"),
            missing_ok=True,
        )
    )
    assert not missing.changed
    assert missing.stats == before

    removed = await workspace.remove(RemovePathRequest(directory, recursive=True))

    assert removed.current_hash is None
    assert removed.stats.total_bytes == 0
    assert removed.stats.node_count == 1

    with pytest.raises(PathNotFoundError):
        await workspace.stat(file_path)


@pytest.mark.asyncio
async def test_root_modification_and_directory_write_are_rejected() -> None:
    workspace = MemoryWorkspace()
    directory = SandboxPath.resolve("/workspace/dir")
    await workspace.mkdir(MakeDirectoryRequest(directory))

    with pytest.raises(RootModificationError):
        await workspace.remove(RemovePathRequest(SandboxPath.root(), recursive=True))

    with pytest.raises(NotAFileError):
        await workspace.write(WorkspaceWriteRequest(directory, b"content", AnyCurrentState()))


@pytest.mark.asyncio
async def test_missing_parent_requires_explicit_parent_creation() -> None:
    workspace = MemoryWorkspace()

    with pytest.raises(PathNotFoundError, match="parent"):
        await workspace.write(
            WorkspaceWriteRequest(
                SandboxPath.resolve("/workspace/a/b/file"),
                b"x",
                AnyCurrentState(),
            )
        )


@pytest.mark.asyncio
async def test_identical_states_have_identical_root_hashes() -> None:
    first = MemoryWorkspace()
    second = MemoryWorkspace()
    paths = (
        SandboxPath.resolve("/workspace/a"),
        SandboxPath.resolve("/workspace/b"),
    )

    for path in paths:
        await first.write(WorkspaceWriteRequest(path, path.name.encode(), AnyCurrentState()))
    for path in reversed(paths):
        await second.write(WorkspaceWriteRequest(path, path.name.encode(), AnyCurrentState()))

    first_stats = await first.stats()
    second_stats = await second.stats()

    assert first_stats.root_hash == second_stats.root_hash
    assert first_stats.total_bytes == second_stats.total_bytes
    assert first_stats.node_count == second_stats.node_count
