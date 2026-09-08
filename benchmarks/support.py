"""Deterministic public MemSandbox composition used by validation drivers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from mem_sandbox.core import SystemClock, SystemUuidGenerator
from mem_sandbox.events import InMemoryEventSink
from mem_sandbox.policy import AllowAllPolicyEngine
from mem_sandbox.secrets import NoSecretBroker
from mem_sandbox.service import (
    DefaultSessionFactory,
    InMemorySandboxService,
    InMemoryServiceSnapshotGateway,
)
from mem_sandbox.snapshots import (
    InMemorySnapshotStore,
    JsonSessionSnapshotCodec,
    SnapshotStoreLimits,
)


@dataclass(frozen=True, slots=True)
class ValidationServiceBundle:
    service: InMemorySandboxService
    snapshot_store: InMemorySnapshotStore
    snapshot_gateway: InMemoryServiceSnapshotGateway
    snapshot_codec: JsonSessionSnapshotCodec
    clock: SystemClock


def create_validation_service_bundle() -> ValidationServiceBundle:
    clock = SystemClock()
    uuids = SystemUuidGenerator()
    codec = JsonSessionSnapshotCodec()
    store = InMemorySnapshotStore(
        default_ttl=timedelta(days=1),
        limits=SnapshotStoreLimits(
            max_snapshots=100,
            max_total_payload_bytes=128 * 1024 * 1024,
        ),
        clock=clock,
    )
    gateway = InMemoryServiceSnapshotGateway(store)
    factory = DefaultSessionFactory(
        policy_engine=AllowAllPolicyEngine(),
        secret_broker=NoSecretBroker(),
        event_sink=InMemoryEventSink(max_events=2_000, max_payload_bytes=16 * 1024 * 1024),
        snapshot_codec=codec,
        clock=clock,
        uuid_generator=uuids,
    )
    service = InMemorySandboxService(
        session_factory=factory,
        snapshot_gateway=gateway,
        snapshot_decoder=codec,
        clock=clock,
        uuid_generator=uuids,
    )
    return ValidationServiceBundle(service, store, gateway, codec, clock)
