"""Immutable command execution contracts and plan values."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
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
    NO_MATCH = "no_match"
    WORKSPACE_FAILURE = "workspace_failure"
    PIPELINE_LIMIT_EXCEEDED = "pipeline_limit_exceeded"
    REDIRECTION_FAILURE = "redirection_failure"
    PROTECTED_VALUE_REJECTED = "protected_value_rejected"


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


@dataclass(frozen=True, slots=True, repr=False)
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
    max_pipeline_stages: int = 8
    max_pipeline_intermediate_bytes: int = 256 * 1024
    max_pipeline_aggregate_bytes: int = 1024 * 1024
    max_secret_bindings: int = 16
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        for name in (
            "max_command_bytes",
            "max_argv_entries",
            "max_argument_bytes",
            "max_stdout_bytes",
            "max_stderr_bytes",
            "max_pipeline_stages",
            "max_pipeline_intermediate_bytes",
            "max_pipeline_aggregate_bytes",
            "max_secret_bindings",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")
        _require_timeout(self.timeout_seconds)


class CommandOverlayValue(Protocol):
    def reveal_text(self) -> str: ...


@dataclass(frozen=True, slots=True, repr=False)
class CommandEnvironmentOverlayEntry:
    name: str
    value: CommandOverlayValue

    def __post_init__(self) -> None:
        _require_environment_name(self.name)
        if self.name == "PWD":
            raise ValueError("PWD cannot be overlaid")
        if not callable(getattr(self.value, "reveal_text", None)):
            raise TypeError("overlay value must implement reveal_text")


@dataclass(frozen=True, slots=True, repr=False)
class CommandEnvironmentOverlay:
    entries: tuple[CommandEnvironmentOverlayEntry, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.entries), tuple):
            raise TypeError("overlay entries must be a tuple")
        names: set[str] = set()
        for entry in self.entries:
            if not isinstance(cast(object, entry), CommandEnvironmentOverlayEntry):
                raise TypeError(
                    "overlay entries must contain CommandEnvironmentOverlayEntry values"
                )
            if entry.name in names:
                raise ValueError(f"duplicate overlay environment name: {entry.name}")
            names.add(entry.name)
        object.__setattr__(
            self,
            "entries",
            tuple(sorted(self.entries, key=lambda entry: entry.name)),
        )

    def get(self, name: str) -> str | None:
        _require_environment_name(name)
        for entry in self.entries:
            if entry.name == name:
                return entry.value.reveal_text()
        return None

    def contains(self, name: str) -> bool:
        _require_environment_name(name)
        return any(entry.name == name for entry in self.entries)


@dataclass(frozen=True, slots=True, repr=False)
class CommandEnvironmentViewEntry:
    name: str
    value: str

    def __post_init__(self) -> None:
        _require_environment_name(self.name)
        _require_utf8_text("environment view value", self.value)


@dataclass(frozen=True, slots=True, repr=False)
class CommandEnvironmentView:
    base: CommandEnvironment
    overlay: CommandEnvironmentOverlay = field(default_factory=CommandEnvironmentOverlay)

    def __post_init__(self) -> None:
        _require_instance("base", self.base, CommandEnvironment)
        _require_instance("overlay", self.overlay, CommandEnvironmentOverlay)

    def get(self, name: str, default: str = "") -> str:
        _require_environment_name(name)
        overlay_value = self.overlay.get(name)
        if overlay_value is not None:
            return overlay_value
        return self.base.get(name, default)

    @property
    def values(self) -> tuple[CommandEnvironmentViewEntry, ...]:
        current = {item.name: item.value for item in self.base.values}
        for entry in self.overlay.entries:
            current[entry.name] = entry.value.reveal_text()
        return tuple(
            CommandEnvironmentViewEntry(name, value) for name, value in sorted(current.items())
        )

    def is_overlay_name(self, name: str) -> bool:
        return self.overlay.contains(name)


class CommandValueProtection(Protocol):
    def redact_text(self, value: str) -> str: ...

    def redact_bytes(self, value: bytes) -> bytes: ...

    def contains_protected_text(self, value: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class NoOpCommandValueProtection:
    def redact_text(self, value: str) -> str:
        return value

    def redact_bytes(self, value: bytes) -> bytes:
        return value

    def contains_protected_text(self, value: str) -> bool:
        return False


@dataclass(frozen=True, slots=True, repr=False)
class ExecuteRequest:
    """A complete constrained command-language request."""

    command: str
    cwd: SandboxPath
    environment: CommandEnvironment
    limits: CommandLimits
    overlay: CommandEnvironmentOverlay = field(default_factory=CommandEnvironmentOverlay)

    def __post_init__(self) -> None:
        _require_text("command", self.command)
        _require_instance("cwd", self.cwd, SandboxPath)
        _require_instance("environment", self.environment, CommandEnvironment)
        _require_instance("limits", self.limits, CommandLimits)
        _require_instance("overlay", self.overlay, CommandEnvironmentOverlay)
        if len(self.overlay.entries) > self.limits.max_secret_bindings:
            raise ValueError("overlay exceeds limits.max_secret_bindings")


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


@dataclass(frozen=True, slots=True, repr=False)
class CommandRequest:
    """One expanded simple-command request."""

    argv: tuple[str, ...]
    stdin: str = ""
    stdin_connected: bool = False

    def __post_init__(self) -> None:
        argv = _require_text_tuple("argv", self.argv)
        if not argv:
            raise ValueError("argv must not be empty")
        _require_text("stdin", self.stdin)
        _require_boolean("stdin_connected", self.stdin_connected)


@dataclass(frozen=True, slots=True, repr=False)
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


@dataclass(frozen=True, slots=True, repr=False)
class CommandExecutionContext:
    """Per-execution identity and cooperative cancellation."""

    session_id: SessionId | None = None
    operation_id: OperationId | None = None
    cancellation: CancellationSignal | None = None
    protection: CommandValueProtection = field(default_factory=NoOpCommandValueProtection)

    def __post_init__(self) -> None:
        protection = self.protection
        for method in ("redact_text", "redact_bytes", "contains_protected_text"):
            if not callable(getattr(protection, method, None)):
                raise TypeError("protection must implement CommandValueProtection")


@dataclass(frozen=True, slots=True, repr=False, init=False)
class CommandContext:
    """Current immutable state supplied to one command stage."""

    cwd: SandboxPath
    environment: CommandEnvironmentView
    cancellation: CancellationSignal | None
    session_id: SessionId | None
    operation_id: OperationId | None
    protection: CommandValueProtection

    def __init__(
        self,
        cwd: SandboxPath,
        environment: CommandEnvironment | CommandEnvironmentView,
        cancellation: CancellationSignal | None,
        session_id: SessionId | None,
        operation_id: OperationId | None,
        protection: CommandValueProtection | None = None,
    ) -> None:
        _require_instance("cwd", cwd, SandboxPath)
        environment_object = cast(object, environment)
        if isinstance(environment_object, CommandEnvironment):
            environment_view = CommandEnvironmentView(environment_object)
        elif isinstance(environment_object, CommandEnvironmentView):
            environment_view = environment_object
        else:
            raise TypeError("environment must be a CommandEnvironment or CommandEnvironmentView")
        resolved_protection = NoOpCommandValueProtection() if protection is None else protection
        for method in ("redact_text", "redact_bytes", "contains_protected_text"):
            if not callable(getattr(resolved_protection, method, None)):
                raise TypeError("protection must implement CommandValueProtection")
        object.__setattr__(self, "cwd", cwd)
        object.__setattr__(self, "environment", environment_view)
        object.__setattr__(self, "cancellation", cancellation)
        object.__setattr__(self, "session_id", session_id)
        object.__setattr__(self, "operation_id", operation_id)
        object.__setattr__(self, "protection", resolved_protection)


@dataclass(frozen=True, slots=True)
class CommandDescriptor:
    """The registry-owned metadata for one virtual command."""

    name: str
    aliases: tuple[str, ...]
    summary: str
    usage: str
    permits_stdout_redirection: bool
    accepts_stdin: bool
    pipeline_safe: bool = False

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
        _require_boolean("pipeline_safe", self.pipeline_safe)


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
class CommandStage:
    """One immutable simple-command stage."""

    words: tuple[CommandWord, ...]

    def __post_init__(self) -> None:
        if not self.words:
            raise ValueError("command stage words must not be empty")


@dataclass(frozen=True, slots=True)
class PlanUnit:
    """One command or pipeline with one eligibility connector."""

    connector: Connector
    stages: tuple[CommandStage, ...]
    redirection: StdoutRedirection | None = None

    def __post_init__(self) -> None:
        if not self.stages:
            raise ValueError("plan unit stages must not be empty")


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """A fully parsed plan that is safe to dispatch."""

    units: tuple[PlanUnit, ...]

    def __post_init__(self) -> None:
        if not self.units:
            raise ValueError("execution plan units must not be empty")


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
