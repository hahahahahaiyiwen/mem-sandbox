"""OpenAI snapshot bridge backed by the public MemSandbox snapshot store."""

from __future__ import annotations

import base64
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from agents.sandbox.errors import SnapshotPersistError, SnapshotRestoreError
from agents.sandbox.session import Dependencies
from agents.sandbox.snapshot import SnapshotBase, SnapshotSpec
from pydantic import field_validator

from mem_sandbox.core import Clock, Revision, SessionId, SnapshotId
from mem_sandbox.service import FactorySnapshotStore
from mem_sandbox.snapshots import (
    SandboxSnapshot,
    SandboxSnapshotDraft,
    SnapshotCorrupt,
    SnapshotIncompatible,
    SnapshotMetadata,
    SnapshotNotFound,
    SnapshotRef,
)
from mem_sandbox.workspace import ContentHash, WorkspaceArchiveData

_SNAPSHOT_TYPE = "mem_sandbox_store"
_SNAPSHOT_STORE_DEPENDENCY_KEY = "mem_sandbox.snapshot_store"
_SNAPSHOT_FORMAT = "openai-portable-workspace"
_SNAPSHOT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class _SnapshotStoreRuntime:
    store: FactorySnapshotStore
    clock: Clock


class WorkspaceArchiveStream(io.BytesIO):
    def __init__(
        self,
        archive: WorkspaceArchiveData,
        *,
        source_session_id: SessionId,
        owner_id: str,
    ) -> None:
        super().__init__(archive.encoded)
        self.archive = archive
        self.source_session_id = source_session_id
        self.owner_id = owner_id


class InMemorySandboxSnapshot(SnapshotBase):
    """Serializable OpenAI snapshot backed by a runtime MemSandbox store."""

    type: Literal["mem_sandbox_store"] = _SNAPSHOT_TYPE  # pyright: ignore[reportIncompatibleVariableOverride]
    store_dependency_key: Literal["mem_sandbox.snapshot_store"] = _SNAPSHOT_STORE_DEPENDENCY_KEY
    owner_id: str | None = None

    @field_validator("id")
    @classmethod
    def _validate_snapshot_id(cls, value: str) -> str:
        SnapshotId.parse(value)
        return value

    @field_validator("owner_id")
    @classmethod
    def _validate_owner_id(cls, value: str | None) -> str | None:
        if value is not None and not value:
            raise ValueError("owner_id must not be empty")
        return value

    async def persist(
        self,
        data: io.IOBase,
        *,
        dependencies: Dependencies | None = None,
    ) -> None:
        try:
            stream = _require_archive_stream(data)
            archive_bytes = stream.read()
            if archive_bytes != stream.archive.encoded:
                raise ValueError("workspace archive stream does not match its metadata")
            runtime = await _require_store_runtime(dependencies, self.store_dependency_key)
            snapshot_id = SnapshotId.parse(self.id)
            payload = _encode_archive_envelope(stream.archive)
            snapshot_ref = await runtime.store.save(
                SandboxSnapshotDraft(
                    snapshot_id=snapshot_id,
                    schema_version=_SNAPSHOT_SCHEMA_VERSION,
                    created_at=runtime.clock.now(),
                    source_session_id=stream.source_session_id,
                    workspace_revision=stream.archive.workspace_revision,
                    content_hash=ContentHash.from_bytes(payload),
                    payload=payload,
                    metadata=SnapshotMetadata(
                        format_name=_SNAPSHOT_FORMAT,
                        payload_bytes=len(payload),
                        process_local=runtime.store.process_local,
                    ),
                    created_by=_require_owner_id(self.owner_id, stream.owner_id),
                )
            )
            if snapshot_ref.snapshot_id != snapshot_id:
                raise ValueError("snapshot store returned an unexpected snapshot reference")
        except Exception as error:
            raise SnapshotPersistError(
                snapshot_id=self.id,
                path=_snapshot_path(self.store_dependency_key, self.id),
                cause=error,
            ) from error

    async def restore(self, *, dependencies: Dependencies | None = None) -> io.IOBase:
        try:
            snapshot = await _load_snapshot(
                dependencies,
                dependency_key=self.store_dependency_key,
                snapshot_id=self.id,
                owner_id=self.owner_id,
            )
            archive = _decode_archive_envelope(snapshot)
            return WorkspaceArchiveStream(
                archive,
                source_session_id=snapshot.source_session_id,
                owner_id=cast(str, snapshot.created_by),
            )
        except Exception as error:
            raise SnapshotRestoreError(
                snapshot_id=self.id,
                path=_snapshot_path(self.store_dependency_key, self.id),
                cause=error,
            ) from error

    async def restorable(self, *, dependencies: Dependencies | None = None) -> bool:
        try:
            await _load_snapshot(
                dependencies,
                dependency_key=self.store_dependency_key,
                snapshot_id=self.id,
                owner_id=self.owner_id,
            )
        except SnapshotNotFound:
            return False
        return True


class InMemorySandboxSnapshotSpec(SnapshotSpec):
    """Build an OpenAI snapshot that resolves the configured MemSandbox store."""

    type: Literal["mem_sandbox_store"] = _SNAPSHOT_TYPE  # pyright: ignore[reportIncompatibleVariableOverride]
    store_dependency_key: Literal["mem_sandbox.snapshot_store"] = _SNAPSHOT_STORE_DEPENDENCY_KEY

    def build(self, snapshot_id: str) -> SnapshotBase:
        return InMemorySandboxSnapshot(
            id=snapshot_id,
            store_dependency_key=self.store_dependency_key,
        )


def configure_snapshot_dependencies(
    dependencies: Dependencies | None,
    *,
    snapshot_store: FactorySnapshotStore | None,
    clock: Clock | None,
) -> Dependencies | None:
    if snapshot_store is None:
        if clock is not None:
            raise ValueError("clock requires snapshot_store")
        return dependencies
    if clock is None:
        raise ValueError("snapshot_store requires clock")
    configured = dependencies.clone() if dependencies is not None else Dependencies()
    configured.bind_value(
        _SNAPSHOT_STORE_DEPENDENCY_KEY,
        _SnapshotStoreRuntime(store=snapshot_store, clock=clock),
    )
    return configured


async def _require_store_runtime(
    dependencies: Dependencies | None,
    dependency_key: str,
) -> _SnapshotStoreRuntime:
    if dependencies is None:
        raise RuntimeError(f"InMemorySandboxSnapshot requires dependency `{dependency_key}`")
    runtime = await dependencies.require(
        dependency_key,
        consumer="InMemorySandboxSnapshot",
    )
    if not isinstance(runtime, _SnapshotStoreRuntime):
        raise TypeError(
            f"dependency `{dependency_key}` must be configured by InMemorySandboxClient"
        )
    return runtime


async def _load_snapshot(
    dependencies: Dependencies | None,
    *,
    dependency_key: str,
    snapshot_id: str,
    owner_id: str | None,
) -> SandboxSnapshot:
    runtime = await _require_store_runtime(dependencies, dependency_key)
    snapshot = await runtime.store.load(SnapshotRef(SnapshotId.parse(snapshot_id)))
    if snapshot.schema_version != _SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotIncompatible(
            f"OpenAI workspace snapshot schema version {snapshot.schema_version} is unsupported"
        )
    if snapshot.metadata.format_name != _SNAPSHOT_FORMAT:
        raise SnapshotIncompatible(
            f"snapshot format {snapshot.metadata.format_name} is unsupported"
        )
    if snapshot.metadata.payload_bytes != len(snapshot.payload):
        raise SnapshotCorrupt("snapshot metadata payload size does not match payload")
    if snapshot.content_hash != ContentHash.from_bytes(snapshot.payload):
        raise SnapshotCorrupt("snapshot content hash does not match payload")
    if owner_id is None:
        raise SnapshotIncompatible("snapshot owner is not bound")
    if snapshot.created_by != owner_id:
        raise SnapshotIncompatible("snapshot owner does not match provider state")
    return snapshot


def _require_archive_stream(data: io.IOBase) -> WorkspaceArchiveStream:
    if not isinstance(data, WorkspaceArchiveStream):
        raise TypeError(
            "InMemorySandboxSnapshot requires a MemSandbox portable workspace archive stream"
        )
    return data


def _snapshot_path(dependency_key: str, snapshot_id: str) -> Path:
    return Path(f"<mem-sandbox-store:{dependency_key}/{snapshot_id}>")


def _require_owner_id(snapshot_owner_id: str | None, stream_owner_id: str) -> str:
    if snapshot_owner_id is None:
        raise ValueError("snapshot owner is not bound")
    if snapshot_owner_id != stream_owner_id:
        raise ValueError("snapshot owner does not match workspace owner")
    return snapshot_owner_id


def _encode_archive_envelope(archive: WorkspaceArchiveData) -> bytes:
    return json.dumps(
        {
            "archive_base64": base64.b64encode(archive.encoded).decode("ascii"),
            "archive_format_version": archive.format_version,
            "archive_root_hash": archive.root_hash.value,
            "workspace_revision": archive.workspace_revision.value,
        },
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _decode_archive_envelope(snapshot: SandboxSnapshot) -> WorkspaceArchiveData:
    try:
        decoded: object = json.loads(snapshot.payload.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise ValueError("snapshot archive envelope has an invalid shape")
        value = cast(dict[str, object], decoded)
        if set(value) != {
            "archive_base64",
            "archive_format_version",
            "archive_root_hash",
            "workspace_revision",
        }:
            raise ValueError("snapshot archive envelope has an invalid shape")
        archive_base64 = value["archive_base64"]
        archive_format_version = value["archive_format_version"]
        archive_root_hash = value["archive_root_hash"]
        workspace_revision = value["workspace_revision"]
        if not isinstance(archive_base64, str):
            raise TypeError("snapshot archive payload must be base64 text")
        if isinstance(archive_format_version, bool) or not isinstance(archive_format_version, int):
            raise TypeError("snapshot archive format version must be an integer")
        if isinstance(workspace_revision, bool) or not isinstance(workspace_revision, int):
            raise TypeError("snapshot workspace revision must be an integer")
        if not isinstance(archive_root_hash, str):
            raise TypeError("snapshot archive root hash must be a string")
        archive = WorkspaceArchiveData(
            encoded=base64.b64decode(archive_base64, validate=True),
            format_version=archive_format_version,
            workspace_revision=Revision(workspace_revision),
            root_hash=ContentHash(archive_root_hash),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as error:
        raise SnapshotCorrupt("snapshot archive envelope is invalid") from error
    if snapshot.workspace_revision != archive.workspace_revision:
        raise SnapshotCorrupt("snapshot workspace revision does not match archive envelope")
    return archive
