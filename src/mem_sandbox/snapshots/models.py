"""Immutable session snapshot values."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import cast

from mem_sandbox.command_executor import CommandEnvironment
from mem_sandbox.core import Revision, SessionId, SnapshotId
from mem_sandbox.workspace import ContentHash, SandboxPath, WorkspaceSnapshotData


@dataclass(frozen=True, slots=True)
class SnapshotRef:
    snapshot_id: SnapshotId


@dataclass(frozen=True, slots=True)
class SnapshotMetadata:
    format_name: str
    payload_bytes: int
    process_local: bool

    def __post_init__(self) -> None:
        format_name = cast(object, self.format_name)
        payload_bytes = cast(object, self.payload_bytes)
        process_local = cast(object, self.process_local)
        if not isinstance(format_name, str) or not format_name:
            raise ValueError("format_name must not be empty")
        if (
            isinstance(payload_bytes, bool)
            or not isinstance(payload_bytes, int)
            or payload_bytes < 0
        ):
            raise ValueError("payload_bytes must be non-negative")
        if not isinstance(process_local, bool):
            raise TypeError("process_local must be a boolean")


@dataclass(frozen=True, slots=True)
class SandboxSnapshotDraft:
    snapshot_id: SnapshotId
    schema_version: int
    created_at: datetime
    source_session_id: SessionId
    workspace_revision: Revision
    content_hash: ContentHash
    payload: bytes
    metadata: SnapshotMetadata
    created_by: str | None = None

    def __post_init__(self) -> None:
        _validate_snapshot_fields(
            self.snapshot_id,
            self.schema_version,
            self.source_session_id,
            self.workspace_revision,
            self.content_hash,
            self.payload,
            self.metadata,
        )
        _require_utc("created_at", self.created_at)
        _require_creator(self.created_by)


@dataclass(frozen=True, slots=True)
class SandboxSnapshot:
    snapshot_id: SnapshotId
    schema_version: int
    created_at: datetime
    expires_at: datetime
    source_session_id: SessionId
    workspace_revision: Revision
    content_hash: ContentHash
    payload: bytes
    metadata: SnapshotMetadata
    created_by: str | None

    def __post_init__(self) -> None:
        _validate_snapshot_fields(
            self.snapshot_id,
            self.schema_version,
            self.source_session_id,
            self.workspace_revision,
            self.content_hash,
            self.payload,
            self.metadata,
        )
        _require_utc("created_at", self.created_at)
        _require_utc("expires_at", self.expires_at)
        _require_creator(self.created_by)


@dataclass(frozen=True, slots=True, kw_only=True)
class SnapshotStoreLimits:
    max_snapshots: int
    max_total_payload_bytes: int

    def __post_init__(self) -> None:
        _require_positive_integer("max_snapshots", self.max_snapshots)
        _require_positive_integer(
            "max_total_payload_bytes",
            self.max_total_payload_bytes,
        )


@dataclass(frozen=True, slots=True)
class SnapshotStoreStats:
    snapshot_count: int
    payload_bytes: int


@dataclass(frozen=True, slots=True)
class SnapshotPurgeResult:
    removed_refs: tuple[SnapshotRef, ...]
    removed_payload_bytes: int


@dataclass(frozen=True, slots=True)
class SessionSnapshotState:
    workspace: WorkspaceSnapshotData
    cwd: SandboxPath
    approved_environment: CommandEnvironment
    schema_version: int = 1
    capability_profile_version: int = 1

    def __post_init__(self) -> None:
        workspace = cast(object, self.workspace)
        cwd = cast(object, self.cwd)
        environment = cast(object, self.approved_environment)
        schema_version = cast(object, self.schema_version)
        capability_version = cast(object, self.capability_profile_version)
        if not isinstance(workspace, WorkspaceSnapshotData):
            raise TypeError("workspace must be WorkspaceSnapshotData")
        if not isinstance(cwd, SandboxPath):
            raise TypeError("cwd must be SandboxPath")
        if not isinstance(environment, CommandEnvironment):
            raise TypeError("approved_environment must be CommandEnvironment")
        if (
            isinstance(schema_version, bool)
            or not isinstance(schema_version, int)
            or schema_version < 0
        ):
            raise ValueError("schema_version must be non-negative")
        if (
            isinstance(capability_version, bool)
            or not isinstance(capability_version, int)
            or capability_version < 0
        ):
            raise ValueError("capability_profile_version must be non-negative")


@dataclass(frozen=True, slots=True)
class SnapshotPayload:
    payload: bytes
    content_hash: ContentHash
    format_name: str
    schema_version: int
    workspace_revision: Revision


def _require_utc(name: str, value: datetime) -> None:
    datetime_value = cast(object, value)
    if not isinstance(datetime_value, datetime):
        raise TypeError(f"{name} must be a datetime")
    if datetime_value.tzinfo is None or datetime_value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    if datetime_value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must use UTC")


def _require_creator(value: str | None) -> None:
    creator = cast(object, value)
    if creator is None:
        return
    if not isinstance(creator, str):
        raise TypeError("created_by must be a string or None")
    try:
        size = len(creator.encode("utf-8"))
    except UnicodeEncodeError as error:
        raise ValueError("created_by must be valid UTF-8") from error
    if not creator or size > 256:
        raise ValueError("created_by must contain between 1 and 256 UTF-8 bytes")


def _require_positive_integer(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _validate_snapshot_fields(
    snapshot_id: object,
    schema_version: object,
    source_session_id: object,
    workspace_revision: object,
    content_hash: object,
    payload: object,
    metadata: object,
) -> None:
    if not isinstance(snapshot_id, SnapshotId):
        raise TypeError("snapshot_id must be SnapshotId")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise TypeError("schema_version must be an integer")
    if schema_version < 0:
        raise ValueError("schema_version must be non-negative")
    if not isinstance(source_session_id, SessionId):
        raise TypeError("source_session_id must be SessionId")
    if not isinstance(workspace_revision, Revision):
        raise TypeError("workspace_revision must be Revision")
    if not isinstance(content_hash, ContentHash):
        raise TypeError("content_hash must be ContentHash")
    if not isinstance(payload, bytes):
        raise TypeError("payload must be bytes")
    if not isinstance(metadata, SnapshotMetadata):
        raise TypeError("metadata must be SnapshotMetadata")
