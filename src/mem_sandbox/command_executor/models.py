"""Immutable command execution contracts and plan values."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

from mem_sandbox.core.identifiers import OperationId, SessionId
from mem_sandbox.workspace import SandboxPath

_ENVIRONMENT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_COMMAND_NAME = re.compile(r"[A-Za-z0-9_.-]+\Z")


class CommandFailureCode(StrEnum):
    """Stable normal command failure categories."""

    COMMAND_NOT_FOUND = "command_not_found"
    INVALID_ARGUMENT = "invalid_argument"
    WORKSPACE_FAILURE = "workspace_failure"
    REDIRECTION_FAILURE = "redirection_failure"


@dataclass(frozen=True, slots=True, order=True)
class EnvironmentValue:
    """One approved immutable environment value."""

    name: str
    value: str

    def __post_init__(self) -> None:
        _require_environment_name(self.name)
        _require_utf8_text("environment value", self.value)


@dataclass(frozen=True, slots=True)
class CommandEnvironment:
    """An immutable, duplicate-free approved environment."""

    values: tuple[EnvironmentValue, ...] = ()

    def __post_init__(self) -> None:
        _require_environment_values(self.values)

    def get(self, name: str, default: str = "") -> str:
        """Return an approved value or a deterministic default."""
        _require_environment_name(name)
        for item in self.values:
            if item.name == name:
                return item.value
        return default


@dataclass(frozen=True, slots=True)
class EnvironmentChange:
    """An explicit environment assignment or removal."""

    name: str
    value: str | None

    def __post_init__(self) -> None:
        _require_environment_name(self.name)
        if self.value is not None:
            _require_utf8_text("environment change value", self.value)


@dataclass(frozen=True, slots=True, kw_only=True)
class CommandLimits:
    """Validated resource limits for one complete execution plan."""

    max_command_bytes: int = 32 * 1024
    max_argv_entries: int = 256
    max_argument_bytes: int = 8 * 1024
    max_stdout_bytes: int = 256 * 1024
    max_stderr_bytes: int = 256 * 1024
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        for name in (
            "max_command_bytes",
            "max_argv_entries",
            "max_argument_bytes",
            "max_stdout_bytes",
            "max_stderr_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        _require_timeout(self.timeout_seconds)


@dataclass(frozen=True, slots=True)
class ExecuteRequest:
    """A complete constrained command-language request."""

    command: str
    cwd: SandboxPath
    environment: CommandEnvironment
    limits: CommandLimits

    def __post_init__(self) -> None:
        _require_text("command", self.command)
        _require_instance("cwd", self.cwd, SandboxPath)
        _require_instance("environment", self.environment, CommandEnvironment)
        _require_instance("limits", self.limits, CommandLimits)


@dataclass(frozen=True, slots=True)
class ExecuteResult:
    """Normal completion, including structured non-zero command status."""

    exit_code: int
    failure_code: CommandFailureCode | None
    stdout: str
    stderr: str
    stdout_original_bytes: int
    stderr_original_bytes: int
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: float
    resulting_cwd: SandboxPath
    environment_changes: tuple[EnvironmentChange, ...] = ()

    def __post_init__(self) -> None:
        _require_result_fields(
            self.exit_code,
            self.failure_code,
            self.stdout,
            self.stderr,
            self.resulting_cwd,
            self.environment_changes,
        )
        _require_non_negative_integer("stdout_original_bytes", self.stdout_original_bytes)
        _require_non_negative_integer("stderr_original_bytes", self.stderr_original_bytes)
        _require_boolean("stdout_truncated", self.stdout_truncated)
        _require_boolean("stderr_truncated", self.stderr_truncated)
        _require_non_negative_finite("duration_ms", self.duration_ms)


@dataclass(frozen=True, slots=True)
class CommandRequest:
    """One expanded simple-command request."""

    argv: tuple[str, ...]
    stdin: str

    def __post_init__(self) -> None:
        argv = _require_text_tuple("argv", self.argv)
        if not argv:
            raise ValueError("argv must not be empty")
        _require_text("stdin", self.stdin)


@dataclass(frozen=True, slots=True)
class CommandResult:
    """One pipeline-ready stage result and explicit state transition."""

    exit_code: int
    failure_code: CommandFailureCode | None
    stdout: str
    stderr: str
    resulting_cwd: SandboxPath | None = None
    environment_changes: tuple[EnvironmentChange, ...] = ()

    def __post_init__(self) -> None:
        _require_result_fields(
            self.exit_code,
            self.failure_code,
            self.stdout,
            self.stderr,
            self.resulting_cwd,
            self.environment_changes,
        )

    @classmethod
    def success(
        cls,
        *,
        stdout: str = "",
        stderr: str = "",
        resulting_cwd: SandboxPath | None = None,
    ) -> CommandResult:
        """Create a successful stage result."""
        return cls(0, None, stdout, stderr, resulting_cwd, ())


class CancellationSignal(Protocol):
    """Minimal cooperative cancellation boundary."""

    def is_set(self) -> bool:
        """Return whether cancellation was requested."""
        ...


@dataclass(frozen=True, slots=True)
class CommandExecutionContext:
    """Per-execution identity and cooperative cancellation."""

    session_id: SessionId | None = None
    operation_id: OperationId | None = None
    cancellation: CancellationSignal | None = None


@dataclass(frozen=True, slots=True)
class CommandContext:
    """Current immutable state supplied to one command stage."""

    cwd: SandboxPath
    environment: CommandEnvironment
    cancellation: CancellationSignal | None
    session_id: SessionId | None
    operation_id: OperationId | None


@dataclass(frozen=True, slots=True)
class CommandDescriptor:
    """The registry-owned metadata for one virtual command."""

    name: str
    aliases: tuple[str, ...]
    summary: str
    usage: str
    permits_stdout_redirection: bool
    accepts_stdin: bool

    def __post_init__(self) -> None:
        _require_command_name(self.name)
        aliases = _require_text_tuple("aliases", self.aliases)
        seen = {self.name}
        for alias in aliases:
            _require_command_name(alias)
            if alias in seen:
                raise ValueError(f"duplicate command name or alias: {alias}")
            seen.add(alias)
        if not self.summary.strip():
            raise ValueError("summary must not be empty")
        if not self.usage.strip():
            raise ValueError("usage must not be empty")
        _require_boolean("permits_stdout_redirection", self.permits_stdout_redirection)
        _require_boolean("accepts_stdin", self.accepts_stdin)


class Connector(StrEnum):
    """Eligibility condition for an immutable plan command."""

    ALWAYS = "always"
    ON_SUCCESS = "on_success"


class RedirectionMode(StrEnum):
    """Supported stdout redirection modes."""

    REPLACE = "replace"
    APPEND = "append"


@dataclass(frozen=True, slots=True)
class WordFragment:
    """A quote-aware word fragment with expansion behavior."""

    text: str
    expands_environment: bool


@dataclass(frozen=True, slots=True)
class CommandWord:
    """Adjacent fragments that produce one argv entry."""

    fragments: tuple[WordFragment, ...]


@dataclass(frozen=True, slots=True)
class StdoutRedirection:
    """One trailing stdout destination."""

    mode: RedirectionMode
    destination: CommandWord


@dataclass(frozen=True, slots=True)
class PlanCommand:
    """One immutable simple command in execution order."""

    connector: Connector
    words: tuple[CommandWord, ...]
    redirection: StdoutRedirection | None = None


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """A fully parsed plan that is safe to dispatch."""

    commands: tuple[PlanCommand, ...]


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _require_environment_values(value: object) -> None:
    if not isinstance(value, tuple):
        raise TypeError("values must be a tuple")
    seen: set[str] = set()
    for item_object in cast(tuple[object, ...], value):
        item = _require_environment_value(item_object)
        if item.name in seen:
            raise ValueError(f"duplicate environment name: {item.name}")
        seen.add(item.name)


def _require_timeout(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError("timeout_seconds must be a number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError("timeout_seconds must be positive and finite")


def _require_environment_value(value: object) -> EnvironmentValue:
    if not isinstance(value, EnvironmentValue):
        raise TypeError("values must contain EnvironmentValue instances")
    return value


def _require_instance(name: str, value: object, expected: type[object]) -> None:
    if not isinstance(value, expected):
        raise TypeError(f"{name} must be a {expected.__name__}")


def _require_text_tuple(name: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{name} must be a tuple")
    typed: list[str] = []
    for item in cast(tuple[object, ...], value):
        typed.append(_require_text(f"{name} entry", item))
    return tuple(typed)


def _require_result_fields(
    exit_code: object,
    failure_code: object,
    stdout: object,
    stderr: object,
    resulting_cwd: object,
    environment_changes: object,
) -> None:
    code = _require_non_negative_integer("exit_code", exit_code)
    if failure_code is not None and not isinstance(failure_code, CommandFailureCode):
        raise TypeError("failure_code must be a CommandFailureCode or None")
    if code == 0 and failure_code is not None:
        raise ValueError("successful results must not have a failure code")
    if code != 0 and failure_code is None:
        raise ValueError("non-zero results must have a failure code")
    _require_utf8_text("stdout", stdout)
    _require_utf8_text("stderr", stderr)
    if resulting_cwd is not None:
        _require_instance("resulting_cwd", resulting_cwd, SandboxPath)
    _require_environment_changes(environment_changes)


def _require_environment_changes(value: object) -> None:
    if not isinstance(value, tuple):
        raise TypeError("environment_changes must be a tuple")
    for item in cast(tuple[object, ...], value):
        if not isinstance(item, EnvironmentChange):
            raise TypeError("environment_changes must contain EnvironmentChange instances")


def _require_non_negative_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def _require_boolean(name: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _require_non_negative_finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{name} must be non-negative and finite")
    return float(value)


def _require_environment_name(value: object) -> str:
    text = _require_text("environment name", value)
    if _ENVIRONMENT_NAME.fullmatch(text) is None:
        raise ValueError("environment name must use shell variable syntax")
    return text


def _require_utf8_text(name: str, value: object) -> str:
    text = _require_text(name, value)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} must be valid UTF-8 text") from error
    return text


def _require_command_name(value: object) -> str:
    text = _require_text("command name", value)
    if _COMMAND_NAME.fullmatch(text) is None:
        raise ValueError("command name must contain only letters, digits, '.', '_', or '-'")
    return text
