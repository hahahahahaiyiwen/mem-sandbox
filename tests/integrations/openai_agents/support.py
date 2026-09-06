from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from mem_sandbox.core import SystemClock, SystemUuidGenerator
from mem_sandbox.events import InMemoryEventSink
from mem_sandbox.policy import AllowAllPolicyEngine
from mem_sandbox.secrets import NoSecretBroker
from mem_sandbox.service import (
    CreateSandboxRequest,
    DefaultSessionFactory,
    InMemorySandboxService,
    InMemoryServiceSnapshotGateway,
    ResumeSandboxRequest,
    SandboxHandle,
)
from mem_sandbox.session import SandboxSession
from mem_sandbox.snapshots import (
    InMemorySnapshotStore,
    JsonSessionSnapshotCodec,
    SnapshotStoreLimits,
)


@dataclass(frozen=True, slots=True)
class ServiceBundle:
    service: InMemorySandboxService


class RecordingService:
    def __init__(self, delegate: InMemorySandboxService) -> None:
        self.delegate = delegate
        self.create_calls = 0
        self.created_handles: list[SandboxHandle] = []
        self.deleted_handles: list[SandboxHandle] = []
        self.get_session_error: Exception | None = None
        self.get_session_calls = 0

    async def create(self, request: CreateSandboxRequest) -> SandboxHandle:
        self.create_calls += 1
        handle = await self.delegate.create(request)
        self.created_handles.append(handle)
        return handle

    async def get_session(self, handle: SandboxHandle) -> SandboxSession:
        self.get_session_calls += 1
        if self.get_session_error is not None:
            raise self.get_session_error
        return await self.delegate.get_session(handle)

    async def resume(self, request: ResumeSandboxRequest) -> SandboxHandle:
        return await self.delegate.resume(request)

    async def delete(self, handle: SandboxHandle) -> None:
        self.deleted_handles.append(handle)
        await self.delegate.delete(handle)

    async def close(self) -> None:
        await self.delegate.close()


def create_service_bundle() -> ServiceBundle:
    clock = SystemClock()
    uuids = SystemUuidGenerator()
    codec = JsonSessionSnapshotCodec()
    store = InMemorySnapshotStore(
        default_ttl=timedelta(days=1),
        limits=SnapshotStoreLimits(
            max_snapshots=20,
            max_total_payload_bytes=64 * 1024 * 1024,
        ),
        clock=clock,
    )
    gateway = InMemoryServiceSnapshotGateway(store)
    factory = DefaultSessionFactory(
        policy_engine=AllowAllPolicyEngine(),
        secret_broker=NoSecretBroker(),
        event_sink=InMemoryEventSink(max_events=500, max_payload_bytes=4 * 1024 * 1024),
        snapshot_codec=codec,
        clock=clock,
        uuid_generator=uuids,
    )
    return ServiceBundle(
        InMemorySandboxService(
            session_factory=factory,
            snapshot_gateway=gateway,
            snapshot_decoder=codec,
            clock=clock,
            uuid_generator=uuids,
        )
    )
