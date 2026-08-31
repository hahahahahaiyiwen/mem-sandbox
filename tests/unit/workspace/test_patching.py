from __future__ import annotations

import pytest

from mem_sandbox.workspace import (
    AnyCurrentState,
    ExpectedFileHash,
    FileEncodingError,
    FileSizeLimitExceededError,
    InvalidPatchError,
    MemoryWorkspace,
    PatchContextMismatchError,
    SandboxPath,
    StaleContentError,
    WorkspaceLimits,
    WorkspacePatchRequest,
    WorkspaceWriteRequest,
)


@pytest.mark.asyncio
async def test_unified_diff_adds_removes_and_replaces_across_hunks() -> None:
    workspace = MemoryWorkspace()
    path = SandboxPath.resolve("/workspace/file.txt")
    created = await workspace.write(
        WorkspaceWriteRequest(path, b"A\r\nB\r\nC\r\nD\r\nE", AnyCurrentState())
    )
    assert created.current_hash is not None
    patch = """--- a/file.txt
+++ b/file.txt
@@ -1,2 +1,3 @@
 A
+A2
 B
@@ -4,2 +5,2 @@
 D
-E
+E2
"""

    result = await workspace.patch(
        WorkspacePatchRequest(
            patch,
            (ExpectedFileHash(path, created.current_hash),),
        )
    )

    assert (await workspace.read_text(path)).content == "A\nA2\nB\nC\nD\nE2"
    assert result.stats.revision.value == 2
    assert len(result.files) == 1
    assert result.files[0].previous_hash == created.current_hash


@pytest.mark.asyncio
async def test_multi_file_patch_commits_once() -> None:
    workspace = MemoryWorkspace()
    first = SandboxPath.resolve("/workspace/first.txt")
    second = SandboxPath.resolve("/workspace/second.txt")
    first_write = await workspace.write(
        WorkspaceWriteRequest(first, b"old first", AnyCurrentState())
    )
    second_write = await workspace.write(
        WorkspaceWriteRequest(second, b"old second", AnyCurrentState())
    )
    assert first_write.current_hash is not None
    assert second_write.current_hash is not None
    patch = """--- a/first.txt
+++ b/first.txt
@@ -1 +1 @@
-old first
+new first
--- a/second.txt
+++ b/second.txt
@@ -1 +1 @@
-old second
+new second
"""

    result = await workspace.patch(
        WorkspacePatchRequest(
            patch,
            (
                ExpectedFileHash(first, first_write.current_hash),
                ExpectedFileHash(second, second_write.current_hash),
            ),
        )
    )

    assert result.stats.revision.value == 3
    assert [item.path for item in result.files] == [first, second]
    assert (await workspace.read_text(first)).content == "new first"
    assert (await workspace.read_text(second)).content == "new second"


@pytest.mark.asyncio
async def test_patch_can_insert_the_first_line_into_an_empty_existing_file() -> None:
    workspace = MemoryWorkspace()
    path = SandboxPath.resolve("/workspace/empty.txt")
    await workspace.write(WorkspaceWriteRequest(path, b"", AnyCurrentState()))
    patch = """--- a/empty.txt
+++ b/empty.txt
@@ -0,0 +1 @@
+first
"""

    await workspace.patch(WorkspacePatchRequest(patch))

    assert (await workspace.read_text(path)).content == "first"


@pytest.mark.asyncio
async def test_stale_hash_rejects_patch_without_mutation() -> None:
    workspace = MemoryWorkspace()
    path = SandboxPath.resolve("/workspace/file.txt")
    original = await workspace.write(WorkspaceWriteRequest(path, b"old", AnyCurrentState()))
    assert original.current_hash is not None
    await workspace.write(WorkspaceWriteRequest(path, b"newer", AnyCurrentState()))
    before = await workspace.stats()
    patch = """--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+replacement
"""

    with pytest.raises(StaleContentError):
        await workspace.patch(
            WorkspacePatchRequest(
                patch,
                (ExpectedFileHash(path, original.current_hash),),
            )
        )

    assert await workspace.stats() == before
    assert (await workspace.read_text(path)).content == "newer"


@pytest.mark.asyncio
async def test_context_mismatch_makes_multi_file_patch_atomic() -> None:
    workspace = MemoryWorkspace()
    first = SandboxPath.resolve("/workspace/first.txt")
    second = SandboxPath.resolve("/workspace/second.txt")
    await workspace.write(WorkspaceWriteRequest(first, b"first", AnyCurrentState()))
    await workspace.write(WorkspaceWriteRequest(second, b"actual", AnyCurrentState()))
    before = await workspace.stats()
    patch = """--- a/first.txt
+++ b/first.txt
@@ -1 +1 @@
-first
+changed
--- a/second.txt
+++ b/second.txt
@@ -1 +1 @@
-expected
+changed
"""

    with pytest.raises(PatchContextMismatchError):
        await workspace.patch(WorkspacePatchRequest(patch))

    assert await workspace.stats() == before
    assert (await workspace.read_text(first)).content == "first"
    assert (await workspace.read_text(second)).content == "actual"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "patch",
    (
        "",
        "@@ invalid @@\n line",
        "--- a/file.txt\n+++ b/other.txt\n@@ -1 +1 @@\n-old\n+new",
        "--- /dev/null\n+++ b/file.txt\n@@ -0,0 +1 @@\n+new",
        "--- a/file.txt\n+++ b/file.txt\n@@ -1 +1 @@\n?invalid",
        "--- a/../escape.txt\n+++ b/../escape.txt\n@@ -1 +1 @@\n-old\n+new",
    ),
)
async def test_malformed_or_unsupported_patches_are_rejected(patch: str) -> None:
    workspace = MemoryWorkspace()

    with pytest.raises(InvalidPatchError):
        await workspace.patch(WorkspacePatchRequest(patch))


@pytest.mark.asyncio
async def test_patch_limits_and_file_size_failures_preserve_state() -> None:
    path = SandboxPath.resolve("/workspace/file.txt")
    workspace = MemoryWorkspace(
        WorkspaceLimits(
            max_file_bytes=4,
            max_total_bytes=4,
            max_patch_bytes=100,
        )
    )
    await workspace.write(WorkspaceWriteRequest(path, b"old", AnyCurrentState()))
    before = await workspace.stats()
    oversized_result = """--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+12345
"""

    with pytest.raises(FileSizeLimitExceededError):
        await workspace.patch(WorkspacePatchRequest(oversized_result))

    with pytest.raises(InvalidPatchError, match="limit"):
        await MemoryWorkspace(
            WorkspaceLimits(
                max_file_bytes=100,
                max_total_bytes=100,
                max_patch_bytes=10,
            )
        ).patch(WorkspacePatchRequest(oversized_result))

    assert await workspace.stats() == before
    assert (await workspace.read_text(path)).content == "old"


@pytest.mark.asyncio
async def test_patch_rejects_non_utf8_files() -> None:
    workspace = MemoryWorkspace()
    path = SandboxPath.resolve("/workspace/file.txt")
    await workspace.write(WorkspaceWriteRequest(path, b"\xff", AnyCurrentState()))
    patch = """--- a/file.txt
+++ b/file.txt
@@ -1 +1 @@
-old
+new
"""

    with pytest.raises(FileEncodingError):
        await workspace.patch(WorkspacePatchRequest(patch))
