"""Deterministic canonical JSON session snapshot codec."""

from __future__ import annotations

import base64
import binascii
import json
from typing import cast

from mem_sandbox.command_executor import CommandEnvironment, EnvironmentValue
from mem_sandbox.core import Revision
from mem_sandbox.snapshots.errors import (
    SnapshotCorrupt,
    SnapshotIncompatible,
    SnapshotTooLarge,
)
from mem_sandbox.snapshots.models import (
    SandboxSnapshot,
    SessionSnapshotState,
    SnapshotPayload,
)
from mem_sandbox.workspace import ContentHash, SandboxPath, WorkspaceSnapshotData


class JsonSessionSnapshotCodec:
    """Pure version 1 deterministic session-state codec."""

    SCHEMA_VERSION = 1
    CAPABILITY_PROFILE_VERSION = 1
    DEFAULT_MAX_PAYLOAD_BYTES = 64 * 1024 * 1024

    def __init__(self, max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES) -> None:
        value = cast(object, max_payload_bytes)
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError("max_payload_bytes must be an integer")
        if value <= 0:
            raise ValueError("max_payload_bytes must be positive")
        self._max_payload_bytes = value

    @property
    def max_payload_bytes(self) -> int:
        return self._max_payload_bytes

    def encode(self, state: SessionSnapshotState) -> SnapshotPayload:
        if state.schema_version != self.SCHEMA_VERSION:
            raise SnapshotIncompatible(
                f"session snapshot schema version {state.schema_version} is unsupported"
            )
        if state.capability_profile_version != self.CAPABILITY_PROFILE_VERSION:
            raise SnapshotIncompatible(
                f"capability profile version {state.capability_profile_version} is unsupported"
            )
        value: dict[str, object] = {
            "approved_environment": [
                {"name": item.name, "value": item.value}
                for item in sorted(
                    state.approved_environment.values,
                    key=lambda environment_value: environment_value.name,
                )
            ],
            "capability_profile_version": state.capability_profile_version,
            "cwd": state.cwd.value,
            "schema_version": state.schema_version,
            "workspace": {
                "encoded_base64": base64.b64encode(state.workspace.encoded).decode("ascii"),
                "integrity_hash": state.workspace.integrity_hash.value,
                "root_hash": state.workspace.root_hash.value,
                "schema_version": state.workspace.schema_version,
                "workspace_revision": state.workspace.workspace_revision.value,
            },
        }
        payload = _canonical_json(value)
        self._check_size(payload)
        return SnapshotPayload(
            payload=payload,
            content_hash=ContentHash.from_bytes(payload),
            format_name="json",
            schema_version=state.schema_version,
            workspace_revision=state.workspace.workspace_revision,
        )

    def decode(self, snapshot: SandboxSnapshot) -> SessionSnapshotState:
        self._check_size(snapshot.payload)
        if snapshot.metadata.format_name != "json":
            raise SnapshotIncompatible(
                f"snapshot format {snapshot.metadata.format_name} is unsupported"
            )
        if snapshot.metadata.payload_bytes != len(snapshot.payload):
            raise SnapshotCorrupt("snapshot metadata payload size does not match payload")
        if snapshot.content_hash != ContentHash.from_bytes(snapshot.payload):
            raise SnapshotCorrupt("snapshot content hash does not match payload")
        value = _load_object(snapshot.payload)
        if _canonical_json(value) != snapshot.payload:
            raise SnapshotCorrupt("snapshot payload is not canonical JSON")
        _require_keys(
            value,
            {
                "approved_environment",
                "capability_profile_version",
                "cwd",
                "schema_version",
                "workspace",
            },
            "session snapshot",
        )
        schema_version = _integer(value["schema_version"], "schema_version")
        capability_version = _integer(
            value["capability_profile_version"],
            "capability_profile_version",
        )
        if snapshot.schema_version != schema_version:
            raise SnapshotCorrupt("outer schema version does not match payload")
        if schema_version != self.SCHEMA_VERSION:
            raise SnapshotIncompatible(
                f"session snapshot schema version {schema_version} is unsupported"
            )
        if capability_version != self.CAPABILITY_PROFILE_VERSION:
            raise SnapshotIncompatible(
                f"capability profile version {capability_version} is unsupported"
            )
        workspace_value = _object(value["workspace"], "workspace")
        _require_keys(
            workspace_value,
            {
                "encoded_base64",
                "integrity_hash",
                "root_hash",
                "schema_version",
                "workspace_revision",
            },
            "workspace",
        )
        workspace = _workspace_snapshot(workspace_value)
        if snapshot.workspace_revision != workspace.workspace_revision:
            raise SnapshotCorrupt("outer workspace revision does not match payload")
        cwd_value = value["cwd"]
        if not isinstance(cwd_value, str):
            raise SnapshotCorrupt("cwd must be a string")
        try:
            cwd = SandboxPath(cwd_value)
            environment = _environment(value["approved_environment"])
        except (TypeError, ValueError) as error:
            raise SnapshotCorrupt("session snapshot contains invalid state") from error
        return SessionSnapshotState(
            workspace=workspace,
            cwd=cwd,
            approved_environment=environment,
            schema_version=schema_version,
            capability_profile_version=capability_version,
        )

    def _check_size(self, payload: bytes) -> None:
        if len(payload) > self._max_payload_bytes:
            raise SnapshotTooLarge(
                f"session snapshot contains {len(payload)} bytes; limit is "
                f"{self._max_payload_bytes} bytes"
            )


def _workspace_snapshot(value: dict[str, object]) -> WorkspaceSnapshotData:
    encoded_value = value["encoded_base64"]
    if not isinstance(encoded_value, str):
        raise SnapshotCorrupt("workspace encoded bytes must be base64 text")
    try:
        encoded = base64.b64decode(encoded_value, validate=True)
    except (binascii.Error, ValueError) as error:
        raise SnapshotCorrupt("workspace encoded bytes are not valid base64") from error
    try:
        return WorkspaceSnapshotData(
            encoded=encoded,
            schema_version=_integer(value["schema_version"], "workspace schema_version"),
            integrity_hash=ContentHash(_text(value["integrity_hash"], "integrity_hash")),
            workspace_revision=Revision(
                _integer(value["workspace_revision"], "workspace_revision")
            ),
            root_hash=ContentHash(_text(value["root_hash"], "root_hash")),
        )
    except (TypeError, ValueError) as error:
        raise SnapshotCorrupt("workspace snapshot metadata is invalid") from error


def _environment(value: object) -> CommandEnvironment:
    if not isinstance(value, list):
        raise SnapshotCorrupt("approved_environment must be a list")
    entries = cast(list[object], value)
    result: list[EnvironmentValue] = []
    for entry in entries:
        item = _object(entry, "environment entry")
        _require_keys(item, {"name", "value"}, "environment entry")
        result.append(
            EnvironmentValue(
                _text(item["name"], "environment name"),
                _text(item["value"], "environment value"),
            )
        )
    return CommandEnvironment(tuple(result))


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError, ValueError) as error:
        raise SnapshotCorrupt("session snapshot contains non-serializable data") from error


class _DuplicateKey(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKey(key)
        result[key] = value
    return result


def _load_object(payload: bytes) -> dict[str, object]:
    try:
        value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKey) as error:
        raise SnapshotCorrupt("session snapshot is not valid JSON") from error
    return _object(value, "session snapshot")


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SnapshotCorrupt(f"{name} must be an object")
    return cast(dict[str, object], value)


def _require_keys(value: dict[str, object], keys: set[str], name: str) -> None:
    if set(value) != keys:
        raise SnapshotCorrupt(f"{name} has missing or unsupported fields")


def _integer(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SnapshotCorrupt(f"{name} must be a non-negative integer")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise SnapshotCorrupt(f"{name} must be a string")
    return value
