from __future__ import annotations

import pytest

from mem_sandbox.command_executor import (
    CommandEnvironment,
    CommandExecutionContext,
    CommandLimits,
    EnvironmentValue,
    ExecuteRequest,
    ExecuteResult,
    create_default_executor,
)
from mem_sandbox.workspace import (
    AnyCurrentState,
    MemoryWorkspace,
    SandboxPath,
    WorkspaceWriteRequest,
)


async def execute(
    workspace: MemoryWorkspace,
    command: str,
    *,
    environment: CommandEnvironment | None = None,
) -> ExecuteResult:
    return await create_default_executor(workspace, workspace).execute(
        ExecuteRequest(
            command,
            SandboxPath.root(),
            environment or CommandEnvironment(),
            CommandLimits(),
        ),
        CommandExecutionContext(),
    )


async def write_text(workspace: MemoryWorkspace, path: str, content: str) -> None:
    await workspace.write(
        WorkspaceWriteRequest(
            SandboxPath.resolve(path),
            content.encode(),
            AnyCurrentState(),
            create_parents=True,
        )
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
        ("ls -l", 2),
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


@pytest.mark.asyncio
async def test_posix_shaped_cat_echo_ls_and_pipeline_stdin() -> None:
    workspace = MemoryWorkspace()
    await write_text(workspace, "/workspace/a", "alpha")
    await write_text(workspace, "/workspace/b", "beta")

    result = await execute(
        workspace,
        "echo -n piped | cat -; cat a - b; ls -1a a .",
    )

    assert result.exit_code == 0
    assert result.stdout == ("pipedalphabetaa\n\n.:\na\nb\n")


@pytest.mark.asyncio
async def test_text_processing_commands_preserve_lf_and_utf8_byte_semantics() -> None:
    workspace = MemoryWorkspace()
    await write_text(workspace, "/workspace/data", "beta\nalpha\nbeta")

    result = await execute(
        workspace,
        "head -n 2 data; tail -n 1 data; grep -n beta data; wc -lwc data; sort data | uniq -c",
    )

    assert result.exit_code == 0
    assert result.stdout == (
        "beta\nalpha\nbeta1:beta\n3:beta\n2 3 15 data\n      1 alpha\n      2 beta\n"
    )


@pytest.mark.asyncio
async def test_wc_uses_canonical_column_order_and_numeric_sort_unique_uses_sort_key() -> None:
    workspace = MemoryWorkspace()
    await write_text(workspace, "/workspace/data", "1\n1.0\n2\n")

    result = await execute(workspace, "wc -cl data; sort -nu data")

    assert result.exit_code == 0
    assert result.stdout == "3 8 data\n1\n2\n"


@pytest.mark.asyncio
async def test_new_output_commands_support_standalone_nonzero_redirection() -> None:
    workspace = MemoryWorkspace()
    await write_text(workspace, "/workspace/data", "present\n")

    result = await execute(workspace, "grep absent data > matches")

    assert result.exit_code == 1
    assert result.failure_code is not None
    assert result.failure_code.value == "no_match"
    assert (await workspace.read_text(SandboxPath.resolve("/workspace/matches"))).content == ""


@pytest.mark.asyncio
async def test_grep_no_match_find_and_recursive_search_are_deterministic() -> None:
    workspace = MemoryWorkspace()
    await write_text(workspace, "/workspace/src/a.txt", "needle\n")
    await write_text(workspace, "/workspace/src/nested/b.py", "other\nneedle")

    result = await execute(
        workspace,
        "find src -type f -name '*.py'; grep -rn needle src; grep absent src/a.txt",
    )

    assert result.exit_code == 1
    assert result.failure_code is not None
    assert result.failure_code.value == "no_match"
    assert result.stdout == ("src/nested/b.py\nsrc/a.txt:1:needle\nsrc/nested/b.py:2:needle\n")


@pytest.mark.asyncio
async def test_copy_move_multi_source_and_file_overwrite() -> None:
    workspace = MemoryWorkspace()
    await execute(workspace, "mkdir target")
    await write_text(workspace, "/workspace/a", "first")
    await write_text(workspace, "/workspace/b", "second")
    await write_text(workspace, "/workspace/replaced", "old")

    result = await execute(
        workspace,
        "cp a replaced; cp a b target; mv target/a moved; cat replaced target/b moved",
    )

    assert result.exit_code == 0
    assert result.stdout == "firstsecondfirst"


@pytest.mark.asyncio
async def test_env_export_and_unset_use_explicit_changes_and_derived_pwd() -> None:
    workspace = MemoryWorkspace()
    result = await execute(
        workspace,
        "export B=two A=one; env; unset A; env",
        environment=CommandEnvironment((EnvironmentValue("Z", "last"),)),
    )

    assert result.exit_code == 0
    assert result.stdout == (
        "A=one\nB=two\nPWD=/workspace\nZ=last\nB=two\nPWD=/workspace\nZ=last\n"
    )
    assert [(change.name, change.value) for change in result.environment_changes] == [
        ("B", "two"),
        ("A", "one"),
        ("A", None),
    ]
