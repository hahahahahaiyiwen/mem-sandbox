from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from mem_sandbox.workspace import (
    AnyCurrentState,
    FileEncodingError,
    InvalidPathError,
    InvalidRangeError,
    MakeDirectoryRequest,
    MemoryWorkspace,
    NodeKind,
    NotADirectoryError,
    NotAFileError,
    PathNotFoundError,
    ReadLimitExceededError,
    SandboxPath,
    WorkspaceLimits,
    WorkspaceRangeRequest,
    WorkspaceWriteRequest,
)


@pytest.mark.asyncio
async def test_empty_workspace_has_one_root_node_at_revision_zero() -> None:
    workspace = MemoryWorkspace()

    stats = await workspace.stats()
    root = await workspace.stat(SandboxPath.root())

    assert stats.total_bytes == 0
    assert stats.node_count == 1
    assert stats.revision.value == 0
    assert root.kind is NodeKind.DIRECTORY
    assert root.size_bytes == 0
    assert root.content_hash == stats.root_hash
    assert root.revision == stats.revision
    assert await workspace.list(SandboxPath.root()) == ()


@pytest.mark.asyncio
async def test_listing_is_lexical_and_metadata_views_are_immutable() -> None:
    workspace = MemoryWorkspace()
    root = SandboxPath.root()

    await workspace.write(
        WorkspaceWriteRequest(
            SandboxPath.resolve("/workspace/z.txt"),
            b"z",
            AnyCurrentState(),
        )
    )
    await workspace.mkdir(MakeDirectoryRequest(SandboxPath.resolve("/workspace/a")))
    await workspace.write(
        WorkspaceWriteRequest(
            SandboxPath.resolve("/workspace/m.txt"),
            b"middle",
            AnyCurrentState(),
        )
    )

    entries = await workspace.list(root)

    assert [entry.path.name for entry in entries] == ["a", "m.txt", "z.txt"]
    assert [entry.kind for entry in entries] == [
        NodeKind.DIRECTORY,
        NodeKind.FILE,
        NodeKind.FILE,
    ]
    assert all(entry.revision.value == 3 for entry in entries)

    with pytest.raises(FrozenInstanceError):
        entries[0].size_bytes = 10  # type: ignore[misc]


@pytest.mark.asyncio
async def test_binary_and_utf8_reads_have_explicit_encoding_behavior() -> None:
    workspace = MemoryWorkspace()
    path = SandboxPath.resolve("/workspace/data.bin")
    content = b"\x00valid\xff"

    mutation = await workspace.write(WorkspaceWriteRequest(path, content, AnyCurrentState()))
    binary = await workspace.read_bytes(path)

    assert binary.content == content
    assert binary.content_hash == mutation.current_hash
    assert binary.revision == mutation.stats.revision

    with pytest.raises(FileEncodingError):
        await workspace.read_text(path)


@pytest.mark.asyncio
async def test_range_reads_are_one_based_inclusive_and_normalize_line_endings() -> None:
    workspace = MemoryWorkspace()
    path = SandboxPath.resolve("/workspace/lines.txt")
    await workspace.write(
        WorkspaceWriteRequest(path, b"one\r\ntwo\nthree\rfour", AnyCurrentState())
    )

    result = await workspace.read_range(WorkspaceRangeRequest(path=path, start_line=2, end_line=3))
    clamped = await workspace.read_range(
        WorkspaceRangeRequest(path=path, start_line=4, end_line=99)
    )

    assert result.content == "two\nthree"
    assert result.start_line == 2
    assert result.end_line == 3
    assert result.total_lines == 4
    assert clamped.content == "four"
    assert clamped.end_line == 4


@pytest.mark.asyncio
async def test_range_reads_do_not_treat_other_unicode_separators_as_newlines() -> None:
    workspace = MemoryWorkspace()
    path = SandboxPath.resolve("/workspace/lines.txt")
    await workspace.write(
        WorkspaceWriteRequest(
            path,
            "line1\fline2\nline3\u2028still-line3".encode(),
            AnyCurrentState(),
        )
    )

    result = await workspace.read_range(WorkspaceRangeRequest(path))

    assert result.total_lines == 2
    assert result.content == "line1\fline2\nline3\u2028still-line3"


@pytest.mark.asyncio
async def test_empty_file_has_an_empty_range() -> None:
    workspace = MemoryWorkspace()
    path = SandboxPath.resolve("/workspace/empty.txt")
    await workspace.write(WorkspaceWriteRequest(path, b"", AnyCurrentState()))

    result = await workspace.read_range(WorkspaceRangeRequest(path))

    assert result.content == ""
    assert result.start_line == 1
    assert result.end_line == 0
    assert result.total_lines == 0


@pytest.mark.asyncio
async def test_range_limits_and_out_of_bounds_start_are_explicit() -> None:
    workspace = MemoryWorkspace(
        WorkspaceLimits(
            max_file_bytes=100,
            max_total_bytes=100,
            max_read_bytes=4,
            max_read_lines=2,
        )
    )
    path = SandboxPath.resolve("/workspace/lines.txt")
    await workspace.write(WorkspaceWriteRequest(path, b"aa\nbb\ncc", AnyCurrentState()))

    with pytest.raises(ReadLimitExceededError, match="lines"):
        await workspace.read_range(WorkspaceRangeRequest(path, 1, 3))

    with pytest.raises(ReadLimitExceededError, match="bytes"):
        await workspace.read_range(WorkspaceRangeRequest(path, 1, 2))

    with pytest.raises(InvalidRangeError, match="outside"):
        await workspace.read_range(WorkspaceRangeRequest(path, 4))

    assert (await workspace.read_bytes(path)).content == b"aa\nbb\ncc"


@pytest.mark.asyncio
async def test_read_errors_distinguish_missing_files_and_wrong_node_types() -> None:
    workspace = MemoryWorkspace()
    directory = SandboxPath.resolve("/workspace/dir")
    await workspace.mkdir(MakeDirectoryRequest(directory))

    with pytest.raises(PathNotFoundError):
        await workspace.read_bytes(SandboxPath.resolve("/workspace/missing"))

    with pytest.raises(NotAFileError):
        await workspace.read_bytes(directory)

    with pytest.raises(NotADirectoryError):
        path = SandboxPath.resolve("/workspace/file")
        await workspace.write(WorkspaceWriteRequest(path, b"x", AnyCurrentState()))
        await workspace.list(path)


@pytest.mark.asyncio
async def test_workspace_revalidates_preconstructed_paths_against_its_limits() -> None:
    workspace = MemoryWorkspace(WorkspaceLimits(max_path_bytes=14, max_segment_bytes=9))
    path = SandboxPath.resolve("/workspace/abcd")

    with pytest.raises(InvalidPathError, match="path"):
        await workspace.stat(path)
