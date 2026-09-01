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
from mem_sandbox.workspace import MemoryWorkspace, SandboxPath


async def execute(workspace: MemoryWorkspace, command: str) -> ExecuteResult:
    return await create_default_executor(workspace, workspace).execute(
        ExecuteRequest(command, SandboxPath.root(), CommandEnvironment(), CommandLimits()),
        CommandExecutionContext(),
    )


@pytest.mark.asyncio
async def test_first_wave_commands_and_fail_fast_mutations() -> None:
    workspace = MemoryWorkspace()

    result = await execute(
        workspace,
        "pwd; mkdir -p a/b c; touch a/b/one a/b/two; ls a/b; "
        "echo hello world; cat a/b/one; rm -rf a missing; ls",
    )

    assert result.exit_code == 0
    assert result.failure_code is None
    assert result.stdout == "/workspace\none\ntwo\nhello world\nc\n"
    assert result.resulting_cwd == SandboxPath.root()
    assert [entry.path.name for entry in await workspace.list(SandboxPath.root())] == ["c"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "exit_code"),
    (
        ("pwd extra", 2),
        ("cd", 2),
        ("cd -x", 2),
        ("ls a b", 2),
        ("ls -l", 2),
        ("cat", 2),
        ("cat -n file", 2),
        ("mkdir", 2),
        ("mkdir -x a", 2),
        ("touch", 2),
        ("rm", 2),
        ("rm -z a", 2),
        ("unknown", 127),
    ),
)
async def test_invalid_arguments_and_unknown_commands(command: str, exit_code: int) -> None:
    result = await execute(MemoryWorkspace(), command)
    assert result.exit_code == exit_code
    if exit_code == 2:
        assert result.failure_code is not None
        assert result.failure_code.value == "invalid_argument"


@pytest.mark.asyncio
async def test_semicolon_continues_and_and_short_circuits() -> None:
    workspace = MemoryWorkspace()
    result = await execute(
        workspace,
        "cat missing && touch skipped; touch kept; cat missing; echo final",
    )

    assert result.exit_code == 0
    assert result.stdout == "final\n"
    assert [entry.path.name for entry in await workspace.list(SandboxPath.root())] == ["kept"]


@pytest.mark.asyncio
async def test_multi_target_commands_fail_fast_after_committing_earlier_targets() -> None:
    mkdir_workspace = MemoryWorkspace()
    mkdir_result = await execute(
        mkdir_workspace,
        "mkdir one missing/two three",
    )
    assert mkdir_result.exit_code == 1
    assert [entry.path.name for entry in await mkdir_workspace.list(SandboxPath.root())] == ["one"]

    touch_workspace = MemoryWorkspace()
    touch_result = await execute(
        touch_workspace,
        "touch one missing/two three",
    )
    assert touch_result.exit_code == 1
    assert [entry.path.name for entry in await touch_workspace.list(SandboxPath.root())] == ["one"]

    rm_workspace = MemoryWorkspace()
    await execute(rm_workspace, "touch one two")
    rm_result = await execute(rm_workspace, "rm one missing two")
    assert rm_result.exit_code == 1
    assert [entry.path.name for entry in await rm_workspace.list(SandboxPath.root())] == ["two"]
