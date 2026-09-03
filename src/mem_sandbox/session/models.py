"""Session-owned public requests, results, and lifecycle values."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import cast

from mem_sandbox.command_executor import (
    CancellationSignal,
    CommandFailureCode,
    CommandLimits,
    EnvironmentChange,
    EnvironmentValue,
)
from mem_sandbox.core import OperationLimits, OperationResultMetadata
from mem_sandbox.secrets import SecretRef
from mem_sandbox.snapshots import SnapshotRef
from mem_sandbox.workspace import (
    AnyCurrentState,
    ContentHash,
    ExpectedFileHash,
    NodeKind,
    PatchedFile,
    SandboxPath,
    WorkspaceEntry,
    WritePrecondition,
)


class SandboxSessionState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


SessionState = SandboxSessionState


@dataclass(frozen=True, slots=True)
class SessionSecretEnvironmentBinding:
    name: str
    secret_ref: SecretRef

    def __post_init__(self) -> None:
        EnvironmentValue(self.name, "")
        if self.name == "PWD":
            raise ValueError("PWD cannot be used as a secret environment binding")
        if not isinstance(cast(object, self.secret_ref), SecretRef):
            raise TypeError("secret_ref must be a SecretRef")


@dataclass(frozen=True, slots=True)
class SessionExpectedFileHash:
    path: str
    content_hash: ContentHash


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionExecuteRequest:
    command: str
    secret_environment: tuple[SessionSecretEnvironmentBinding, ...] = ()
    command_limits: CommandLimits = field(default_factory=CommandLimits)
    limits: OperationLimits = field(default_factory=OperationLimits)
    cancellation: CancellationSignal | None = None

    def __post_init__(self) -> None:
        _require_text("command", self.command)
        if not isinstance(cast(object, self.command_limits), CommandLimits):
            raise TypeError("command_limits must be CommandLimits")
        if not isinstance(cast(object, self.secret_environment), tuple):
            raise TypeError("secret_environment must be a tuple")
        for binding in self.secret_environment:
            if not isinstance(cast(object, binding), SessionSecretEnvironmentBinding):
                raise TypeError(
                    "secret_environment must contain SessionSecretEnvironmentBinding values"
                )
        names = tuple(binding.name for binding in self.secret_environment)
        if len(set(names)) != len(names):
            raise ValueError("secret_environment contains duplicate environment names")
        if len(self.secret_environment) > self.command_limits.max_secret_bindings:
            raise ValueError(
                "secret_environment exceeds command_limits.max_secret_bindings"
            )
        unique_refs = {binding.secret_ref for binding in self.secret_environment}
        if len(unique_refs) > self.command_limits.max_secret_bindings:
            raise ValueError(
                "secret_environment references exceed command_limits.max_secret_bindings"
            )
        object.__setattr__(
            self,
            "secret_environment",
            tuple(sorted(self.secret_environment, key=lambda binding: binding.name)),
        )


@dataclass(frozen=True, slots=True)
class SessionExecuteResult:
    metadata: OperationResultMetadata
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
    environment_changes: tuple[EnvironmentChange, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadFileRequest:
    path: str
    start_line: int = 1
    end_line: int | None = None
    limits: OperationLimits = field(default_factory=OperationLimits)
    cancellation: CancellationSignal | None = None

    def __post_init__(self) -> None:
        _require_text("path", self.path)
        _require_line_range(self.start_line, self.end_line)


@dataclass(frozen=True, slots=True)
class ReadFileResult:
    metadata: OperationResultMetadata
    path: SandboxPath
    content: str
    start_line: int
    end_line: int
    total_lines: int
    content_hash: ContentHash


@dataclass(frozen=True, slots=True, kw_only=True)
class WriteFileRequest:
    path: str
    content: str
    precondition: WritePrecondition = field(default_factory=AnyCurrentState)
    create_parents: bool = False
    limits: OperationLimits = field(default_factory=OperationLimits)
    cancellation: CancellationSignal | None = None

    def __post_init__(self) -> None:
        _require_text("path", self.path)
        _require_utf8("content", self.content)


@dataclass(frozen=True, slots=True, kw_only=True)
class WriteBytesRequest:
    path: str
    content: bytes
    precondition: WritePrecondition = field(default_factory=AnyCurrentState)
    create_parents: bool = False
    limits: OperationLimits = field(default_factory=OperationLimits)
    cancellation: CancellationSignal | None = None

    def __post_init__(self) -> None:
        _require_text("path", self.path)
        content = cast(object, self.content)
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")


@dataclass(frozen=True, slots=True)
class FileMutationResult:
    metadata: OperationResultMetadata
    path: SandboxPath
    created: bool
    changed: bool
    previous_hash: ContentHash | None
    current_hash: ContentHash | None


@dataclass(frozen=True, slots=True, kw_only=True)
class ApplyPatchRequest:
    patch: str
    expected_hashes: tuple[SessionExpectedFileHash | ExpectedFileHash, ...] = ()
    limits: OperationLimits = field(default_factory=OperationLimits)
    cancellation: CancellationSignal | None = None

    def __post_init__(self) -> None:
        _require_utf8("patch", self.patch)


@dataclass(frozen=True, slots=True)
class PatchMutationResult:
    metadata: OperationResultMetadata
    files: tuple[PatchedFile, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReadBytesRequest:
    path: str
    limits: OperationLimits = field(default_factory=OperationLimits)
    cancellation: CancellationSignal | None = None

    def __post_init__(self) -> None:
        _require_text("path", self.path)


@dataclass(frozen=True, slots=True)
class ReadBytesResult:
    metadata: OperationResultMetadata
    path: SandboxPath
    content: bytes
    content_hash: ContentHash


@dataclass(frozen=True, slots=True, kw_only=True)
class StatRequest:
    path: str
    limits: OperationLimits = field(default_factory=OperationLimits)
    cancellation: CancellationSignal | None = None

    def __post_init__(self) -> None:
        _require_text("path", self.path)


@dataclass(frozen=True, slots=True)
class StatResult:
    metadata: OperationResultMetadata
    entry: WorkspaceEntry

    @property
    def path(self) -> SandboxPath:
        return self.entry.path

    @property
    def kind(self) -> NodeKind:
        return self.entry.kind


@dataclass(frozen=True, slots=True, kw_only=True)
class ListEntriesRequest:
    path: str = "."
    limits: OperationLimits = field(default_factory=OperationLimits)
    cancellation: CancellationSignal | None = None

    def __post_init__(self) -> None:
        _require_text("path", self.path)


@dataclass(frozen=True, slots=True)
class ListEntriesResult:
    metadata: OperationResultMetadata
    entries: tuple[WorkspaceEntry, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateSnapshotRequest:
    limits: OperationLimits = field(default_factory=OperationLimits)
    cancellation: CancellationSignal | None = None


@dataclass(frozen=True, slots=True)
class CreateSnapshotResult:
    metadata: OperationResultMetadata
    snapshot_ref: SnapshotRef
    content_hash: ContentHash


@dataclass(frozen=True, slots=True, kw_only=True)
class RestoreSnapshotRequest:
    snapshot_ref: SnapshotRef
    limits: OperationLimits = field(default_factory=OperationLimits)
    cancellation: CancellationSignal | None = None


@dataclass(frozen=True, slots=True)
class RestoreSnapshotResult:
    metadata: OperationResultMetadata
    snapshot_ref: SnapshotRef


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _require_utf8(name: str, value: object) -> str:
    text = _require_text(name, value)
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError(f"{name} must be valid UTF-8") from error
    return text


def _require_line_range(start_line: object, end_line: object) -> None:
    if isinstance(start_line, bool) or not isinstance(start_line, int):
        raise TypeError("start_line must be an integer")
    if start_line < 1:
        raise ValueError("start_line must be at least 1")
    if end_line is not None:
        if isinstance(end_line, bool) or not isinstance(end_line, int):
            raise TypeError("end_line must be an integer")
        if end_line < start_line:
            raise ValueError("end_line must not be before start_line")
