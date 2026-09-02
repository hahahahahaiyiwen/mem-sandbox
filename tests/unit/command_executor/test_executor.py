from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from mem_sandbox.command_executor import (
    CommandCancelled,
    CommandContext,
    CommandDescriptor,
    CommandEnvironment,
    CommandExecutionContext,
    CommandFailureCode,
    CommandLimits,
    CommandRegistry,
    CommandRequest,
    CommandResult,
    CommandSyntaxInvalid,
    CommandTimeout,
    CommandTooLong,
    EnvironmentChange,
    EnvironmentValue,
    ExecuteRequest,
    VirtualCommandExecutor,
    create_default_executor,
    create_first_wave_commands,
)
from mem_sandbox.core.errors import InternalSandboxError
from mem_sandbox.workspace import (
    AnyCurrentState,
    MemoryWorkspace,
    PathNotFoundError,
    SandboxPath,
    WorkspaceAppendRequest,
    WorkspaceEntry,
    WorkspaceLimits,
    WorkspaceMutation,
    WorkspaceWriteRequest,
)


def request(command: str, *, limits: CommandLimits | None = None) -> ExecuteRequest:
    return ExecuteRequest(
        command=command,
        cwd=SandboxPath.root(),
        environment=CommandEnvironment((EnvironmentValue("NAME", "world"),)),
        limits=limits or CommandLimits(),
    )


def test_execution_models_are_immutable_and_limits_are_validated() -> None:
    value = request("pwd")
    with pytest.raises(FrozenInstanceError):
        value.command = "echo changed"  # type: ignore[misc]
    with pytest.raises(ValueError):
        CommandLimits(max_stdout_bytes=0)
    with pytest.raises(ValueError):
        CommandLimits(timeout_seconds=float("inf"))
    with pytest.raises(ValueError, match="UTF-8"):
        EnvironmentValue("BAD", "\ud800")
    with pytest.raises(ValueError, match="UTF-8"):
        EnvironmentChange("BAD", "\ud800")
    with pytest.raises(ValueError, match="UTF-8"):
        CommandResult.success(stdout="\ud800")
    command_request = CommandRequest(("cat",), "", stdin_connected=True)
    assert command_request.stdin_connected


@pytest.mark.asyncio
async def test_quotes_empty_arguments_expansion_and_sequencing() -> None:
    workspace = MemoryWorkspace()
    executor = create_default_executor(workspace, workspace)

    result = await executor.execute(
        request("""echo '' "$NAME" '$NAME'; cd /workspace && echo "$PWD"; missing && echo no"""),
        CommandExecutionContext(),
    )

    assert result.exit_code == 127
    assert result.failure_code is CommandFailureCode.COMMAND_NOT_FOUND
    assert result.stdout == " world $NAME\n/workspace\n"
    assert result.resulting_cwd == SandboxPath.root()
    assert result.environment_changes == ()


@pytest.mark.asyncio
async def test_double_quoted_backslashes_only_escape_supported_characters() -> None:
    workspace = MemoryWorkspace()
    executor = create_default_executor(workspace, workspace)

    result = await executor.execute(
        request(r'''echo "a\nb" "c\$d" "e\"f" "g\\h" "i\;j"'''),
        CommandExecutionContext(),
    )

    assert result.stdout == 'a\\nb c$d e"f g\\h i\\;j\n'


@pytest.mark.asyncio
async def test_complete_pipeline_is_preflighted_before_any_dispatch() -> None:
    workspace = MemoryWorkspace()
    dispatched = False

    class SourceCommand:
        @property
        def descriptor(self) -> CommandDescriptor:
            return CommandDescriptor("source", (), "source", "source", True, False, True)

        async def execute(
            self,
            request: CommandRequest,
            context: CommandContext,
        ) -> CommandResult:
            nonlocal dispatched
            dispatched = True
            return CommandResult.success(stdout="content\n")

    result = await VirtualCommandExecutor(
        CommandRegistry((SourceCommand(),)),
        workspace,
    ).execute(
        request("source | missing"),
        CommandExecutionContext(),
    )

    assert result.exit_code == 127
    assert result.failure_code is CommandFailureCode.COMMAND_NOT_FOUND
    assert not dispatched


@pytest.mark.asyncio
async def test_invalid_environment_name_is_rejected_before_any_mutation() -> None:
    workspace = MemoryWorkspace()
    executor = create_default_executor(workspace, workspace)

    with pytest.raises(CommandSyntaxInvalid):
        await executor.execute(
            request("touch created; echo $é"),
            CommandExecutionContext(),
        )

    assert (await workspace.stats()).node_count == 1


@pytest.mark.asyncio
async def test_output_is_utf8_safely_truncated_without_changing_exit_code() -> None:
    workspace = MemoryWorkspace()
    executor = create_default_executor(workspace, workspace)

    result = await executor.execute(
        request("echo éé", limits=CommandLimits(max_stdout_bytes=3)),
        CommandExecutionContext(),
    )

    assert result.exit_code == 0
    assert result.stdout == "é"
    assert result.stdout_original_bytes == 5
    assert result.stdout_truncated
    assert not result.stderr_truncated


@pytest.mark.asyncio
async def test_output_does_not_backfill_after_partial_code_point_truncation() -> None:
    workspace = MemoryWorkspace()
    executor = create_default_executor(workspace, workspace)

    result = await executor.execute(
        request(
            "echo 123456789€; echo XYZ",
            limits=CommandLimits(max_stdout_bytes=10),
        ),
        CommandExecutionContext(),
    )

    assert result.stdout == "123456789"
    assert result.stdout_original_bytes == 17
    assert result.stdout_truncated


@pytest.mark.asyncio
async def test_command_argv_argument_and_stderr_limits_are_enforced() -> None:
    workspace = MemoryWorkspace()
    executor = create_default_executor(workspace, workspace)
    with pytest.raises(CommandTooLong):
        await executor.execute(
            request("echo", limits=CommandLimits(max_command_bytes=3)),
            CommandExecutionContext(),
        )
    with pytest.raises(CommandSyntaxInvalid):
        await executor.execute(
            request("echo a b", limits=CommandLimits(max_argv_entries=2)),
            CommandExecutionContext(),
        )
    result = await executor.execute(
        request("echo $NAME", limits=CommandLimits(max_argument_bytes=3)),
        CommandExecutionContext(),
    )
    assert result.exit_code == 2
    assert result.failure_code is CommandFailureCode.INVALID_ARGUMENT

    class ErrorCommand:
        @property
        def descriptor(self) -> CommandDescriptor:
            return CommandDescriptor("error", (), "error", "error", False, False)

        async def execute(
            self,
            request: CommandRequest,
            context: CommandContext,
        ) -> CommandResult:
            return CommandResult(1, CommandFailureCode.WORKSPACE_FAILURE, "", "éé")

    result = await VirtualCommandExecutor(
        CommandRegistry((ErrorCommand(),)),
        workspace,
    ).execute(
        request("error", limits=CommandLimits(max_stderr_bytes=3)),
        CommandExecutionContext(),
    )
    assert result.stderr == "é"
    assert result.stderr_original_bytes == 4
    assert result.stderr_truncated


@pytest.mark.asyncio
async def test_argv_count_is_validated_before_any_plan_mutation() -> None:
    workspace = MemoryWorkspace()
    executor = create_default_executor(workspace, workspace)

    with pytest.raises(CommandSyntaxInvalid):
        await executor.execute(
            request("touch created; echo a b", limits=CommandLimits(max_argv_entries=2)),
            CommandExecutionContext(),
        )

    assert (await workspace.stats()).node_count == 1


@pytest.mark.asyncio
async def test_expanded_argument_limit_is_a_structured_mid_plan_failure() -> None:
    workspace = MemoryWorkspace()
    executor = create_default_executor(workspace, workspace)

    result = await executor.execute(
        ExecuteRequest(
            command="touch ok; echo $NAME",
            cwd=SandboxPath.root(),
            environment=CommandEnvironment((EnvironmentValue("NAME", "longer"),)),
            limits=CommandLimits(max_argument_bytes=5),
        ),
        CommandExecutionContext(),
    )

    assert result.exit_code == 2
    assert result.failure_code is CommandFailureCode.INVALID_ARGUMENT
    await workspace.stat(SandboxPath.resolve("/workspace/ok"))


@pytest.mark.asyncio
async def test_redirection_replace_append_and_truncated_output_failure() -> None:
    workspace = MemoryWorkspace()
    executor = create_default_executor(workspace, workspace)

    result = await executor.execute(
        request("echo first > out; echo second >> out; cat out"),
        CommandExecutionContext(),
    )
    assert result.stdout == "first\nsecond\n"

    limited = await executor.execute(
        request(
            "echo éé > rejected",
            limits=CommandLimits(max_stdout_bytes=3),
        ),
        CommandExecutionContext(),
    )
    assert limited.exit_code == 1
    assert limited.failure_code is CommandFailureCode.REDIRECTION_FAILURE
    assert limited.stdout == ""
    assert "output limit" in limited.stderr


@pytest.mark.asyncio
async def test_redirection_applies_to_normal_nonzero_results_and_preserves_status() -> None:
    workspace = MemoryWorkspace()

    class FailingCommand:
        @property
        def descriptor(self) -> CommandDescriptor:
            return CommandDescriptor("fail", (), "fail", "fail", True, False)

        async def execute(
            self,
            request: CommandRequest,
            context: CommandContext,
        ) -> CommandResult:
            return CommandResult(
                1,
                CommandFailureCode.NO_MATCH,
                "partial\n",
                "diagnostic\n",
            )

    result = await VirtualCommandExecutor(
        CommandRegistry((FailingCommand(),)),
        workspace,
    ).execute(request("fail > result"), CommandExecutionContext())

    assert result.exit_code == 1
    assert result.failure_code is CommandFailureCode.NO_MATCH
    assert result.stdout == ""
    assert result.stderr == "diagnostic\n"
    assert (
        await workspace.read_text(SandboxPath.resolve("/workspace/result"))
    ).content == "partial\n"


@pytest.mark.asyncio
async def test_pipeline_connects_complete_stdout_and_uses_fixed_pipefail() -> None:
    workspace = MemoryWorkspace()
    seen: list[tuple[str, bool]] = []

    class StageCommand:
        def __init__(
            self,
            name: str,
            result: CommandResult,
            *,
            accepts_stdin: bool,
        ) -> None:
            self._descriptor = CommandDescriptor(
                name,
                (),
                name,
                name,
                True,
                accepts_stdin,
                True,
            )
            self._result = result

        @property
        def descriptor(self) -> CommandDescriptor:
            return self._descriptor

        async def execute(
            self,
            request: CommandRequest,
            context: CommandContext,
        ) -> CommandResult:
            seen.append((request.stdin, request.stdin_connected))
            return self._result

    executor = VirtualCommandExecutor(
        CommandRegistry(
            (
                StageCommand(
                    "first",
                    CommandResult(
                        3,
                        CommandFailureCode.WORKSPACE_FAILURE,
                        "",
                        "first error\n",
                    ),
                    accepts_stdin=False,
                ),
                StageCommand(
                    "second",
                    CommandResult(
                        2,
                        CommandFailureCode.INVALID_ARGUMENT,
                        "second output\n",
                        "second error\n",
                    ),
                    accepts_stdin=True,
                ),
                StageCommand(
                    "third",
                    CommandResult.success(stdout="final\n", stderr="third note\n"),
                    accepts_stdin=True,
                ),
            )
        ),
        workspace,
    )

    result = await executor.execute(
        request("first | second | third"),
        CommandExecutionContext(),
    )

    assert seen == [("", False), ("", True), ("second output\n", True)]
    assert result.exit_code == 2
    assert result.failure_code is CommandFailureCode.INVALID_ARGUMENT
    assert result.stdout == "final\n"
    assert result.stderr == "first error\nsecond error\nthird note\n"


@pytest.mark.asyncio
async def test_pipeline_limit_starts_no_following_stage_and_controls_connectors() -> None:
    workspace = MemoryWorkspace()
    downstream_calls = 0

    class SourceCommand:
        @property
        def descriptor(self) -> CommandDescriptor:
            return CommandDescriptor("source", (), "source", "source", True, False, True)

        async def execute(
            self,
            request: CommandRequest,
            context: CommandContext,
        ) -> CommandResult:
            return CommandResult.success(stdout="oversized")

    class SinkCommand:
        @property
        def descriptor(self) -> CommandDescriptor:
            return CommandDescriptor("sink", (), "sink", "sink", True, True, True)

        async def execute(
            self,
            request: CommandRequest,
            context: CommandContext,
        ) -> CommandResult:
            nonlocal downstream_calls
            downstream_calls += 1
            return CommandResult.success()

    executor = VirtualCommandExecutor(
        CommandRegistry(
            (
                SourceCommand(),
                SinkCommand(),
                *create_first_wave_commands(workspace, workspace),
            )
        ),
        workspace,
    )
    result = await executor.execute(
        request(
            "source | sink && touch skipped; touch followed",
            limits=CommandLimits(max_pipeline_intermediate_bytes=4),
        ),
        CommandExecutionContext(),
    )

    assert downstream_calls == 0
    assert result.exit_code == 0
    assert result.failure_code is None
    with pytest.raises(PathNotFoundError):
        await workspace.stat(SandboxPath.resolve("/workspace/skipped"))
    await workspace.stat(SandboxPath.resolve("/workspace/followed"))


@pytest.mark.asyncio
async def test_pipeline_admission_and_stage_limits_prevent_all_dispatch() -> None:
    workspace = MemoryWorkspace()
    executor = create_default_executor(workspace, workspace)

    unsafe = await executor.execute(
        request("touch created | cat"),
        CommandExecutionContext(),
    )
    assert unsafe.exit_code == 2
    assert unsafe.failure_code is CommandFailureCode.INVALID_ARGUMENT
    with pytest.raises(PathNotFoundError):
        await workspace.stat(SandboxPath.resolve("/workspace/created"))

    ineligible = await executor.execute(
        request("echo content | pwd"),
        CommandExecutionContext(),
    )
    assert ineligible.exit_code == 2
    assert ineligible.failure_code is CommandFailureCode.INVALID_ARGUMENT

    with pytest.raises(CommandSyntaxInvalid, match="stages"):
        await executor.execute(
            request(
                "echo content | cat | cat",
                limits=CommandLimits(max_pipeline_stages=2),
            ),
            CommandExecutionContext(),
        )


@pytest.mark.asyncio
async def test_pipeline_aggregate_or_admission_failure_does_not_redirect() -> None:
    workspace = MemoryWorkspace()
    executor = create_default_executor(workspace, workspace)

    aggregate = await executor.execute(
        request(
            "echo 123 | cat | cat > aggregate",
            limits=CommandLimits(
                max_pipeline_intermediate_bytes=10,
                max_pipeline_aggregate_bytes=7,
            ),
        ),
        CommandExecutionContext(),
    )
    assert aggregate.exit_code == 1
    assert aggregate.failure_code is CommandFailureCode.PIPELINE_LIMIT_EXCEEDED

    admission = await executor.execute(
        request("missing | touch created > admission"),
        CommandExecutionContext(),
    )
    assert admission.exit_code == 2
    assert admission.failure_code is CommandFailureCode.INVALID_ARGUMENT

    for path in ("aggregate", "admission", "created"):
        with pytest.raises(PathNotFoundError):
            await workspace.stat(SandboxPath.resolve(f"/workspace/{path}"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    (
        "missing | cat > existing",
        "cat source | missing > existing",
    ),
)
async def test_pipeline_command_not_found_still_applies_normal_127_redirection(
    command: str,
) -> None:
    workspace = MemoryWorkspace()
    await workspace.write(
        WorkspaceWriteRequest(
            SandboxPath.resolve("/workspace/source"),
            b"source\n",
            AnyCurrentState(),
        )
    )
    await workspace.write(
        WorkspaceWriteRequest(
            SandboxPath.resolve("/workspace/existing"),
            b"previous\n",
            AnyCurrentState(),
        )
    )

    result = await create_default_executor(workspace, workspace).execute(
        request(command),
        CommandExecutionContext(),
    )

    assert result.exit_code == 127
    assert result.failure_code is CommandFailureCode.COMMAND_NOT_FOUND
    assert (await workspace.read_text(SandboxPath.resolve("/workspace/existing"))).content == ""


@pytest.mark.asyncio
async def test_redirection_target_and_quota_failures_leave_workspace_unchanged() -> None:
    target_workspace = MemoryWorkspace()
    target_executor = create_default_executor(target_workspace, target_workspace)
    before_target = await target_workspace.stats()

    target_result = await target_executor.execute(
        request("echo content > /workspace"),
        CommandExecutionContext(),
    )

    assert target_result.exit_code == 1
    assert target_result.failure_code is CommandFailureCode.REDIRECTION_FAILURE
    assert await target_workspace.stats() == before_target

    quota_workspace = MemoryWorkspace(WorkspaceLimits(max_file_bytes=2, max_total_bytes=2))
    quota_executor = create_default_executor(quota_workspace, quota_workspace)
    before_quota = await quota_workspace.stats()

    quota_result = await quota_executor.execute(
        request("echo hi > file"),
        CommandExecutionContext(),
    )

    assert quota_result.exit_code == 1
    assert quota_result.failure_code is CommandFailureCode.REDIRECTION_FAILURE
    assert await quota_workspace.stats() == before_quota


@pytest.mark.asyncio
async def test_append_redirection_dispatches_one_atomic_append_mutation() -> None:
    class AppendSpyWorkspace(MemoryWorkspace):
        def __init__(self) -> None:
            super().__init__()
            self.append_calls = 0

        async def append(self, request: WorkspaceAppendRequest) -> WorkspaceMutation:
            self.append_calls += 1
            return await super().append(request)

        async def write(self, request: WorkspaceWriteRequest) -> WorkspaceMutation:
            raise AssertionError("append redirection must not use write")

    reader = MemoryWorkspace()
    mutator = AppendSpyWorkspace()
    result = await create_default_executor(reader, mutator).execute(
        request("echo content >> file"),
        CommandExecutionContext(),
    )

    assert result.exit_code == 0
    assert mutator.append_calls == 1
    assert (await mutator.read_text(SandboxPath.resolve("/workspace/file"))).content == "content\n"


@pytest.mark.asyncio
async def test_external_cancellation_and_complete_plan_timeout_are_typed() -> None:
    class SlowCommand:
        @property
        def descriptor(self) -> CommandDescriptor:
            return CommandDescriptor("slow", (), "slow", "slow", False, False)

        async def execute(
            self,
            request: CommandRequest,
            context: CommandContext,
        ) -> CommandResult:
            await asyncio.sleep(10)
            return CommandResult.success()

    workspace = MemoryWorkspace()
    executor = VirtualCommandExecutor(
        CommandRegistry((SlowCommand(),)),
        workspace,
    )
    with pytest.raises(CommandTimeout):
        await executor.execute(
            request("slow", limits=CommandLimits(timeout_seconds=0.01)),
            CommandExecutionContext(),
        )

    cancellation = asyncio.Event()
    cancellation.set()
    with pytest.raises(CommandCancelled):
        await create_default_executor(workspace, workspace).execute(
            request("touch never"),
            CommandExecutionContext(cancellation=cancellation),
        )
    assert (await workspace.stats()).node_count == 1


@pytest.mark.asyncio
async def test_native_asyncio_cancellation_propagates_without_translation() -> None:
    started = asyncio.Event()

    class BlockingCommand:
        @property
        def descriptor(self) -> CommandDescriptor:
            return CommandDescriptor("block", (), "block", "block", False, False)

        async def execute(
            self,
            request: CommandRequest,
            context: CommandContext,
        ) -> CommandResult:
            started.set()
            await asyncio.Event().wait()
            raise AssertionError

    workspace = MemoryWorkspace()
    executor = VirtualCommandExecutor(CommandRegistry((BlockingCommand(),)), workspace)
    task = asyncio.create_task(executor.execute(request("block"), CommandExecutionContext()))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()


@pytest.mark.asyncio
async def test_infrastructure_workspace_errors_are_not_normal_command_failures() -> None:
    class FailingListWorkspace(MemoryWorkspace):
        async def list(self, path: SandboxPath) -> tuple[WorkspaceEntry, ...]:
            raise InternalSandboxError("list collaborator failed")

    class FailingWriteWorkspace(MemoryWorkspace):
        async def write(self, request: WorkspaceWriteRequest) -> WorkspaceMutation:
            raise InternalSandboxError("write collaborator failed")

    failing_reader = FailingListWorkspace()
    with pytest.raises(InternalSandboxError):
        await create_default_executor(failing_reader, failing_reader).execute(
            request("ls"),
            CommandExecutionContext(),
        )

    reader = MemoryWorkspace()
    failing_writer = FailingWriteWorkspace()
    with pytest.raises(InternalSandboxError):
        await create_default_executor(reader, failing_writer).execute(
            request("echo content > file"),
            CommandExecutionContext(),
        )


@pytest.mark.asyncio
async def test_timeout_keeps_committed_mutation_but_starts_no_later_command() -> None:
    workspace = MemoryWorkspace()

    class MutatingSlowCommand:
        @property
        def descriptor(self) -> CommandDescriptor:
            return CommandDescriptor("mutate-slow", (), "slow", "mutate-slow", False, False)

        async def execute(
            self,
            request: CommandRequest,
            context: CommandContext,
        ) -> CommandResult:
            await workspace.write(
                WorkspaceWriteRequest(
                    SandboxPath.resolve("/workspace/committed"),
                    b"",
                    AnyCurrentState(),
                )
            )
            await asyncio.sleep(10)
            return CommandResult.success(resulting_cwd=SandboxPath.resolve("/workspace/other"))

    executor = VirtualCommandExecutor(
        CommandRegistry((MutatingSlowCommand(), *create_first_wave_commands(workspace, workspace))),
        workspace,
    )
    with pytest.raises(CommandTimeout):
        await executor.execute(
            request(
                "mutate-slow; touch later",
                limits=CommandLimits(timeout_seconds=0.01),
            ),
            CommandExecutionContext(),
        )

    await workspace.stat(SandboxPath.resolve("/workspace/committed"))
    with pytest.raises(PathNotFoundError):
        await workspace.stat(SandboxPath.resolve("/workspace/later"))


@pytest.mark.asyncio
async def test_swallowed_timeout_cannot_succeed_or_start_later_command() -> None:
    class SwallowingCommand:
        @property
        def descriptor(self) -> CommandDescriptor:
            return CommandDescriptor("swallow", (), "swallow", "swallow", False, False)

        async def execute(
            self,
            request: CommandRequest,
            context: CommandContext,
        ) -> CommandResult:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                return CommandResult.success()
            raise AssertionError

    workspace = MemoryWorkspace()
    executor = VirtualCommandExecutor(
        CommandRegistry((SwallowingCommand(), *create_first_wave_commands(workspace, workspace))),
        workspace,
    )

    with pytest.raises(CommandTimeout):
        await executor.execute(
            request(
                "swallow; touch later",
                limits=CommandLimits(timeout_seconds=0.01),
            ),
            CommandExecutionContext(),
        )

    with pytest.raises(PathNotFoundError):
        await workspace.stat(SandboxPath.resolve("/workspace/later"))


@pytest.mark.asyncio
async def test_mid_plan_cooperative_cancellation_stops_before_next_command() -> None:
    cancellation = asyncio.Event()
    workspace = MemoryWorkspace()

    class CancellingCommand:
        @property
        def descriptor(self) -> CommandDescriptor:
            return CommandDescriptor("cancel", (), "cancel", "cancel", False, False)

        async def execute(
            self,
            request: CommandRequest,
            context: CommandContext,
        ) -> CommandResult:
            await workspace.write(
                WorkspaceWriteRequest(
                    SandboxPath.resolve("/workspace/committed"),
                    b"",
                    AnyCurrentState(),
                )
            )
            cancellation.set()
            return CommandResult.success()

    executor = VirtualCommandExecutor(
        CommandRegistry((CancellingCommand(), *create_first_wave_commands(workspace, workspace))),
        workspace,
    )

    with pytest.raises(CommandCancelled):
        await executor.execute(
            request("cancel; touch later"),
            CommandExecutionContext(cancellation=cancellation),
        )

    await workspace.stat(SandboxPath.resolve("/workspace/committed"))
    with pytest.raises(PathNotFoundError):
        await workspace.stat(SandboxPath.resolve("/workspace/later"))


@pytest.mark.asyncio
async def test_every_dispatch_has_explicit_empty_stdin() -> None:
    seen: list[str] = []

    class Spy:
        @property
        def descriptor(self) -> CommandDescriptor:
            return CommandDescriptor("spy", (), "spy", "spy", False, False)

        async def execute(
            self,
            request: CommandRequest,
            context: CommandContext,
        ) -> CommandResult:
            seen.append(request.stdin)
            return CommandResult.success()

    workspace = MemoryWorkspace()
    executor = VirtualCommandExecutor(CommandRegistry((Spy(),)), workspace)
    await executor.execute(request("spy"), CommandExecutionContext())

    assert seen == [""]


@pytest.mark.asyncio
async def test_cat_invalid_utf8_is_a_normal_workspace_failure() -> None:
    workspace = MemoryWorkspace()
    path = SandboxPath.resolve("/workspace/binary")
    await workspace.write(WorkspaceWriteRequest(path, b"\xff", AnyCurrentState()))

    result = await create_default_executor(workspace, workspace).execute(
        request("cat binary"),
        CommandExecutionContext(),
    )

    assert result.exit_code == 1
    assert result.failure_code is CommandFailureCode.WORKSPACE_FAILURE
