from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from mem_sandbox.command_executor import (
    CommandDescriptor,
    CommandRegistry,
    CommandSyntaxInvalid,
    CommandSyntaxUnsupported,
    parse_execution_plan,
)


class Command:
    def __init__(self, descriptor: CommandDescriptor) -> None:
        self._descriptor = descriptor

    @property
    def descriptor(self) -> CommandDescriptor:
        return self._descriptor

    async def execute(self, request, context):  # type: ignore[no-untyped-def]
        raise AssertionError


def test_registry_is_case_sensitive_alias_explicit_duplicate_safe_and_immutable() -> None:
    command = Command(CommandDescriptor("echo", ("say",), "summary", "echo [ARG...]", True, False))
    registry = CommandRegistry((command,))
    assert registry.resolve("echo") is command
    assert registry.resolve("say") is command
    assert registry.resolve("ECHO") is None
    assert registry.descriptions == (command.descriptor,)
    with pytest.raises(FrozenInstanceError):
        command.descriptor.name = "changed"  # type: ignore[misc]
    with pytest.raises(ValueError, match="duplicate"):
        CommandRegistry(
            (command, Command(CommandDescriptor("other", ("echo",), "s", "u", False, False)))
        )


@pytest.mark.parametrize(
    "command",
    (
        "echo a | cat",
        "echo a || echo b",
        "cat < file",
        "cat << EOF",
        "echo a &",
        "echo $(pwd)",
        "echo <(pwd)",
        "echo `pwd`",
        "echo a\npwd",
        "echo a # comment",
        "echo hi 2> err",
        "echo hi 2>> err",
    ),
)
def test_unsupported_syntax_is_explicit(command: str) -> None:
    with pytest.raises(CommandSyntaxUnsupported):
        parse_execution_plan(command)


@pytest.mark.parametrize(
    "command",
    (
        "",
        " ",
        "echo '",
        "echo \\",
        "echo &&",
        "; echo",
        "echo >",
        "echo > a b",
        "echo > a > b",
        "echo ${é}",
        "echo $é",
    ),
)
def test_invalid_or_incomplete_syntax_is_explicit(command: str) -> None:
    from mem_sandbox.command_executor import CommandEmpty

    expected = CommandEmpty if not command.strip() else CommandSyntaxInvalid
    with pytest.raises(expected):
        parse_execution_plan(command)
