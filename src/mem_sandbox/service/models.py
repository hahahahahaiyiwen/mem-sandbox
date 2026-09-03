"""Immutable host-facing sandbox service values."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast
from uuid import UUID

from mem_sandbox.core import OperationLimits, SessionId
from mem_sandbox.snapshots import SessionSnapshotState, SnapshotRef
from mem_sandbox.workspace import WorkspaceLimits

if TYPE_CHECKING:
    from mem_sandbox.service.ports import FactorySnapshotStore


@dataclass(frozen=True, slots=True)
class OwnerId:
    """Application-supplied logical owner provenance."""

    value: str

    def __post_init__(self) -> None:
        value = _require_text("owner_id", self.value)
        try:
            size = len(value.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise ValueError("owner_id must be valid UTF-8") from error
        if size == 0 or size > 256:
            raise ValueError("owner_id must contain between 1 and 256 UTF-8 bytes")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class SandboxHandle:
    """Opaque process-local lookup handle."""

    value: UUID

    def __post_init__(self) -> None:
        value = cast(object, self.value)
        if not isinstance(value, UUID):
            raise TypeError("sandbox handle value must be a UUID")
        if value.int == 0:
            raise ValueError("sandbox handle value must not be the nil UUID")

    def __str__(self) -> str:
        return str(self.value)


@dataclass(frozen=True, slots=True, kw_only=True)
class SandboxOptions:
    workspace_limits: WorkspaceLimits = field(default_factory=WorkspaceLimits)
    lifecycle_limits: OperationLimits = field(default_factory=OperationLimits)

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.workspace_limits), WorkspaceLimits):
            raise TypeError("workspace_limits must be WorkspaceLimits")
        if not isinstance(cast(object, self.lifecycle_limits), OperationLimits):
            raise TypeError("lifecycle_limits must be OperationLimits")


@dataclass(frozen=True, slots=True)
class WorkspaceSeedFile:
    path: str
    content: bytes

    def __post_init__(self) -> None:
        path = _require_text("seed path", self.path)
        try:
            path.encode("utf-8")
        except UnicodeEncodeError as error:
            raise ValueError("seed path must be valid UTF-8") from error
        if not path:
            raise ValueError("seed path must not be empty")
        if not isinstance(cast(object, self.content), bytes):
            raise TypeError("seed content must be bytes")


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateSandboxRequest:
    owner_id: OwnerId
    options: SandboxOptions = field(default_factory=SandboxOptions)
    initial_files: tuple[WorkspaceSeedFile, ...] = ()

    def __post_init__(self) -> None:
        _require_owner(self.owner_id)
        _require_options(self.options)
        object.__setattr__(self, "initial_files", _seed_tuple(self.initial_files))


@dataclass(frozen=True, slots=True, kw_only=True)
class ResumeSandboxRequest:
    owner_id: OwnerId
    snapshot_ref: SnapshotRef
    options: SandboxOptions = field(default_factory=SandboxOptions)

    def __post_init__(self) -> None:
        _require_owner(self.owner_id)
        _require_options(self.options)
        if not isinstance(cast(object, self.snapshot_ref), SnapshotRef):
            raise TypeError("snapshot_ref must be SnapshotRef")


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionFactoryRequest:
    session_id: SessionId
    options: SandboxOptions
    snapshot_store: FactorySnapshotStore
    initial_files: tuple[WorkspaceSeedFile, ...] = ()
    restored_state: SessionSnapshotState | None = None

    def __post_init__(self) -> None:
        if not isinstance(cast(object, self.session_id), SessionId):
            raise TypeError("session_id must be SessionId")
        _require_options(self.options)
        object.__setattr__(self, "initial_files", _seed_tuple(self.initial_files))
        restored_state = cast(object, self.restored_state)
        if restored_state is not None and not isinstance(restored_state, SessionSnapshotState):
            raise TypeError("restored_state must be SessionSnapshotState or None")
        if self.initial_files and self.restored_state is not None:
            raise ValueError("initial_files and restored_state cannot be combined")


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    return value


def _require_owner(value: object) -> None:
    if not isinstance(value, OwnerId):
        raise TypeError("owner_id must be OwnerId")


def _require_options(value: object) -> None:
    if not isinstance(value, SandboxOptions):
        raise TypeError("options must be SandboxOptions")


def _seed_tuple(value: object) -> tuple[WorkspaceSeedFile, ...]:
    if not isinstance(value, tuple):
        raise TypeError("initial_files must be a tuple")
    raw_seeds = cast(tuple[object, ...], value)
    seeds: list[WorkspaceSeedFile] = []
    for seed in raw_seeds:
        if not isinstance(seed, WorkspaceSeedFile):
            raise TypeError("initial_files must contain WorkspaceSeedFile values")
        seeds.append(seed)
    return tuple(seeds)
