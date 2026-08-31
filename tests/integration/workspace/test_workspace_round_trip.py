from __future__ import annotations

import pytest

from mem_sandbox.workspace import (
    AnyCurrentState,
    CopyPathRequest,
    ExpectedFileHash,
    MakeDirectoryRequest,
    MemoryWorkspace,
    SandboxPath,
    WorkspacePatchRequest,
    WorkspaceRangeRequest,
    WorkspaceWriteRequest,
)


@pytest.mark.asyncio
async def test_workspace_round_trip_acceptance_scenario() -> None:
    workspace = MemoryWorkspace()
    source = SandboxPath.resolve("/workspace/src")
    app = source.join("app.py")
    readme = SandboxPath.resolve("/workspace/README.md")

    await workspace.mkdir(MakeDirectoryRequest(source))
    app_write = await workspace.write(
        WorkspaceWriteRequest(
            app,
            b"def greet(name):\n    return f'Hello, {name}'\n",
            AnyCurrentState(),
        )
    )
    await workspace.write(
        WorkspaceWriteRequest(
            readme,
            b"# MemSandbox\n\nIn-memory agent workspace.\n",
            AnyCurrentState(),
        )
    )

    listing = await workspace.list(SandboxPath.root())
    assert [entry.path.name for entry in listing] == ["README.md", "src"]

    lines = await workspace.read_range(WorkspaceRangeRequest(app, 1, 2))
    assert lines.content == "def greet(name):\n    return f'Hello, {name}'"

    assert app_write.current_hash is not None
    patch = """--- a/src/app.py
+++ b/src/app.py
@@ -1,2 +1,2 @@
 def greet(name):
-    return f'Hello, {name}'
+    return f'Hello, {name}!'
"""
    await workspace.patch(
        WorkspacePatchRequest(
            patch,
            (ExpectedFileHash(app, app_write.current_hash),),
        )
    )

    await workspace.copy(CopyPathRequest(source, SandboxPath.resolve("/workspace/backup")))
    snapshot = await workspace.export()
    expected_stats = await workspace.stats()
    expected_app = await workspace.read_bytes(app)
    expected_backup = await workspace.read_bytes(SandboxPath.resolve("/workspace/backup/app.py"))

    await workspace.write(WorkspaceWriteRequest(app, b"mutated", AnyCurrentState()))
    await workspace.write(
        WorkspaceWriteRequest(
            SandboxPath.resolve("/workspace/transient.txt"),
            b"transient",
            AnyCurrentState(),
        )
    )

    await workspace.restore(snapshot)

    assert await workspace.stats() == expected_stats
    assert (await workspace.read_bytes(app)).content == expected_app.content
    assert (
        await workspace.read_bytes(SandboxPath.resolve("/workspace/backup/app.py"))
    ).content == expected_backup.content
