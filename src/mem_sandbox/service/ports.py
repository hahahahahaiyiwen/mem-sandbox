"""Narrow interfaces owned by the sandbox service boundary."""

from typing import Protocol

from mem_sandbox.service.models import (
    CreateSandboxRequest,
    OwnerId,
    ResumeSandboxRequest,
    SandboxHandle,
    SessionFactoryRequest,
)
from mem_sandbox.session import SandboxSession
from mem_sandbox.snapshots import (
    SandboxSnapshot,
    SandboxSnapshotDraft,
    SessionSnapshotState,
    SnapshotRef,
)


class FactorySnapshotStore(Protocol):
    @property
    def process_local(self) -> bool: ...

    async def save(self, draft: SandboxSnapshotDraft) -> SnapshotRef: ...
    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot: ...


class ServiceSnapshotGateway(Protocol):
    def session_store(self, owner_id: OwnerId) -> FactorySnapshotStore: ...
    async def load(self, snapshot_ref: SnapshotRef) -> SandboxSnapshot: ...


class ServiceSnapshotDecoder(Protocol):
    def decode(self, snapshot: SandboxSnapshot) -> SessionSnapshotState: ...


class ServiceSessionRuntime(Protocol):
    @property
    def session(self) -> SandboxSession: ...

    async def close(self) -> None: ...


class SessionFactory(Protocol):
    async def create(self, request: SessionFactoryRequest) -> ServiceSessionRuntime: ...


class SandboxService(Protocol):
    async def create(self, request: CreateSandboxRequest) -> SandboxHandle: ...
    async def get_session(self, handle: SandboxHandle) -> SandboxSession: ...
    async def resume(self, request: ResumeSandboxRequest) -> SandboxHandle: ...
    async def delete(self, handle: SandboxHandle) -> None: ...
    async def close(self) -> None: ...


class AsyncCloseable(Protocol):
    async def close(self) -> None: ...
