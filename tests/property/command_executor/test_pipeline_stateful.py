from __future__ import annotations

from dataclasses import dataclass

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.property.async_machine import AsyncRunner

from mem_sandbox.command_executor import (
    CommandContext,
    CommandDescriptor,
    CommandEnvironment,
    CommandExecutionContext,
    CommandFailureCode,
    CommandLimits,
    CommandRegistry,
    CommandRequest,
    CommandResult,
    ExecuteRequest,
    ExecuteResult,
    VirtualCommandExecutor,
    create_default_executor,
)
from mem_sandbox.workspace import MemoryWorkspace, PathNotFoundError, SandboxPath


@dataclass(frozen=True, slots=True)
class PipelineCase:
    outputs: tuple[str, ...]
    errors: tuple[str, ...]
    exit_codes: tuple[int, ...]


@st.composite
def pipeline_cases(draw: st.DrawFn) -> PipelineCase:
    stage_count = draw(st.integers(min_value=2, max_value=4))
    text = st.text(alphabet="abcé", min_size=0, max_size=5)
    outputs = tuple(draw(st.lists(text, min_size=stage_count, max_size=stage_count)))
    errors = tuple(draw(st.lists(text, min_size=stage_count, max_size=stage_count)))
    exit_codes = tuple(
        draw(
            st.lists(
                st.integers(min_value=0, max_value=3),
                min_size=stage_count,
                max_size=stage_count,
            )
        )
    )
    return PipelineCase(outputs, errors, exit_codes)


class TraceCommand:
    def __init__(
        self,
        name: str,
        result: CommandResult,
        trace: list[tuple[str, str, bool, tuple[str, ...]]],
    ) -> None:
        self._descriptor = CommandDescriptor(
            name,
            (),
            name,
            name,
            True,
            True,
            True,
        )
        self._result = result
        self._trace = trace

    @property
    def descriptor(self) -> CommandDescriptor:
        return self._descriptor

    async def execute(
        self,
        request: CommandRequest,
        context: CommandContext,
    ) -> CommandResult:
        self._trace.append(
            (
                self._descriptor.name,
                request.stdin,
                request.stdin_connected,
                request.argv,
            )
        )
        return self._result


def _request(command: str, limits: CommandLimits | None = None) -> ExecuteRequest:
    return ExecuteRequest(
        command=command,
        cwd=SandboxPath.root(),
        environment=CommandEnvironment(),
        limits=limits or CommandLimits(),
    )


@given(case=pipeline_cases())
def test_generated_pipeline_passes_exact_intermediates_and_uses_rightmost_failure(
    case: PipelineCase,
) -> None:
    async def exercise() -> None:
        workspace = MemoryWorkspace()
        trace: list[tuple[str, str, bool, tuple[str, ...]]] = []
        commands = tuple(
            TraceCommand(
                f"stage{index}",
                CommandResult(
                    exit_code,
                    None if exit_code == 0 else CommandFailureCode.NO_MATCH,
                    case.outputs[index],
                    case.errors[index],
                ),
                trace,
            )
            for index, exit_code in enumerate(case.exit_codes)
        )
        result = await VirtualCommandExecutor(
            CommandRegistry(commands),
            workspace,
        ).execute(
            _request(" | ".join(command.descriptor.name for command in commands)),
            CommandExecutionContext(),
        )

        assert len(trace) == len(commands)
        assert trace[0][1:] == ("", False, ("stage0",))
        for index in range(1, len(trace)):
            assert trace[index][1] == case.outputs[index - 1]
            assert trace[index][2]
            assert trace[index][3] == (f"stage{index}",)

        failing = [exit_code for exit_code in case.exit_codes if exit_code != 0]
        assert result.exit_code == (failing[-1] if failing else 0)
        assert result.failure_code is (CommandFailureCode.NO_MATCH if failing else None)
        assert result.stdout == case.outputs[-1]
        assert result.stderr == "".join(case.errors)

    with AsyncRunner() as runner:
        runner.run(exercise())


@given(size=st.integers(min_value=2, max_value=16))
def test_pipeline_intermediate_and_aggregate_exact_boundaries(size: int) -> None:
    async def exercise() -> None:
        output = "x" * size

        exact_workspace = MemoryWorkspace()
        exact_trace: list[tuple[str, str, bool, tuple[str, ...]]] = []
        exact_commands = (
            TraceCommand("source", CommandResult.success(stdout=output), exact_trace),
            TraceCommand("middle", CommandResult.success(stdout=output), exact_trace),
            TraceCommand("sink", CommandResult.success(stdout="done"), exact_trace),
        )
        exact = await VirtualCommandExecutor(
            CommandRegistry(exact_commands),
            exact_workspace,
        ).execute(
            _request(
                "source | middle | sink > result",
                CommandLimits(
                    max_pipeline_intermediate_bytes=size,
                    max_pipeline_aggregate_bytes=size * 2,
                ),
            ),
            CommandExecutionContext(),
        )
        assert exact.exit_code == 0
        assert [item[0] for item in exact_trace] == ["source", "middle", "sink"]
        assert (
            await exact_workspace.read_text(SandboxPath.resolve("/workspace/result"))
        ).content == "done"

        intermediate_workspace = MemoryWorkspace()
        intermediate_trace: list[tuple[str, str, bool, tuple[str, ...]]] = []
        intermediate_commands = (
            TraceCommand("source", CommandResult.success(stdout=output), intermediate_trace),
            TraceCommand("sink", CommandResult.success(stdout="unreached"), intermediate_trace),
        )
        intermediate = await VirtualCommandExecutor(
            CommandRegistry(intermediate_commands),
            intermediate_workspace,
        ).execute(
            _request(
                "source | sink > result",
                CommandLimits(max_pipeline_intermediate_bytes=size - 1),
            ),
            CommandExecutionContext(),
        )
        assert intermediate.failure_code is CommandFailureCode.PIPELINE_LIMIT_EXCEEDED
        assert [item[0] for item in intermediate_trace] == ["source"]
        with pytest.raises(PathNotFoundError):
            await intermediate_workspace.stat(SandboxPath.resolve("/workspace/result"))

        aggregate_workspace = MemoryWorkspace()
        aggregate_trace: list[tuple[str, str, bool, tuple[str, ...]]] = []
        aggregate_commands = (
            TraceCommand("source", CommandResult.success(stdout=output), aggregate_trace),
            TraceCommand("middle", CommandResult.success(stdout=output), aggregate_trace),
            TraceCommand("sink", CommandResult.success(stdout="unreached"), aggregate_trace),
        )
        aggregate = await VirtualCommandExecutor(
            CommandRegistry(aggregate_commands),
            aggregate_workspace,
        ).execute(
            _request(
                "source | middle | sink > result",
                CommandLimits(
                    max_pipeline_intermediate_bytes=size,
                    max_pipeline_aggregate_bytes=(size * 2) - 1,
                ),
            ),
            CommandExecutionContext(),
        )
        assert aggregate.failure_code is CommandFailureCode.PIPELINE_LIMIT_EXCEEDED
        assert [item[0] for item in aggregate_trace] == ["source", "middle"]
        with pytest.raises(PathNotFoundError):
            await aggregate_workspace.stat(SandboxPath.resolve("/workspace/result"))

    with AsyncRunner() as runner:
        runner.run(exercise())


@given(
    stage_count=st.integers(min_value=2, max_value=4),
    missing_index=st.integers(min_value=0, max_value=7),
)
def test_pipeline_preflights_every_stage_before_dispatch(
    stage_count: int,
    missing_index: int,
) -> None:
    async def exercise() -> None:
        workspace = MemoryWorkspace()
        trace: list[tuple[str, str, bool, tuple[str, ...]]] = []
        missing = missing_index % stage_count
        names = [f"stage{index}" for index in range(stage_count)]
        names[missing] = "missing"
        commands = tuple(
            TraceCommand(name, CommandResult.success(stdout=name), trace)
            for index, name in enumerate(names)
            if index != missing
        )
        result = await VirtualCommandExecutor(
            CommandRegistry(commands),
            workspace,
        ).execute(
            _request(" | ".join(names)),
            CommandExecutionContext(),
        )

        assert result.exit_code == 127
        assert result.failure_code is CommandFailureCode.COMMAND_NOT_FOUND
        assert trace == []

    with AsyncRunner() as runner:
        runner.run(exercise())


def _normalized_result(result: ExecuteResult) -> tuple[object, ...]:
    return (
        result.exit_code,
        result.failure_code,
        result.stdout,
        result.stderr,
        result.stdout_original_bytes,
        result.stderr_original_bytes,
        result.stdout_truncated,
        result.stderr_truncated,
        result.resulting_cwd,
        result.environment_changes,
    )


@given(
    words=st.lists(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=6),
        min_size=1,
        max_size=6,
    )
)
def test_default_executor_is_deterministic_for_generated_pipeline_input(
    words: list[str],
) -> None:
    async def exercise() -> None:
        results: list[tuple[ExecuteResult, bytes]] = []
        command = f"echo {' '.join(words)} | wc -w"
        for _ in range(2):
            workspace = MemoryWorkspace()
            result = await create_default_executor(workspace, workspace).execute(
                _request(command),
                CommandExecutionContext(),
            )
            results.append((result, (await workspace.export()).encoded))

        assert _normalized_result(results[0][0]) == _normalized_result(results[1][0])
        assert results[0][1] == results[1][1]
        first = results[0][0]
        assert first.stdout == f"{len(words)}\n"

    with AsyncRunner() as runner:
        runner.run(exercise())
