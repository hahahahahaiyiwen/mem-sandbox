"""Tokenizer, parser, and environment expansion for the constrained language."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from mem_sandbox.command_executor.errors import (
    CommandEmpty,
    CommandSyntaxInvalid,
    CommandSyntaxUnsupported,
)
from mem_sandbox.command_executor.models import (
    CommandEnvironment,
    CommandWord,
    Connector,
    ExecutionPlan,
    PlanCommand,
    RedirectionMode,
    StdoutRedirection,
    WordFragment,
)
from mem_sandbox.workspace import SandboxPath


class _Operator(StrEnum):
    SEQUENCE = ";"
    AND = "&&"
    REDIRECT = ">"
    APPEND = ">>"


@dataclass(frozen=True, slots=True)
class _OperatorToken:
    value: _Operator


type _Token = CommandWord | _OperatorToken


def tokenize(command: str) -> tuple[_Token, ...]:
    """Tokenize the complete input while retaining quote-aware word fragments."""
    if "\n" in command or "\r" in command:
        raise CommandSyntaxUnsupported("multiline command input is unsupported")

    tokens: list[_Token] = []
    fragments: list[WordFragment] = []
    word_started = False
    index = 0

    def flush_word() -> None:
        nonlocal fragments, word_started
        if word_started:
            tokens.append(CommandWord(tuple(fragments)))
        fragments = []
        word_started = False

    def append_fragment(text: str, expands: bool) -> None:
        nonlocal word_started
        fragments.append(WordFragment(text, expands))
        word_started = True

    while index < len(command):
        character = command[index]
        if character.isspace():
            flush_word()
            index += 1
            continue
        if character in "'\"":
            quote = character
            expands = quote == '"'
            word_started = True
            index += 1
            start = index
            segment = ""
            while index < len(command) and command[index] != quote:
                current = command[index]
                if quote == '"' and current == "\\":
                    if index + 1 >= len(command):
                        raise CommandSyntaxInvalid("incomplete escape in double-quoted word")
                    escaped = command[index + 1]
                    if escaped not in '$`"\\':
                        segment += "\\"
                        index += 1
                        continue
                    if segment or index == start:
                        append_fragment(segment, expands)
                    segment = ""
                    append_fragment(escaped, False)
                    index += 2
                    start = index
                    continue
                if current in "\n\r":
                    raise CommandSyntaxUnsupported("multiline command input is unsupported")
                segment += current
                index += 1
            if index >= len(command):
                raise CommandSyntaxInvalid("unterminated quoted word")
            append_fragment(segment, expands)
            index += 1
            continue
        if character == "\\":
            word_started = True
            index += 1
            if index >= len(command):
                raise CommandSyntaxInvalid("incomplete escape at end of input")
            append_fragment(command[index], False)
            index += 1
            continue
        if character == "#":
            raise CommandSyntaxUnsupported("comments are unsupported")
        if character == "`":
            raise CommandSyntaxUnsupported("command substitution is unsupported")
        if character in "()":
            raise CommandSyntaxUnsupported("command and process substitution are unsupported")

        two = command[index : index + 2]
        if two == "$(":
            raise CommandSyntaxUnsupported("command substitution is unsupported")
        if two in ("<(", ">("):
            raise CommandSyntaxUnsupported("process substitution is unsupported")
        if two == "||":
            raise CommandSyntaxUnsupported("OR sequencing is unsupported")
        if two == "<<":
            raise CommandSyntaxUnsupported("heredocs and input redirection are unsupported")
        if character == "<":
            raise CommandSyntaxUnsupported("input redirection is unsupported")
        if character == "|":
            raise CommandSyntaxUnsupported("pipelines are unsupported")
        if character == "&" and two != "&&":
            raise CommandSyntaxUnsupported("background jobs are unsupported")

        operator: _Operator | None = None
        consumed = 1
        if two == "&&":
            operator = _Operator.AND
            consumed = 2
        elif two == ">>":
            operator = _Operator.APPEND
            consumed = 2
        elif character == ";":
            operator = _Operator.SEQUENCE
        elif character == ">":
            operator = _Operator.REDIRECT
        if operator is not None:
            flush_word()
            tokens.append(_OperatorToken(operator))
            index += consumed
            continue

        start = index
        starts_unquoted_word = not word_started
        while index < len(command):
            current = command[index]
            if current.isspace() or current in "'\"\\#`()<>|&;":
                break
            index += 1
        text = command[start:index]
        if (
            starts_unquoted_word
            and text.isascii()
            and text.isdigit()
            and index < len(command)
            and command[index] == ">"
        ):
            raise CommandSyntaxUnsupported("file-descriptor redirection is unsupported")
        append_fragment(text, True)

    flush_word()
    return tuple(tokens)


def parse_execution_plan(command: str) -> ExecutionPlan:
    """Parse and validate the complete input before any command can dispatch."""
    tokens = tokenize(command)
    if not tokens:
        raise CommandEmpty("command text must contain a command")

    commands: list[PlanCommand] = []
    index = 0
    connector = Connector.ALWAYS
    while index < len(tokens):
        if not isinstance(tokens[index], CommandWord):
            raise CommandSyntaxInvalid("expected a command word")
        words: list[CommandWord] = []
        while index < len(tokens) and isinstance(tokens[index], CommandWord):
            candidate = tokens[index]
            assert isinstance(candidate, CommandWord)
            word = candidate
            _validate_word_expansions(word)
            words.append(word)
            index += 1

        redirection: StdoutRedirection | None = None
        if index < len(tokens):
            token = tokens[index]
            assert isinstance(token, _OperatorToken)
            if token.value in (_Operator.REDIRECT, _Operator.APPEND):
                index += 1
                if index >= len(tokens) or not isinstance(tokens[index], CommandWord):
                    raise CommandSyntaxInvalid("stdout redirection requires one destination")
                candidate = tokens[index]
                assert isinstance(candidate, CommandWord)
                destination = candidate
                _validate_word_expansions(destination)
                index += 1
                if index < len(tokens) and isinstance(tokens[index], CommandWord):
                    raise CommandSyntaxInvalid("stdout redirection must be trailing")
                if index < len(tokens) and isinstance(tokens[index], _OperatorToken):
                    candidate_operator = tokens[index]
                    assert isinstance(candidate_operator, _OperatorToken)
                    if candidate_operator.value in (_Operator.REDIRECT, _Operator.APPEND):
                        raise CommandSyntaxInvalid("only one stdout redirection is permitted")
                redirection = StdoutRedirection(
                    RedirectionMode.REPLACE
                    if token.value is _Operator.REDIRECT
                    else RedirectionMode.APPEND,
                    destination,
                )

        commands.append(PlanCommand(connector, tuple(words), redirection))
        if index >= len(tokens):
            break
        separator = tokens[index]
        assert isinstance(separator, _OperatorToken)
        if separator.value not in (_Operator.SEQUENCE, _Operator.AND):
            raise CommandSyntaxInvalid("unexpected redirection operator")
        connector = (
            Connector.ALWAYS if separator.value is _Operator.SEQUENCE else Connector.ON_SUCCESS
        )
        index += 1
        if index >= len(tokens):
            raise CommandSyntaxInvalid("input ends with an incomplete separator")

    return ExecutionPlan(tuple(commands))


def expand_word(
    word: CommandWord,
    environment: CommandEnvironment,
    cwd: SandboxPath,
) -> str:
    """Expand approved variables without word splitting or globbing."""
    return "".join(
        _expand_fragment(fragment.text, environment, cwd)
        if fragment.expands_environment
        else fragment.text
        for fragment in word.fragments
    )


def _validate_word_expansions(word: CommandWord) -> None:
    for fragment in word.fragments:
        if fragment.expands_environment:
            _scan_expansion(fragment.text, None, None)


def _expand_fragment(
    text: str,
    environment: CommandEnvironment,
    cwd: SandboxPath,
) -> str:
    return _scan_expansion(text, environment, cwd)


def _scan_expansion(
    text: str,
    environment: CommandEnvironment | None,
    cwd: SandboxPath | None,
) -> str:
    output: list[str] = []
    index = 0
    while index < len(text):
        if text[index] != "$":
            output.append(text[index])
            index += 1
            continue
        if index + 1 >= len(text):
            output.append("$")
            index += 1
            continue
        if text[index + 1] == "{":
            closing = text.find("}", index + 2)
            if closing < 0:
                raise CommandSyntaxInvalid("unterminated environment expansion")
            name = text[index + 2 : closing]
            if not _is_environment_name(name):
                raise CommandSyntaxInvalid("invalid environment expansion name")
            output.append(_environment_value(name, environment, cwd))
            index = closing + 1
            continue
        if _is_environment_name_start(text[index + 1]):
            end = index + 2
            while end < len(text) and _is_environment_name_character(text[end]):
                end += 1
            name = text[index + 1 : end]
            output.append(_environment_value(name, environment, cwd))
            index = end
            continue
        if text[index + 1].isalpha():
            raise CommandSyntaxInvalid("invalid environment expansion name")
        output.append("$")
        index += 1
    return "".join(output)


def _environment_value(
    name: str,
    environment: CommandEnvironment | None,
    cwd: SandboxPath | None,
) -> str:
    if environment is None:
        return ""
    if name == "PWD":
        assert cwd is not None
        return cwd.value
    return environment.get(name)


def _is_environment_name(name: str) -> bool:
    return (
        bool(name)
        and _is_environment_name_start(name[0])
        and all(_is_environment_name_character(character) for character in name[1:])
    )


def _is_environment_name_start(character: str) -> bool:
    return character == "_" or "A" <= character <= "Z" or "a" <= character <= "z"


def _is_environment_name_character(character: str) -> bool:
    return _is_environment_name_start(character) or "0" <= character <= "9"
