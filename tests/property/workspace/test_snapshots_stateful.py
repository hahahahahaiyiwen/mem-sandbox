from __future__ import annotations

from hypothesis import given
from tests.property.async_machine import AsyncRunner
from tests.property.strategies import binary_payloads, workspace_file_maps

from mem_sandbox.workspace import (
    AnyCurrentState,
    MemoryWorkspace,
    SandboxPath,
    WorkspaceWriteRequest,
)


async def _populate(workspace: MemoryWorkspace, files: dict[str, bytes]) -> None:
    for name, content in files.items():
        await workspace.write(
            WorkspaceWriteRequest(
                SandboxPath.resolve(f"/workspace/{name}"),
                content,
                AnyCurrentState(),
            )
        )


@given(files=workspace_file_maps(), mutation=binary_payloads(max_size=8))
def test_workspace_exports_are_canonical_and_restores_are_independent(
    files: dict[str, bytes],
    mutation: bytes,
) -> None:
    async def exercise() -> None:
        source = MemoryWorkspace()
        twin = MemoryWorkspace()
        await _populate(source, files)
        await _populate(twin, dict(reversed(tuple(files.items()))))

        checkpoint = await source.export()
        repeated = await source.export()
        twin_checkpoint = await twin.export()
        checkpoint_bytes = checkpoint.encoded

        assert repeated == checkpoint
        assert twin_checkpoint == checkpoint

        restored = MemoryWorkspace()
        await restored.restore(checkpoint)
        assert await restored.stats() == await source.stats()
        for name, content in files.items():
            path = SandboxPath.resolve(f"/workspace/{name}")
            assert (await restored.read_bytes(path)).content == content

        source_only = SandboxPath.resolve("/workspace/__source_only__")
        restored_only = SandboxPath.resolve("/workspace/__restored_only__")
        await source.write(WorkspaceWriteRequest(source_only, mutation, AnyCurrentState()))
        await restored.write(WorkspaceWriteRequest(restored_only, mutation, AnyCurrentState()))

        assert [entry.path.name for entry in await source.list(SandboxPath.root())] == sorted(
            (*files, "__source_only__")
        )
        assert [entry.path.name for entry in await restored.list(SandboxPath.root())] == sorted(
            (*files, "__restored_only__")
        )
        assert checkpoint.encoded == checkpoint_bytes

        await restored.restore(checkpoint)
        assert await restored.export() == checkpoint

    with AsyncRunner() as runner:
        runner.run(exercise())
