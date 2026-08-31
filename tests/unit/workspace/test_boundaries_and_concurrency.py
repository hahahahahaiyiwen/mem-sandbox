from __future__ import annotations

import asyncio
import random
from typing import cast

import pytest

from mem_sandbox.workspace import (
    AnyCurrentState,
    InvalidPatchError,
    InvalidPathError,
    MemoryWorkspace,
    NodeLimitExceededError,
    PathNotFoundError,
    RemovePathRequest,
    SandboxPath,
    WorkspaceLimits,
    WorkspacePatchRequest,
    WorkspaceWriteRequest,
)


def test_path_and_segment_limits_accept_exact_boundaries_and_reject_one_above() -> None:
    path_limited = MemoryWorkspace(WorkspaceLimits(max_path_bytes=12, max_segment_bytes=9))
    assert str(path_limited.resolve_path("/workspace/a")) == "/workspace/a"
    with pytest.raises(InvalidPathError, match="path"):
        path_limited.resolve_path("/workspace/ab")

    segment_limited = MemoryWorkspace(WorkspaceLimits(max_path_bytes=100, max_segment_bytes=9))
    assert segment_limited.resolve_path("/workspace/123456789").name == "123456789"
    with pytest.raises(InvalidPathError, match="segment"):
        segment_limited.resolve_path("/workspace/1234567890")


@pytest.mark.asyncio
async def test_zero_byte_and_exact_node_boundaries_are_supported() -> None:
    workspace = MemoryWorkspace(WorkspaceLimits(max_file_bytes=1, max_total_bytes=1, max_nodes=2))

    result = await workspace.write(
        WorkspaceWriteRequest(
            SandboxPath.resolve("/workspace/empty"),
            b"",
            AnyCurrentState(),
        )
    )

    assert result.stats.total_bytes == 0
    assert result.stats.node_count == 2
    with pytest.raises(NodeLimitExceededError):
        await workspace.write(
            WorkspaceWriteRequest(
                SandboxPath.resolve("/workspace/another"),
                b"",
                AnyCurrentState(),
            )
        )


@pytest.mark.asyncio
async def test_patch_limit_accepts_exact_bytes_and_rejects_one_above() -> None:
    patch = """--- a/file
+++ b/file
@@ -1 +1 @@
-old
+new
"""
    patch_bytes = len(patch.encode())
    path = SandboxPath.resolve("/workspace/file")
    exact = MemoryWorkspace(
        WorkspaceLimits(
            max_file_bytes=10,
            max_total_bytes=10,
            max_patch_bytes=patch_bytes,
        )
    )
    await exact.write(WorkspaceWriteRequest(path, b"old", AnyCurrentState()))

    await exact.patch(WorkspacePatchRequest(patch))

    below = MemoryWorkspace(
        WorkspaceLimits(
            max_file_bytes=10,
            max_total_bytes=10,
            max_patch_bytes=patch_bytes - 1,
        )
    )
    await below.write(WorkspaceWriteRequest(path, b"old", AnyCurrentState()))
    with pytest.raises(InvalidPatchError, match="limit"):
        await below.patch(WorkspacePatchRequest(patch))


@pytest.mark.asyncio
async def test_cancelled_lock_waiter_never_mutates_workspace() -> None:
    workspace = MemoryWorkspace()
    lock = cast(asyncio.Lock, object.__getattribute__(workspace, "_state_lock"))
    await lock.acquire()
    task = asyncio.create_task(
        workspace.write(
            WorkspaceWriteRequest(
                SandboxPath.resolve("/workspace/file"),
                b"content",
                AnyCurrentState(),
            )
        )
    )
    await asyncio.sleep(0)
    task.cancel()

    try:
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        lock.release()

    assert (await workspace.stats()).revision.value == 0
    with pytest.raises(PathNotFoundError):
        await workspace.stat(SandboxPath.resolve("/workspace/file"))


@pytest.mark.asyncio
async def test_deterministic_random_operation_sequences_produce_identical_states() -> None:
    first = MemoryWorkspace()
    second = MemoryWorkspace()
    generator = random.Random(4242)
    paths = [SandboxPath.resolve(f"/workspace/file-{index}.txt") for index in range(8)]

    for _ in range(100):
        path = generator.choice(paths)
        if generator.random() < 0.7:
            content = generator.randbytes(generator.randrange(0, 32))
            request = WorkspaceWriteRequest(path, content, AnyCurrentState())
            await first.write(request)
            await second.write(request)
        else:
            request = RemovePathRequest(path, missing_ok=True)
            await first.remove(request)
            await second.remove(request)

        assert await first.stats() == await second.stats()

    first_entries = await first.list(SandboxPath.root())
    second_entries = await second.list(SandboxPath.root())
    assert first_entries == second_entries
    for entry in first_entries:
        assert await first.read_bytes(entry.path) == await second.read_bytes(entry.path)
