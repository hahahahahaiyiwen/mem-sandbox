from __future__ import annotations

import pytest

from mem_sandbox.command_executor import (
    CommandEnvironment,
    CommandExecutionContext,
    CommandLimits,
    ExecuteRequest,
    ExecuteResult,
    create_default_executor,
)
from mem_sandbox.workspace import (
    MemoryWorkspace,
    SandboxPath,
    WorkspaceSnapshotData,
    WorkspaceStats,
)

SCENARIO = """mkdir -p /workspace/project
cd /workspace/project
echo "hello" > message.txt
cat message.txt
ls
rm message.txt"""


@pytest.mark.asyncio
async def test_first_command_wave_is_deterministic_from_identical_snapshots() -> None:
    original = MemoryWorkspace()
    snapshot = await original.export()
    results: list[tuple[ExecuteResult, WorkspaceStats, WorkspaceSnapshotData]] = []

    for _ in range(2):
        workspace = MemoryWorkspace()
        await workspace.restore(snapshot)
        result = await create_default_executor(workspace, workspace).execute(
            ExecuteRequest(
                SCENARIO.replace("\n", "; "),
                SandboxPath.root(),
                CommandEnvironment(),
                CommandLimits(),
            ),
            CommandExecutionContext(),
        )
        results.append((result, await workspace.stats(), await workspace.export()))

    first_result, second_result = results[0][0], results[1][0]
    assert first_result.exit_code == second_result.exit_code
    assert first_result.failure_code == second_result.failure_code
    assert first_result.stdout == second_result.stdout
    assert first_result.stderr == second_result.stderr
    assert first_result.resulting_cwd == second_result.resulting_cwd
    assert first_result.environment_changes == second_result.environment_changes
    assert results[0][1] == results[1][1]
    assert results[0][2].root_hash == results[1][2].root_hash
    assert results[0][0].stdout == "hello\nmessage.txt\n"
    assert results[0][0].resulting_cwd == SandboxPath.resolve("/workspace/project")
