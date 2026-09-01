"""Immutable requests, results, limits, and value objects for workspace behavior."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from mem_sandbox.core.identifiers import Revision
from mem_sandbox.workspace.paths import SandboxPath


@dataclass(frozen=True, slots=True, order=True)
class ContentHash:
    """A lowercase SHA-256 digest."""

    value: str

    def __post_init__(self) -> None:
        _require_text("content hash", self.value)
        if len(self.value) != 64 or any(
            character not in "0123456789abcdef" for character in self.value
        ):
            raise ValueError("content hash must be a lowercase SHA-256 digest")

    @classmethod
    def from_bytes(cls, content: bytes) -> ContentHash:
        """Hash immutable content bytes."""
        _require_bytes("content", content)
        return cls(sha256(content).hexdigest())

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True, kw_only=True)
class WorkspaceLimits:
    """Configurable authoritative workspace limits."""

    max_file_bytes: int = 4 * 1024 * 1024
    max_total_bytes: int = 16 * 1024 * 1024
    max_nodes: int = 10_000
    max_path_bytes: int = 4_096
    max_segment_bytes: int = 255
    max_read_bytes: int = 256 * 1024
    max_read_lines: int = 2_000
    max_patch_bytes: int = 1024 * 1024
    max_snapshot_bytes: int = 32 * 1024 * 1024

    def __post_init__(self) -> None:
        for name in (
            "max_file_bytes",
            "max_total_bytes",
            "max_nodes",
            "max_path_bytes",
            "max_segment_bytes",
            "max_read_bytes",
            "max_read_lines",
            "max_patch_bytes",
            "max_snapshot_bytes",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value <= 0:
                raise ValueError(f"{name} must be positive")

        if self.max_file_bytes > self.max_total_bytes:
            raise ValueError("max_file_bytes must not exceed max_total_bytes")
        if self.max_segment_bytes > self.max_path_bytes:
            raise ValueError("max_segment_bytes must not exceed max_path_bytes")
        if self.max_path_bytes < len(SandboxPath.ROOT.encode("utf-8")):
            raise ValueError("max_path_bytes must accommodate the workspace root")
        if self.max_segment_bytes < len(b"workspace"):
            raise ValueError("max_segment_bytes must accommodate the workspace root")


@dataclass(frozen=True, slots=True)
class AnyCurrentState:
    """Allow an intentional unconditional create or replacement."""


@dataclass(frozen=True, slots=True)
class PathMustNotExist:
    """Require the destination path to be absent."""


@dataclass(frozen=True, slots=True)
class ContentHashMustEqual:
    """Require an existing file to have the observed content hash."""

    expected_hash: ContentHash

    def __post_init__(self) -> None:
        _require_content_hash("expected_hash", self.expected_hash)


type WritePrecondition = AnyCurrentState | PathMustNotExist | ContentHashMustEqual


class NodeKind(StrEnum):
    """Supported workspace node types."""

    FILE = "file"
    DIRECTORY = "directory"


@dataclass(frozen=True, slots=True)
class WorkspaceStats:
    """Committed workspace counters and root identity."""

    total_bytes: int
    node_count: int
    revision: Revision
    root_hash: ContentHash


@dataclass(frozen=True, slots=True)
class WorkspaceEntry:
    """Immutable metadata view for a file or directory."""

    path: SandboxPath
    kind: NodeKind
    size_bytes: int
    content_hash: ContentHash
    revision: Revision


@dataclass(frozen=True, slots=True)
class WorkspaceBinaryResult:
    """A complete binary file read."""

    path: SandboxPath
    content: bytes
    content_hash: ContentHash
    revision: Revision


@dataclass(frozen=True, slots=True)
class WorkspaceTextResult:
    """A complete UTF-8 file read."""

    path: SandboxPath
    content: str
    content_hash: ContentHash
    revision: Revision


@dataclass(frozen=True, slots=True)
class WorkspaceRangeRequest:
    """A one-based inclusive text range request."""

    path: SandboxPath
    start_line: int = 1
    end_line: int | None = None

    def __post_init__(self) -> None:
        _require_path("path", self.path)
        _require_integer("start_line", self.start_line)
        if self.start_line < 1:
            raise ValueError("start_line must be at least 1")
        if self.end_line is not None:
            _require_integer("end_line", self.end_line)
            if self.end_line < self.start_line:
                raise ValueError("end_line must not be before start_line")


@dataclass(frozen=True, slots=True)
class WorkspaceRangeResult:
    """A bounded materialized text range from one revision."""

    path: SandboxPath
    content: str
    start_line: int
    end_line: int
    total_lines: int
    content_hash: ContentHash
    revision: Revision


@dataclass(frozen=True, slots=True)
class MakeDirectoryRequest:
    """Create one directory, optionally including missing parents."""

    path: SandboxPath
    create_parents: bool = False

    def __post_init__(self) -> None:
        _require_path("path", self.path)
        _require_boolean("create_parents", self.create_parents)


@dataclass(frozen=True, slots=True)
class WorkspaceWriteRequest:
    """Atomically create or replace one complete file."""

    path: SandboxPath
    content: bytes
    precondition: WritePrecondition
    create_parents: bool = False

    def __post_init__(self) -> None:
        _require_path("path", self.path)
        _require_bytes("content", self.content)
        _require_write_precondition(self.precondition)
        _require_boolean("create_parents", self.create_parents)


@dataclass(frozen=True, slots=True)
class WorkspaceAppendRequest:
    """Atomically append bytes to one file, creating it when permitted."""

    path: SandboxPath
    content: bytes
    precondition: WritePrecondition
    create_parents: bool = False

    def __post_init__(self) -> None:
        _require_path("path", self.path)
        _require_bytes("content", self.content)
        _require_write_precondition(self.precondition)
        _require_boolean("create_parents", self.create_parents)


@dataclass(frozen=True, slots=True)
class RemovePathRequest:
    """Remove a file or directory."""

    path: SandboxPath
    recursive: bool = False
    missing_ok: bool = False

    def __post_init__(self) -> None:
        _require_path("path", self.path)
        _require_boolean("recursive", self.recursive)
        _require_boolean("missing_ok", self.missing_ok)


@dataclass(frozen=True, slots=True)
class CopyPathRequest:
    """Atomically copy a file or directory to an exact destination."""

    source: SandboxPath
    destination: SandboxPath
    overwrite: bool = False
    create_parents: bool = False

    def __post_init__(self) -> None:
        _require_path("source", self.source)
        _require_path("destination", self.destination)
        _require_boolean("overwrite", self.overwrite)
        _require_boolean("create_parents", self.create_parents)


@dataclass(frozen=True, slots=True)
class MovePathRequest:
    """Atomically move a file or directory to an exact destination."""

    source: SandboxPath
    destination: SandboxPath
    overwrite: bool = False
    create_parents: bool = False

    def __post_init__(self) -> None:
        _require_path("source", self.source)
        _require_path("destination", self.destination)
        _require_boolean("overwrite", self.overwrite)
        _require_boolean("create_parents", self.create_parents)


@dataclass(frozen=True, slots=True)
class WorkspaceMutation:
    """The committed outcome of one path mutation."""

    path: SandboxPath
    created: bool
    changed: bool
    previous_hash: ContentHash | None
    current_hash: ContentHash | None
    stats: WorkspaceStats


@dataclass(frozen=True, slots=True)
class ExpectedFileHash:
    """A file-specific stale-content precondition for a patch."""

    path: SandboxPath
    content_hash: ContentHash

    def __post_init__(self) -> None:
        _require_path("path", self.path)
        _require_content_hash("content_hash", self.content_hash)


@dataclass(frozen=True, slots=True)
class WorkspacePatchRequest:
    """Apply one constrained unified diff atomically."""

    patch: str
    expected_hashes: tuple[ExpectedFileHash, ...] = ()

    def __post_init__(self) -> None:
        _require_text("patch", self.patch)
        seen_paths: set[SandboxPath] = set()
        for item in self.expected_hashes:
            _require_expected_file_hash(item)
            if item.path in seen_paths:
                raise ValueError("expected_hashes must not contain duplicate paths")
            seen_paths.add(item.path)


@dataclass(frozen=True, slots=True)
class PatchedFile:
    """Hash transition for one file in an atomic patch."""

    path: SandboxPath
    previous_hash: ContentHash
    current_hash: ContentHash


@dataclass(frozen=True, slots=True)
class WorkspacePatchResult:
    """The committed outcome of a multi-file patch."""

    files: tuple[PatchedFile, ...]
    stats: WorkspaceStats


@dataclass(frozen=True, slots=True)
class WorkspaceSnapshotData:
    """Opaque deterministic workspace snapshot bytes and verified metadata."""

    encoded: bytes
    schema_version: int
    integrity_hash: ContentHash
    workspace_revision: Revision
    root_hash: ContentHash

    def __post_init__(self) -> None:
        _require_bytes("encoded", self.encoded)
        _require_integer("schema_version", self.schema_version)
        _require_content_hash("integrity_hash", self.integrity_hash)
        _require_revision("workspace_revision", self.workspace_revision)
        _require_content_hash("root_hash", self.root_hash)


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _require_bytes(name: str, value: object) -> bytes:
    if not isinstance(value, bytes):
        raise TypeError(f"{name} must be bytes")
    return value


def _require_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    return value


def _require_boolean(name: str, value: object) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{name} must be a boolean")
    return value


def _require_content_hash(name: str, value: object) -> ContentHash:
    if not isinstance(value, ContentHash):
        raise TypeError(f"{name} must be a ContentHash")
    return value


def _require_path(name: str, value: object) -> SandboxPath:
    if not isinstance(value, SandboxPath):
        raise TypeError(f"{name} must be a SandboxPath")
    return value


def _require_write_precondition(value: object) -> WritePrecondition:
    if not isinstance(
        value,
        AnyCurrentState | PathMustNotExist | ContentHashMustEqual,
    ):
        raise TypeError("precondition must be a supported write precondition")
    return value


def _require_expected_file_hash(value: object) -> ExpectedFileHash:
    if not isinstance(value, ExpectedFileHash):
        raise TypeError("expected_hashes must contain ExpectedFileHash values")
    return value


def _require_revision(name: str, value: object) -> Revision:
    if not isinstance(value, Revision):
        raise TypeError(f"{name} must be a Revision")
    return value
