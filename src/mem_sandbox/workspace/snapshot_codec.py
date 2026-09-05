"""Canonical JSON workspace snapshot codec."""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Callable
from typing import cast

from mem_sandbox.core.errors import SandboxError
from mem_sandbox.core.identifiers import Revision
from mem_sandbox.workspace.errors import (
    SnapshotCorruptError,
    SnapshotIncompatibleError,
    SnapshotTooLargeError,
)
from mem_sandbox.workspace.models import (
    ContentHash,
    DecodedWorkspaceSnapshot,
    NodeKind,
    WorkspaceLimits,
    WorkspaceSnapshotData,
    WorkspaceSnapshotEntry,
    WorkspaceStats,
)
from mem_sandbox.workspace.paths import SandboxPath
from mem_sandbox.workspace.snapshot_tree import measure_workspace_entries


class JsonWorkspaceSnapshotCodec:
    """Version 1 canonical JSON/base64 snapshot codec."""

    SCHEMA_VERSION = 1

    def encode(
        self,
        entries: tuple[WorkspaceSnapshotEntry, ...],
        stats: WorkspaceStats,
        limits: WorkspaceLimits,
    ) -> WorkspaceSnapshotData:
        """Encode verified workspace entries deterministically."""
        serialized_entries: list[dict[str, object]] = []
        for entry in sorted(entries, key=lambda item: item.path.value):
            if entry.kind is NodeKind.DIRECTORY:
                serialized_entries.append(
                    {
                        "kind": NodeKind.DIRECTORY.value,
                        "path": entry.path.value,
                    }
                )
                continue
            assert entry.content is not None
            serialized_entries.append(
                {
                    "content_base64": base64.b64encode(entry.content).decode("ascii"),
                    "content_hash": ContentHash.from_bytes(entry.content).value,
                    "kind": NodeKind.FILE.value,
                    "path": entry.path.value,
                }
            )

        payload: dict[str, object] = {
            "entries": serialized_entries,
            "node_count": stats.node_count,
            "revision": stats.revision.value,
            "root_hash": stats.root_hash.value,
            "schema_version": self.SCHEMA_VERSION,
            "total_bytes": stats.total_bytes,
        }
        payload_bytes = _canonical_json(payload)
        integrity_hash = ContentHash.from_bytes(payload_bytes)
        envelope = {
            "integrity": {
                "algorithm": "sha256",
                "digest": integrity_hash.value,
            },
            "payload": payload,
        }
        encoded = _canonical_json(envelope)
        if len(encoded) > limits.max_snapshot_bytes:
            raise SnapshotTooLargeError(
                f"snapshot contains {len(encoded)} bytes; limit is "
                f"{limits.max_snapshot_bytes} bytes"
            )
        return WorkspaceSnapshotData(
            encoded=encoded,
            schema_version=self.SCHEMA_VERSION,
            integrity_hash=integrity_hash,
            workspace_revision=stats.revision,
            root_hash=stats.root_hash,
        )

    def decode(
        self,
        data: WorkspaceSnapshotData,
        limits: WorkspaceLimits,
        resolve_path: Callable[[str], SandboxPath],
    ) -> DecodedWorkspaceSnapshot:
        """Decode, bound, and verify snapshot bytes."""
        if len(data.encoded) > limits.max_snapshot_bytes:
            raise SnapshotTooLargeError(
                f"snapshot contains {len(data.encoded)} bytes; limit is "
                f"{limits.max_snapshot_bytes} bytes"
            )

        envelope = _load_json_object(data.encoded)
        _require_exact_keys(envelope, {"integrity", "payload"}, "snapshot envelope")
        integrity = _require_object(envelope["integrity"], "snapshot integrity")
        _require_exact_keys(integrity, {"algorithm", "digest"}, "snapshot integrity")
        if integrity["algorithm"] != "sha256":
            raise SnapshotCorruptError("snapshot integrity algorithm must be sha256")
        digest = _parse_hash(integrity["digest"], "snapshot integrity digest")
        if data.integrity_hash != digest:
            raise SnapshotCorruptError(
                "snapshot metadata integrity hash does not match encoded bytes"
            )

        payload = _require_object(envelope["payload"], "snapshot payload")
        payload_bytes = _canonical_json(payload)
        if ContentHash.from_bytes(payload_bytes) != digest:
            raise SnapshotCorruptError("snapshot integrity digest does not match payload")

        _require_exact_keys(
            payload,
            {
                "entries",
                "node_count",
                "revision",
                "root_hash",
                "schema_version",
                "total_bytes",
            },
            "snapshot payload",
        )
        schema_version = _require_integer(payload["schema_version"], "schema_version")
        if data.schema_version != schema_version:
            raise SnapshotCorruptError(
                "snapshot metadata schema version does not match encoded bytes"
            )
        if schema_version != self.SCHEMA_VERSION:
            raise SnapshotIncompatibleError(
                f"snapshot schema version {schema_version} is unsupported"
            )

        revision = Revision(_require_integer(payload["revision"], "revision"))
        declared_root_hash = _parse_hash(payload["root_hash"], "root_hash")
        if data.workspace_revision != revision:
            raise SnapshotCorruptError("snapshot metadata revision does not match encoded bytes")
        if data.root_hash != declared_root_hash:
            raise SnapshotCorruptError("snapshot metadata root hash does not match encoded bytes")
        declared_total_bytes = _require_integer(payload["total_bytes"], "total_bytes")
        declared_node_count = _require_integer(payload["node_count"], "node_count")
        entries_value = payload["entries"]
        if not isinstance(entries_value, list):
            raise SnapshotCorruptError("snapshot entries must be a list")
        entries_values = cast(list[object], entries_value)
        if len(entries_values) + 1 > limits.max_nodes:
            raise SnapshotTooLargeError(f"snapshot contains more than {limits.max_nodes} nodes")

        entries = self._decode_entries(entries_values, limits, resolve_path)
        measured = measure_workspace_entries(entries)
        if measured.total_bytes != declared_total_bytes:
            raise SnapshotCorruptError("snapshot total byte count does not match entries")
        if measured.node_count != declared_node_count:
            raise SnapshotCorruptError("snapshot node count does not match entries")
        if measured.root_hash != declared_root_hash:
            raise SnapshotCorruptError("snapshot root hash does not match entries")

        return DecodedWorkspaceSnapshot(
            entries=entries,
            stats=WorkspaceStats(
                total_bytes=measured.total_bytes,
                node_count=measured.node_count,
                revision=revision,
                root_hash=measured.root_hash,
            ),
        )

    def _decode_entries(
        self,
        values: list[object],
        limits: WorkspaceLimits,
        resolve_path: Callable[[str], SandboxPath],
    ) -> tuple[WorkspaceSnapshotEntry, ...]:
        entries: list[WorkspaceSnapshotEntry] = []
        previous_path: str | None = None
        total_bytes = 0

        for value in values:
            item = _require_object(value, "snapshot entry")
            kind_value = item.get("kind")
            if kind_value == NodeKind.DIRECTORY.value:
                _require_exact_keys(item, {"kind", "path"}, "directory snapshot entry")
                kind = NodeKind.DIRECTORY
                content = None
            elif kind_value == NodeKind.FILE.value:
                _require_exact_keys(
                    item,
                    {"content_base64", "content_hash", "kind", "path"},
                    "file snapshot entry",
                )
                kind = NodeKind.FILE
                content = _decode_file_content(item, limits)
                total_bytes += len(content)
                if total_bytes > limits.max_total_bytes:
                    raise SnapshotTooLargeError(
                        f"snapshot files contain more than {limits.max_total_bytes} bytes"
                    )
            else:
                raise SnapshotCorruptError("snapshot entry has an unsupported node kind")

            path_value = item["path"]
            if not isinstance(path_value, str):
                raise SnapshotCorruptError("snapshot entry path must be a string")
            try:
                path = resolve_path(path_value)
            except (SandboxError, TypeError, ValueError) as error:
                raise SnapshotCorruptError("snapshot entry contains an invalid path") from error
            if path.is_root:
                raise SnapshotCorruptError("snapshot entries must not contain the root")
            if previous_path is not None and path.value <= previous_path:
                raise SnapshotCorruptError("snapshot entries must be unique and lexically ordered")
            previous_path = path.value
            entries.append(WorkspaceSnapshotEntry(path=path, kind=kind, content=content))

        return tuple(entries)


def _decode_file_content(
    item: dict[str, object],
    limits: WorkspaceLimits,
) -> bytes:
    encoded_content = item["content_base64"]
    if not isinstance(encoded_content, str):
        raise SnapshotCorruptError("snapshot file content must be base64 text")
    try:
        content = base64.b64decode(encoded_content, validate=True)
    except (binascii.Error, ValueError) as error:
        raise SnapshotCorruptError("snapshot file content is not valid base64") from error
    if len(content) > limits.max_file_bytes:
        raise SnapshotTooLargeError(
            f"snapshot file contains {len(content)} bytes; limit is {limits.max_file_bytes} bytes"
        )
    if ContentHash.from_bytes(content) != _parse_hash(
        item["content_hash"],
        "snapshot file content_hash",
    ):
        raise SnapshotCorruptError("snapshot file content hash does not match content")
    return content


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError) as error:
        raise SnapshotCorruptError("snapshot contains non-serializable data") from error


def _load_json_object(encoded: bytes) -> dict[str, object]:
    try:
        value = json.loads(
            encoded.decode("utf-8"),
            object_pairs_hook=_unique_object,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKeyError) as error:
        raise SnapshotCorruptError("snapshot is not valid canonical JSON") from error
    return _require_object(value, "snapshot envelope")


class _DuplicateKeyError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _require_object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SnapshotCorruptError(f"{name} must be an object")
    return cast(dict[str, object], value)


def _require_exact_keys(
    value: dict[str, object],
    expected: set[str],
    name: str,
) -> None:
    if set(value) != expected:
        raise SnapshotCorruptError(f"{name} has missing or unsupported fields")


def _require_integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SnapshotCorruptError(f"{name} must be a non-negative integer")
    return value


def _parse_hash(value: object, name: str) -> ContentHash:
    if not isinstance(value, str):
        raise SnapshotCorruptError(f"{name} must be a SHA-256 digest")
    try:
        return ContentHash(value)
    except (TypeError, ValueError) as error:
        raise SnapshotCorruptError(f"{name} must be a SHA-256 digest") from error
