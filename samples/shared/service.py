"""SDK-independent sample composition root for the MemSandbox service."""

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
from mem_sandbox.session import SessionPolicyEngine
from mem_sandbox.snapshots import (
    InMemorySnapshotStore,
    JsonSessionSnapshotCodec,
    SnapshotStoreLimits,
)


@dataclass(frozen=True, slots=True)
class SampleServiceBundle:
    """Service plus process-local snapshot dependencies used by scenario runners."""

    service: InMemorySandboxService
    snapshot_store: InMemorySnapshotStore
    clock: SystemClock


def create_sample_service_bundle(
    *,
    policy_engine: SessionPolicyEngine | None = None,
) -> SampleServiceBundle:
    """Construct the process-local service owned by one sample invocation."""
    clock = SystemClock()
    uuid_generator = SystemUuidGenerator()
    snapshot_codec = JsonSessionSnapshotCodec()
    snapshot_store = InMemorySnapshotStore(
        default_ttl=timedelta(days=1),
        limits=SnapshotStoreLimits(
            max_snapshots=20,
            max_total_payload_bytes=64 * 1024 * 1024,
        ),
        clock=clock,
    )
    session_factory = DefaultSessionFactory(
        policy_engine=policy_engine or AllowAllPolicyEngine(),
        secret_broker=NoSecretBroker(),
        event_sink=InMemoryEventSink(
            max_events=500,
            max_payload_bytes=4 * 1024 * 1024,
        ),
        snapshot_codec=snapshot_codec,
        clock=clock,
        uuid_generator=uuid_generator,
    )
    return SampleServiceBundle(
        service=InMemorySandboxService(
            session_factory=session_factory,
            snapshot_gateway=InMemoryServiceSnapshotGateway(snapshot_store),
            snapshot_decoder=snapshot_codec,
            clock=clock,
            uuid_generator=uuid_generator,
        ),
        snapshot_store=snapshot_store,
        clock=clock,
    )


def create_sample_service(
    *,
    policy_engine: SessionPolicyEngine | None = None,
) -> InMemorySandboxService:
    """Construct only the service for callers that do not need snapshot dependencies."""
    return create_sample_service_bundle(policy_engine=policy_engine).service
