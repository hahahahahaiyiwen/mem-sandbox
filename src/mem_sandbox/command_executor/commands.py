"""Exact first-wave virtual command handlers."""

from __future__ import annotations

from mem_sandbox.command_executor.models import (
    CommandContext,
    CommandDescriptor,
    CommandFailureCode,
    CommandRequest,
    CommandResult,
)
from mem_sandbox.command_executor.ports import CommandWorkspaceMutator, CommandWorkspaceReader
from mem_sandbox.core.errors import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    QuotaExceededError,
    UnsupportedOperationError,
)
from mem_sandbox.workspace import (
    MakeDirectoryRequest,
    NodeKind,
    NotADirectoryError,
    PathMustNotExist,
    PathNotFoundError,
    RemovePathRequest,
    WorkspaceWriteRequest,
)

_EXPECTED_WORKSPACE_FAILURES = (
    InvalidRequestError,
    NotFoundError,
    ConflictError,
    QuotaExceededError,
    UnsupportedOperationError,
)


def _failure(
    command: str,
    message: str,
    *,
    code: CommandFailureCode = CommandFailureCode.WORKSPACE_FAILURE,
    exit_code: int = 1,
    stdout: str = "",
) -> CommandResult:
    return CommandResult(exit_code, code, stdout, f"{command}: {message}\n")


def _invalid(command: str, message: str) -> CommandResult:
    return _failure(
        command,
        message,
        code=CommandFailureCode.INVALID_ARGUMENT,
        exit_code=2,
    )


def _reject_stdin(command: str, request: CommandRequest) -> CommandResult | None:
    if request.stdin:
        return _invalid(command, "stdin is not accepted")
    return None


class PwdCommand:
    """Print the explicit current working directory."""

    descriptor = CommandDescriptor("pwd", (), "Print current directory", "pwd", True, False)

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("pwd", request):
            return rejected
        if len(request.argv) != 1:
            return _invalid("pwd", "usage: pwd")
        return CommandResult.success(stdout=f"{context.cwd}\n")


class CdCommand:
    """Change explicit execution cwd after validating a directory."""

    descriptor = CommandDescriptor("cd", (), "Change current directory", "cd PATH", False, False)

    def __init__(self, reader: CommandWorkspaceReader) -> None:
        self._reader = reader

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("cd", request):
            return rejected
        if len(request.argv) != 2:
            return _invalid("cd", "usage: cd PATH")
        if request.argv[1].startswith("-"):
            return _invalid("cd", "usage: cd PATH")
        try:
            path = self._reader.resolve_path(request.argv[1], cwd=context.cwd)
            entry = await self._reader.stat(path)
            if entry.kind is not NodeKind.DIRECTORY:
                raise NotADirectoryError(f"{path} is not a directory")
        except _EXPECTED_WORKSPACE_FAILURES as error:
            return _failure("cd", str(error))
        return CommandResult.success(resulting_cwd=path)


class LsCommand:
    """List direct child names in stable lexical order."""

    descriptor = CommandDescriptor("ls", (), "List directory names", "ls [PATH]", True, False)

    def __init__(self, reader: CommandWorkspaceReader) -> None:
        self._reader = reader

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("ls", request):
            return rejected
        arguments = request.argv[1:]
        if len(arguments) > 1 or any(value.startswith("-") for value in arguments):
            return _invalid("ls", "usage: ls [PATH]")
        try:
            path = (
                context.cwd
                if not arguments
                else self._reader.resolve_path(next(iter(arguments)), cwd=context.cwd)
            )
            entries = await self._reader.list(path)
        except _EXPECTED_WORKSPACE_FAILURES as error:
            return _failure("ls", str(error))
        stdout = "".join(f"{entry.path.name}\n" for entry in entries)
        return CommandResult.success(stdout=stdout)


class CatCommand:
    """Concatenate exact UTF-8 file text."""

    descriptor = CommandDescriptor("cat", (), "Concatenate UTF-8 files", "cat FILE...", True, False)

    def __init__(self, reader: CommandWorkspaceReader) -> None:
        self._reader = reader

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("cat", request):
            return rejected
        if len(request.argv) < 2 or any(value.startswith("-") for value in request.argv[1:]):
            return _invalid("cat", "usage: cat FILE...")
        output: list[str] = []
        for value in request.argv[1:]:
            try:
                path = self._reader.resolve_path(value, cwd=context.cwd)
                output.append((await self._reader.read_text(path)).content)
            except _EXPECTED_WORKSPACE_FAILURES as error:
                return _failure("cat", str(error), stdout="".join(output))
        return CommandResult.success(stdout="".join(output))


class EchoCommand:
    """Emit arguments joined by one space and LF."""

    descriptor = CommandDescriptor("echo", (), "Print arguments", "echo [ARG...]", True, False)

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("echo", request):
            return rejected
        return CommandResult.success(stdout=f"{' '.join(request.argv[1:])}\n")


class MkdirCommand:
    """Create directories, optionally including parents."""

    descriptor = CommandDescriptor(
        "mkdir", (), "Create directories", "mkdir [-p] PATH...", False, False
    )

    def __init__(
        self,
        reader: CommandWorkspaceReader,
        mutator: CommandWorkspaceMutator,
    ) -> None:
        self._reader = reader
        self._mutator = mutator

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("mkdir", request):
            return rejected
        arguments = list(request.argv[1:])
        create_parents = False
        if arguments and arguments[0] == "-p":
            create_parents = True
            arguments.pop(0)
        if not arguments or any(value.startswith("-") for value in arguments):
            return _invalid("mkdir", "usage: mkdir [-p] PATH...")

        for value in arguments:
            try:
                path = self._reader.resolve_path(value, cwd=context.cwd)
            except _EXPECTED_WORKSPACE_FAILURES as error:
                return _failure("mkdir", str(error))
            try:
                await self._mutator.mkdir(MakeDirectoryRequest(path, create_parents))
            except _EXPECTED_WORKSPACE_FAILURES as error:
                if create_parents:
                    try:
                        entry = await self._reader.stat(path)
                    except _EXPECTED_WORKSPACE_FAILURES:
                        pass
                    else:
                        if entry.kind is NodeKind.DIRECTORY:
                            continue
                return _failure("mkdir", str(error))
        return CommandResult.success()


class TouchCommand:
    """Create missing empty files and preserve existing files."""

    descriptor = CommandDescriptor("touch", (), "Create empty files", "touch FILE...", False, False)

    def __init__(
        self,
        reader: CommandWorkspaceReader,
        mutator: CommandWorkspaceMutator,
    ) -> None:
        self._reader = reader
        self._mutator = mutator

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("touch", request):
            return rejected
        arguments = request.argv[1:]
        if not arguments or any(value.startswith("-") for value in arguments):
            return _invalid("touch", "usage: touch FILE...")
        for value in arguments:
            try:
                path = self._reader.resolve_path(value, cwd=context.cwd)
                try:
                    entry = await self._reader.stat(path)
                except PathNotFoundError:
                    await self._mutator.write(WorkspaceWriteRequest(path, b"", PathMustNotExist()))
                else:
                    if entry.kind is NodeKind.DIRECTORY:
                        return _failure("touch", f"{path} is a directory")
            except _EXPECTED_WORKSPACE_FAILURES as error:
                return _failure("touch", str(error))
        return CommandResult.success()


class RmCommand:
    """Remove files or directories with explicit recursive and force flags."""

    descriptor = CommandDescriptor("rm", (), "Remove paths", "rm [-rR] [-f] PATH...", False, False)

    def __init__(
        self,
        reader: CommandWorkspaceReader,
        mutator: CommandWorkspaceMutator,
    ) -> None:
        self._reader = reader
        self._mutator = mutator

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("rm", request):
            return rejected
        recursive = False
        force = False
        paths: list[str] = []
        for argument in request.argv[1:]:
            if argument.startswith("-"):
                if len(argument) == 1 or any(flag not in "rRf" for flag in argument[1:]):
                    return _invalid("rm", "usage: rm [-rR] [-f] PATH...")
                recursive = recursive or "r" in argument or "R" in argument
                force = force or "f" in argument
            else:
                paths.append(argument)
        if not paths:
            return _invalid("rm", "usage: rm [-rR] [-f] PATH...")
        for value in paths:
            try:
                path = self._reader.resolve_path(value, cwd=context.cwd)
                await self._mutator.remove(RemovePathRequest(path, recursive, force))
            except _EXPECTED_WORKSPACE_FAILURES as error:
                return _failure("rm", str(error))
        return CommandResult.success()


def create_first_wave_commands(
    reader: CommandWorkspaceReader,
    mutator: CommandWorkspaceMutator,
) -> tuple[
    PwdCommand,
    CdCommand,
    LsCommand,
    CatCommand,
    EchoCommand,
    MkdirCommand,
    TouchCommand,
    RmCommand,
]:
    """Construct the exact approved command profile with narrow dependencies."""
    return (
        PwdCommand(),
        CdCommand(reader),
        LsCommand(reader),
        CatCommand(reader),
        EchoCommand(),
        MkdirCommand(reader, mutator),
        TouchCommand(reader, mutator),
        RmCommand(reader, mutator),
    )
