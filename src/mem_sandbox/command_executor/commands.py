"""Exact first-wave virtual command handlers."""

from __future__ import annotations

import fnmatch
import re
from decimal import Decimal

from mem_sandbox.command_executor.models import (
    CommandContext,
    CommandDescriptor,
    CommandFailureCode,
    CommandRequest,
    CommandResult,
    EnvironmentChange,
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
    CopyPathRequest,
    MakeDirectoryRequest,
    MovePathRequest,
    NodeKind,
    NotADirectoryError,
    PathMustNotExist,
    PathNotFoundError,
    RemovePathRequest,
    SandboxPath,
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


def _protected_value(command: str) -> CommandResult:
    return _failure(
        command,
        "protected values cannot be persisted",
        code=CommandFailureCode.PROTECTED_VALUE_REJECTED,
    )


def _reject_protected_operands(
    command: str,
    operands: tuple[str, ...],
    context: CommandContext,
) -> CommandResult | None:
    if any(context.protection.contains_protected_text(value) for value in operands):
        return _protected_value(command)
    return None


def _reject_stdin(command: str, request: CommandRequest) -> CommandResult | None:
    if request.stdin_connected or request.stdin:
        return _invalid(command, "stdin is not accepted")
    return None


def _records(text: str) -> list[str]:
    parts = text.split("\n")
    records = [f"{part}\n" for part in parts[:-1]]
    if parts[-1]:
        records.append(parts[-1])
    return records


def _without_lf(record: str) -> str:
    return record[:-1] if record.endswith("\n") else record


def _with_lf(record: str) -> str:
    return record if record.endswith("\n") else f"{record}\n"


def _display_path(operand: str, root: SandboxPath, path: SandboxPath) -> str:
    root_parts = root.parts
    path_parts = path.parts
    relative_parts = path_parts[len(root_parts) :]
    if operand.startswith("/"):
        base = root.value
    else:
        base = operand.rstrip("/") or "."
    if not relative_parts:
        return base
    relative = "/".join(relative_parts)
    return f"./{relative}" if base == "." else f"{base}/{relative}"


async def _walk(
    reader: CommandWorkspaceReader,
    root: SandboxPath,
    *,
    max_depth: int | None = None,
) -> list[tuple[SandboxPath, int, NodeKind]]:
    output: list[tuple[SandboxPath, int, NodeKind]] = []

    async def visit(path: SandboxPath, depth: int) -> None:
        entry = await reader.stat(path)
        output.append((entry.path, depth, entry.kind))
        if entry.kind is not NodeKind.DIRECTORY:
            return
        if max_depth is not None and depth >= max_depth:
            return
        for child in await reader.list(entry.path):
            await visit(child.path, depth + 1)

    await visit(root, 0)
    return output


async def _read_inputs(
    command: str,
    reader: CommandWorkspaceReader,
    operands: tuple[str, ...],
    request: CommandRequest,
    context: CommandContext,
) -> tuple[list[tuple[str | None, str]], CommandResult | None]:
    selected = operands or ("-",)
    output: list[tuple[str | None, str]] = []
    for operand in selected:
        if operand == "-":
            output.append((None if not operands else "-", request.stdin))
            continue
        if rejected := _reject_protected_operands(command, (operand,), context):
            return output, rejected
        try:
            path = reader.resolve_path(operand, cwd=context.cwd)
            output.append((operand, (await reader.read_text(path)).content))
        except _EXPECTED_WORKSPACE_FAILURES as error:
            return output, _failure(command, str(error))
    return output, None


class PwdCommand:
    """Print the explicit current working directory."""

    descriptor = CommandDescriptor("pwd", (), "Print current directory", "pwd", True, False, True)

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
        arguments = list(request.argv[1:])
        if arguments and arguments[0] == "--":
            arguments.pop(0)
        if len(arguments) != 1:
            return _invalid("cd", "usage: cd PATH")
        if arguments[0].startswith("-") and request.argv[1] != "--":
            return _invalid("cd", "usage: cd PATH")
        if rejected := _reject_protected_operands("cd", (arguments[0],), context):
            return rejected
        try:
            path = self._reader.resolve_path(arguments[0], cwd=context.cwd)
            entry = await self._reader.stat(path)
            if entry.kind is not NodeKind.DIRECTORY:
                raise NotADirectoryError(f"{path} is not a directory")
        except _EXPECTED_WORKSPACE_FAILURES as error:
            return _failure("cd", str(error))
        return CommandResult.success(resulting_cwd=path)


class LsCommand:
    """List direct child names in stable lexical order."""

    descriptor = CommandDescriptor(
        "ls",
        (),
        "List directory names",
        "ls [-1a] [PATH...]",
        True,
        False,
        True,
    )

    def __init__(self, reader: CommandWorkspaceReader) -> None:
        self._reader = reader

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("ls", request):
            return rejected
        operands: list[str] = []
        options = True
        for argument in request.argv[1:]:
            if options and argument == "--":
                options = False
            elif options and argument.startswith("-") and argument != "-":
                if any(flag not in "1a" for flag in argument[1:]):
                    return _invalid("ls", "usage: ls [-1a] [PATH...]")
            else:
                options = False
                operands.append(argument)
        operands = operands or ["."]
        sections: list[str] = []
        multiple = len(operands) > 1
        for operand in operands:
            if rejected := _reject_protected_operands("ls", (operand,), context):
                return rejected
            try:
                path = self._reader.resolve_path(operand, cwd=context.cwd)
                entry = await self._reader.stat(path)
                if entry.kind is NodeKind.DIRECTORY:
                    body = "".join(
                        f"{child.path.name}\n" for child in await self._reader.list(path)
                    )
                    sections.append(f"{operand}:\n{body}" if multiple else body)
                else:
                    sections.append(f"{operand}\n")
            except _EXPECTED_WORKSPACE_FAILURES as error:
                return _failure("ls", str(error), stdout="\n".join(sections))
        return CommandResult.success(stdout="\n".join(sections))


class CatCommand:
    """Concatenate exact UTF-8 file text."""

    descriptor = CommandDescriptor(
        "cat",
        (),
        "Concatenate UTF-8 files or stdin",
        "cat [FILE...]",
        True,
        True,
        True,
    )

    def __init__(self, reader: CommandWorkspaceReader) -> None:
        self._reader = reader

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        operands: list[str] = []
        options = True
        for argument in request.argv[1:]:
            if options and argument == "--":
                options = False
            elif options and argument.startswith("-") and argument != "-":
                return _invalid("cat", "usage: cat [FILE...]")
            else:
                options = False
                operands.append(argument)
        if not operands:
            return CommandResult.success(stdout=request.stdin)
        output: list[str] = []
        for value in operands:
            if value == "-":
                output.append(request.stdin)
                continue
            if rejected := _reject_protected_operands("cat", (value,), context):
                return rejected
            try:
                path = self._reader.resolve_path(value, cwd=context.cwd)
                output.append((await self._reader.read_text(path)).content)
            except _EXPECTED_WORKSPACE_FAILURES as error:
                return _failure("cat", str(error), stdout="".join(output))
        return CommandResult.success(stdout="".join(output))


class EchoCommand:
    """Emit arguments joined by one space and LF."""

    descriptor = CommandDescriptor(
        "echo", (), "Print arguments", "echo [-n] [ARG...]", True, False, True
    )

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("echo", request):
            return rejected
        arguments = list(request.argv[1:])
        newline = True
        if arguments and arguments[0] == "-n":
            newline = False
            arguments.pop(0)
        suffix = "\n" if newline else ""
        return CommandResult.success(stdout=f"{' '.join(arguments)}{suffix}")


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
        allow_option_paths = False
        if arguments and arguments[0] == "-p":
            create_parents = True
            arguments.pop(0)
        if arguments and arguments[0] == "--":
            allow_option_paths = True
            arguments.pop(0)
        if not arguments or (
            not allow_option_paths and any(value.startswith("-") for value in arguments)
        ):
            return _invalid("mkdir", "usage: mkdir [-p] PATH...")
        if rejected := _reject_protected_operands("mkdir", tuple(arguments), context):
            return rejected

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
        arguments = list(request.argv[1:])
        allow_option_paths = False
        if arguments and arguments[0] == "--":
            allow_option_paths = True
            arguments.pop(0)
        if not arguments or (
            not allow_option_paths and any(value.startswith("-") for value in arguments)
        ):
            return _invalid("touch", "usage: touch FILE...")
        if rejected := _reject_protected_operands("touch", tuple(arguments), context):
            return rejected
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
        options = True
        for argument in request.argv[1:]:
            if options and argument == "--":
                options = False
            elif options and argument.startswith("-"):
                if len(argument) == 1 or any(flag not in "rRf" for flag in argument[1:]):
                    return _invalid("rm", "usage: rm [-rR] [-f] PATH...")
                recursive = recursive or "r" in argument or "R" in argument
                force = force or "f" in argument
            else:
                options = False
                paths.append(argument)
        if not paths:
            return _invalid("rm", "usage: rm [-rR] [-f] PATH...")
        if rejected := _reject_protected_operands("rm", tuple(paths), context):
            return rejected
        for value in paths:
            try:
                path = self._reader.resolve_path(value, cwd=context.cwd)
                await self._mutator.remove(RemovePathRequest(path, recursive, force))
            except _EXPECTED_WORKSPACE_FAILURES as error:
                return _failure("rm", str(error))
        return CommandResult.success()


def _parse_count(
    command: str,
    arguments: tuple[str, ...],
) -> tuple[int, tuple[str, ...]] | CommandResult:
    count = 10
    remaining = list(arguments)
    if remaining and remaining[0] == "--":
        return count, tuple(remaining[1:])
    if remaining and remaining[0] == "-n":
        if len(remaining) < 2:
            return _invalid(command, f"usage: {command} [-n COUNT] [FILE...]")
        value = remaining[1]
        remaining = remaining[2:]
    elif remaining and remaining[0].startswith("-n") and len(remaining[0]) > 2:
        value = remaining.pop(0)[2:]
    elif (
        remaining
        and remaining[0].startswith("-")
        and len(remaining[0]) > 1
        and remaining[0][1:].isdigit()
    ):
        value = remaining.pop(0)[1:]
    else:
        value = None
    if value is not None:
        try:
            count = int(value)
        except ValueError:
            return _invalid(command, f"usage: {command} [-n COUNT] [FILE...]")
        if count < 0:
            return _invalid(command, f"usage: {command} [-n COUNT] [FILE...]")
    if remaining and remaining[0] == "--":
        remaining.pop(0)
    elif any(value.startswith("-") and value != "-" for value in remaining):
        return _invalid(command, f"usage: {command} [-n COUNT] [FILE...]")
    return count, tuple(remaining)


class HeadCommand:
    """Print the first LF-delimited records from files or stdin."""

    descriptor = CommandDescriptor(
        "head", (), "Print first records", "head [-n COUNT] [FILE...]", True, True, True
    )

    def __init__(self, reader: CommandWorkspaceReader) -> None:
        self._reader = reader

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        parsed = _parse_count("head", request.argv[1:])
        if isinstance(parsed, CommandResult):
            return parsed
        count, operands = parsed
        inputs, failure = await _read_inputs("head", self._reader, operands, request, context)
        if failure is not None:
            return failure
        return CommandResult.success(stdout=_limited_input_output(inputs, count, from_end=False))


class TailCommand:
    """Print the final LF-delimited records from files or stdin."""

    descriptor = CommandDescriptor(
        "tail", (), "Print final records", "tail [-n COUNT] [FILE...]", True, True, True
    )

    def __init__(self, reader: CommandWorkspaceReader) -> None:
        self._reader = reader

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        parsed = _parse_count("tail", request.argv[1:])
        if isinstance(parsed, CommandResult):
            return parsed
        count, operands = parsed
        inputs, failure = await _read_inputs("tail", self._reader, operands, request, context)
        if failure is not None:
            return failure
        return CommandResult.success(stdout=_limited_input_output(inputs, count, from_end=True))


def _limited_input_output(
    inputs: list[tuple[str | None, str]],
    count: int,
    *,
    from_end: bool,
) -> str:
    sections: list[str] = []
    multiple = len(inputs) > 1
    for label, text in inputs:
        records = _records(text)
        selected = records[-count:] if from_end and count else records[:count]
        body = "".join(selected)
        if multiple:
            display = "standard input" if label in (None, "-") else label
            sections.append(f"==> {display} <==\n{body}")
        else:
            sections.append(body)
    return "\n".join(sections)


class GrepCommand:
    """Select matching LF-delimited records from stdin or workspace files."""

    descriptor = CommandDescriptor(
        "grep",
        (),
        "Search text records",
        "grep [-FEivnlrR] PATTERN [FILE...]",
        True,
        True,
        True,
    )

    def __init__(self, reader: CommandWorkspaceReader) -> None:
        self._reader = reader

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        fixed = False
        ignore_case = False
        invert = False
        line_numbers = False
        files_only = False
        recursive = False
        arguments = list(request.argv[1:])
        while arguments and arguments[0].startswith("-") and arguments[0] != "-":
            option = arguments.pop(0)
            if option == "--":
                break
            if len(option) == 1 or any(flag not in "FEivnlrR" for flag in option[1:]):
                return _invalid("grep", "usage: grep [-FEivnlrR] PATTERN [FILE...]")
            for flag in option[1:]:
                if flag == "F":
                    fixed = True
                elif flag == "E":
                    fixed = False
                elif flag == "i":
                    ignore_case = True
                elif flag == "v":
                    invert = True
                elif flag == "n":
                    line_numbers = True
                elif flag == "l":
                    files_only = True
                elif flag in "rR":
                    recursive = True
        if not arguments:
            return _invalid("grep", "usage: grep [-FEivnlrR] PATTERN [FILE...]")
        pattern = arguments.pop(0)
        try:
            expression = re.compile(
                re.escape(pattern) if fixed else pattern,
                re.IGNORECASE if ignore_case else 0,
            )
        except re.error as error:
            return _invalid("grep", f"invalid pattern: {error}")

        operands = arguments or (["."] if recursive else ["-"])
        sources: list[tuple[str, str]] = []
        try:
            for operand in operands:
                if operand == "-":
                    sources.append(("(standard input)", request.stdin))
                    continue
                if rejected := _reject_protected_operands("grep", (operand,), context):
                    return rejected
                root = self._reader.resolve_path(operand, cwd=context.cwd)
                entry = await self._reader.stat(root)
                if entry.kind is NodeKind.DIRECTORY:
                    if not recursive:
                        return _failure("grep", f"{root} is a directory")
                    for path, _, kind in await _walk(self._reader, root):
                        if kind is NodeKind.FILE:
                            sources.append(
                                (
                                    _display_path(operand, root, path),
                                    (await self._reader.read_text(path)).content,
                                )
                            )
                else:
                    sources.append((operand, (await self._reader.read_text(root)).content))
        except _EXPECTED_WORKSPACE_FAILURES as error:
            return _failure("grep", str(error))

        output: list[str] = []
        prefix_file = recursive or len(sources) > 1
        any_match = False
        for label, text in sources:
            matched_file = False
            for line_number, record in enumerate(_records(text), start=1):
                matched = expression.search(_without_lf(record)) is not None
                if invert:
                    matched = not matched
                if not matched:
                    continue
                any_match = True
                matched_file = True
                if files_only:
                    output.append(f"{label}\n")
                    break
                prefix = f"{label}:" if prefix_file else ""
                if line_numbers:
                    prefix += f"{line_number}:"
                output.append(f"{prefix}{_with_lf(record)}")
            if files_only and matched_file:
                continue
        if not any_match:
            return CommandResult(1, CommandFailureCode.NO_MATCH, "", "")
        return CommandResult.success(stdout="".join(output))


class FindCommand:
    """Walk one workspace subtree in stable lexical depth-first order."""

    descriptor = CommandDescriptor(
        "find",
        (),
        "Find workspace paths",
        "find [PATH] [-type f|d] [-name GLOB] [-maxdepth N]",
        True,
        False,
        True,
    )

    def __init__(self, reader: CommandWorkspaceReader) -> None:
        self._reader = reader

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("find", request):
            return rejected
        arguments = list(request.argv[1:])
        operand = "."
        if arguments and arguments[0] == "--":
            arguments.pop(0)
            if arguments:
                operand = arguments.pop(0)
        elif arguments and not arguments[0].startswith("-"):
            operand = arguments.pop(0)
        kind_filter: NodeKind | None = None
        name_filter: str | None = None
        max_depth: int | None = None
        while arguments:
            option = arguments.pop(0)
            if option == "-type" and arguments:
                value = arguments.pop(0)
                if value not in ("f", "d"):
                    return _invalid("find", "type must be 'f' or 'd'")
                kind_filter = NodeKind.FILE if value == "f" else NodeKind.DIRECTORY
            elif option == "-name" and arguments:
                name_filter = arguments.pop(0)
            elif option == "-maxdepth" and arguments:
                try:
                    max_depth = int(arguments.pop(0))
                except ValueError:
                    return _invalid("find", "maxdepth must be a non-negative integer")
                if max_depth < 0:
                    return _invalid("find", "maxdepth must be a non-negative integer")
            else:
                return _invalid(
                    "find",
                    "usage: find [PATH] [-type f|d] [-name GLOB] [-maxdepth N]",
                )
        if rejected := _reject_protected_operands("find", (operand,), context):
            return rejected
        try:
            root = self._reader.resolve_path(operand, cwd=context.cwd)
            entries = await _walk(self._reader, root, max_depth=max_depth)
        except _EXPECTED_WORKSPACE_FAILURES as error:
            return _failure("find", str(error))
        output = [
            f"{_display_path(operand, root, path)}\n"
            for path, _, kind in entries
            if (kind_filter is None or kind is kind_filter)
            and (name_filter is None or fnmatch.fnmatchcase(path.name, name_filter))
        ]
        return CommandResult.success(stdout="".join(output))


class WcCommand:
    """Count LF records, words, and exact UTF-8 bytes."""

    descriptor = CommandDescriptor("wc", (), "Count text", "wc [-clw] [FILE...]", True, True, True)

    def __init__(self, reader: CommandWorkspaceReader) -> None:
        self._reader = reader

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        requested: set[str] = set()
        operands: list[str] = []
        options = True
        for argument in request.argv[1:]:
            if options and argument == "--":
                options = False
            elif options and argument.startswith("-") and argument != "-":
                if len(argument) == 1 or any(flag not in "clw" for flag in argument[1:]):
                    return _invalid("wc", "usage: wc [-clw] [FILE...]")
                requested.update(argument[1:])
            else:
                options = False
                operands.append(argument)
        selected = [flag for flag in ("l", "w", "c") if not requested or flag in requested]
        inputs, failure = await _read_inputs("wc", self._reader, tuple(operands), request, context)
        if failure is not None:
            return failure
        rows: list[str] = []
        totals = [0 for _ in selected]
        for label, text in inputs:
            counts = {
                "l": text.count("\n"),
                "w": len(text.split()),
                "c": len(text.encode("utf-8")),
            }
            values = [counts[flag] for flag in selected]
            totals = [total + value for total, value in zip(totals, values, strict=True)]
            suffix = f" {label}" if label is not None else ""
            rows.append(f"{' '.join(str(value) for value in values)}{suffix}\n")
        if len(inputs) > 1:
            rows.append(f"{' '.join(str(value) for value in totals)} total\n")
        return CommandResult.success(stdout="".join(rows))


class SortCommand:
    """Sort LF-delimited records deterministically."""

    descriptor = CommandDescriptor(
        "sort", (), "Sort text records", "sort [-nru] [FILE...]", True, True, True
    )

    def __init__(self, reader: CommandWorkspaceReader) -> None:
        self._reader = reader

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        numeric = False
        reverse = False
        unique = False
        operands: list[str] = []
        options = True
        for argument in request.argv[1:]:
            if options and argument == "--":
                options = False
            elif options and argument.startswith("-") and argument != "-":
                if len(argument) == 1 or any(flag not in "nru" for flag in argument[1:]):
                    return _invalid("sort", "usage: sort [-nru] [FILE...]")
                numeric = numeric or "n" in argument
                reverse = reverse or "r" in argument
                unique = unique or "u" in argument
            else:
                options = False
                operands.append(argument)
        inputs, failure = await _read_inputs(
            "sort", self._reader, tuple(operands), request, context
        )
        if failure is not None:
            return failure
        lines = [_without_lf(record) for _, text in inputs for record in _records(text)]
        key = _numeric_sort_key if numeric else _byte_sort_key
        lines.sort(key=key, reverse=reverse)
        if unique:
            lines = [
                line
                for index, line in enumerate(lines)
                if index == 0 or key(line) != key(lines[index - 1])
            ]
        return CommandResult.success(stdout="".join(f"{line}\n" for line in lines))


def _numeric_sort_key(value: str) -> Decimal:
    match = re.match(r"[ \t]*([+-]?(?:\d+(?:\.\d*)?|\.\d+))", value)
    return Decimal(match.group(1)) if match is not None else Decimal(0)


def _byte_sort_key(value: str) -> bytes:
    return value.encode("utf-8")


class UniqCommand:
    """Collapse adjacent equal LF-delimited records."""

    descriptor = CommandDescriptor(
        "uniq", (), "Collapse adjacent records", "uniq [-c] [FILE]", True, True, True
    )

    def __init__(self, reader: CommandWorkspaceReader) -> None:
        self._reader = reader

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        arguments = list(request.argv[1:])
        count = False
        if arguments and arguments[0] == "-c":
            count = True
            arguments.pop(0)
        if arguments and arguments[0] == "--":
            arguments.pop(0)
        if len(arguments) > 1 or any(value.startswith("-") and value != "-" for value in arguments):
            return _invalid("uniq", "usage: uniq [-c] [FILE]")
        inputs, failure = await _read_inputs(
            "uniq", self._reader, tuple(arguments), request, context
        )
        if failure is not None:
            return failure
        records = [_without_lf(record) for record in _records(inputs[0][1])]
        output: list[str] = []
        index = 0
        while index < len(records):
            end = index + 1
            while end < len(records) and records[end] == records[index]:
                end += 1
            prefix = f"{end - index:7} " if count else ""
            output.append(f"{prefix}{records[index]}\n")
            index = end
        return CommandResult.success(stdout="".join(output))


class CpCommand:
    """Copy files or non-merging directory trees."""

    descriptor = CommandDescriptor(
        "cp", (), "Copy workspace paths", "cp [-fRr] SOURCE... DEST", False, False
    )

    def __init__(
        self,
        reader: CommandWorkspaceReader,
        mutator: CommandWorkspaceMutator,
    ) -> None:
        self._reader = reader
        self._mutator = mutator

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("cp", request):
            return rejected
        recursive = False
        operands: list[str] = []
        options = True
        for argument in request.argv[1:]:
            if options and argument == "--":
                options = False
            elif options and argument.startswith("-"):
                if len(argument) == 1 or any(flag not in "fRr" for flag in argument[1:]):
                    return _invalid("cp", "usage: cp [-fRr] SOURCE... DEST")
                recursive = recursive or "r" in argument or "R" in argument
            else:
                options = False
                operands.append(argument)
        if len(operands) < 2:
            return _invalid("cp", "usage: cp [-fRr] SOURCE... DEST")
        return await _transfer(
            "cp",
            self._reader,
            self._mutator,
            tuple(operands[:-1]),
            operands[-1],
            context,
            recursive=recursive,
            move=False,
        )


class MvCommand:
    """Move workspace paths without merging directory trees."""

    descriptor = CommandDescriptor(
        "mv", (), "Move workspace paths", "mv [-f] SOURCE... DEST", False, False
    )

    def __init__(
        self,
        reader: CommandWorkspaceReader,
        mutator: CommandWorkspaceMutator,
    ) -> None:
        self._reader = reader
        self._mutator = mutator

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("mv", request):
            return rejected
        operands: list[str] = []
        options = True
        for argument in request.argv[1:]:
            if options and argument == "--":
                options = False
            elif options and argument.startswith("-"):
                if argument != "-f":
                    return _invalid("mv", "usage: mv [-f] SOURCE... DEST")
            else:
                options = False
                operands.append(argument)
        if len(operands) < 2:
            return _invalid("mv", "usage: mv [-f] SOURCE... DEST")
        return await _transfer(
            "mv",
            self._reader,
            self._mutator,
            tuple(operands[:-1]),
            operands[-1],
            context,
            recursive=True,
            move=True,
        )


async def _transfer(
    command: str,
    reader: CommandWorkspaceReader,
    mutator: CommandWorkspaceMutator,
    sources: tuple[str, ...],
    destination_operand: str,
    context: CommandContext,
    *,
    recursive: bool,
    move: bool,
) -> CommandResult:
    if rejected := _reject_protected_operands(
        command,
        (*sources, destination_operand),
        context,
    ):
        return rejected
    try:
        destination = reader.resolve_path(destination_operand, cwd=context.cwd)
        try:
            destination_entry = await reader.stat(destination)
        except PathNotFoundError:
            destination_entry = None
        if len(sources) > 1 and (
            destination_entry is None or destination_entry.kind is not NodeKind.DIRECTORY
        ):
            return _failure(command, "destination must be an existing directory")
        for source_operand in sources:
            source = reader.resolve_path(source_operand, cwd=context.cwd)
            source_entry = await reader.stat(source)
            if source_entry.kind is NodeKind.DIRECTORY and not recursive:
                return _failure(command, f"{source} is a directory; use -r")
            exact_destination = (
                destination.join(source.name)
                if destination_entry is not None and destination_entry.kind is NodeKind.DIRECTORY
                else destination
            )
            try:
                existing = await reader.stat(exact_destination)
            except PathNotFoundError:
                existing = None
            if source_entry.kind is NodeKind.DIRECTORY and existing is not None:
                return _failure(command, "recursive directory merge is unsupported")
            if (
                source_entry.kind is NodeKind.FILE
                and existing is not None
                and existing.kind is NodeKind.DIRECTORY
            ):
                return _failure(command, f"{exact_destination} is a directory")
            if move:
                await mutator.move(MovePathRequest(source, exact_destination, overwrite=True))
            else:
                await mutator.copy(CopyPathRequest(source, exact_destination, overwrite=True))
    except _EXPECTED_WORKSPACE_FAILURES as error:
        return _failure(command, str(error))
    return CommandResult.success()


class EnvCommand:
    """Print the approved environment with a derived PWD."""

    descriptor = CommandDescriptor("env", (), "Print environment", "env", True, False, True)

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("env", request):
            return rejected
        if request.argv[1:] not in ((), ("--",)):
            return _invalid("env", "usage: env")
        values = {item.name: item.value for item in context.environment.values}
        values["PWD"] = context.cwd.value
        return CommandResult.success(
            stdout="".join(f"{name}={value}\n" for name, value in sorted(values.items()))
        )


class ExportCommand:
    """Publish explicit environment assignments."""

    descriptor = CommandDescriptor(
        "export", (), "Set environment values", "export NAME=VALUE...", False, False
    )

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("export", request):
            return rejected
        arguments = list(request.argv[1:])
        if arguments and arguments[0] == "--":
            arguments.pop(0)
        if not arguments:
            return _invalid("export", "usage: export NAME=VALUE...")
        changes: list[EnvironmentChange] = []
        try:
            for argument in arguments:
                if context.protection.contains_protected_text(argument):
                    return _protected_value("export")
                name, separator, value = argument.partition("=")
                if not separator or name == "PWD":
                    return _invalid("export", "usage: export NAME=VALUE...")
                if context.environment.is_overlay_name(name):
                    return _protected_value("export")
                changes.append(EnvironmentChange(name, value))
        except (TypeError, ValueError):
            return _invalid("export", "usage: export NAME=VALUE...")
        return CommandResult(0, None, "", "", environment_changes=tuple(changes))


class UnsetCommand:
    """Publish explicit environment removals."""

    descriptor = CommandDescriptor(
        "unset", (), "Remove environment values", "unset NAME...", False, False
    )

    async def execute(self, request: CommandRequest, context: CommandContext) -> CommandResult:
        if rejected := _reject_stdin("unset", request):
            return rejected
        arguments = list(request.argv[1:])
        if arguments and arguments[0] == "--":
            arguments.pop(0)
        if not arguments or "PWD" in arguments:
            return _invalid("unset", "usage: unset NAME...")
        if any(context.protection.contains_protected_text(name) for name in arguments):
            return _protected_value("unset")
        try:
            if any(context.environment.is_overlay_name(name) for name in arguments):
                return _protected_value("unset")
            changes = tuple(EnvironmentChange(name, None) for name in arguments)
        except (TypeError, ValueError):
            return _invalid("unset", "usage: unset NAME...")
        return CommandResult(0, None, "", "", environment_changes=changes)


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
    """Construct the exact Milestone 2 command profile."""
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


def create_command_profile(
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
    HeadCommand,
    TailCommand,
    GrepCommand,
    FindCommand,
    WcCommand,
    SortCommand,
    UniqCommand,
    CpCommand,
    MvCommand,
    EnvCommand,
    ExportCommand,
    UnsetCommand,
]:
    """Construct the complete approved command profile with narrow dependencies."""
    return (
        *create_first_wave_commands(reader, mutator),
        HeadCommand(reader),
        TailCommand(reader),
        GrepCommand(reader),
        FindCommand(reader),
        WcCommand(reader),
        SortCommand(reader),
        UniqCommand(reader),
        CpCommand(reader, mutator),
        MvCommand(reader, mutator),
        EnvCommand(),
        ExportCommand(),
        UnsetCommand(),
    )
