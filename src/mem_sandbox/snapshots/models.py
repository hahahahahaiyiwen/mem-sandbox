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
class SandboxSnapshot:
    snapshot_id: SnapshotId
    schema_version: int
    created_at: datetime
    source_session_id: SessionId
    workspace_revision: Revision
    content_hash: ContentHash
    payload: bytes
    metadata: SnapshotMetadata

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if self.created_at.utcoffset() != timedelta(0):
            raise ValueError("created_at must use UTC")


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
