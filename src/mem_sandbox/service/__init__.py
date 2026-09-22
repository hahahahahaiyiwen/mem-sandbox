"""Process-local sandbox lifecycle service."""

from mem_sandbox.service.errors import (
    InvalidSandboxRequest,
    SandboxDeleteFailed,
    SandboxIdentifierConflict,
    SandboxNotFound,
    SandboxResumeFailed,
    SandboxServiceClosed,
    SandboxStartupFailed,
    SessionFactoryCleanupFailed,
    SessionFactoryFailed,
)
from mem_sandbox.service.factory import DefaultSessionFactory
from mem_sandbox.service.models import (
    ConnectedSandboxProfile,
    CreateSandboxRequest,
    OwnerId,
    ResumeSandboxRequest,
    SandboxHandle,
    SandboxOptions,
    SandboxProfile,
    SessionFactoryRequest,
    VirtualSandboxProfile,
    WorkspaceSeedFile,
)
from mem_sandbox.service.ports import (
    AsyncCloseable,
    FactorySnapshotStore,
    SandboxService,
    ServiceSessionRuntime,
    ServiceSnapshotDecoder,
    ServiceSnapshotGateway,
    SessionFactory,
)
from mem_sandbox.service.resources import (
    CompositeResourceScope,
    DefaultServiceSessionRuntime,
)
from mem_sandbox.service.service import InMemorySandboxService
from mem_sandbox.service.snapshots import InMemoryServiceSnapshotGateway

__all__ = [
    "AsyncCloseable",
    "CompositeResourceScope",
    "ConnectedSandboxProfile",
    "CreateSandboxRequest",
    "DefaultServiceSessionRuntime",
    "DefaultSessionFactory",
    "FactorySnapshotStore",
    "InMemorySandboxService",
    "InMemoryServiceSnapshotGateway",
    "InvalidSandboxRequest",
    "OwnerId",
    "ResumeSandboxRequest",
    "SandboxDeleteFailed",
    "SandboxHandle",
    "SandboxIdentifierConflict",
    "SandboxNotFound",
    "SandboxOptions",
    "SandboxProfile",
    "SandboxResumeFailed",
    "SandboxService",
    "SandboxServiceClosed",
    "SandboxStartupFailed",
    "ServiceSessionRuntime",
    "ServiceSnapshotDecoder",
    "ServiceSnapshotGateway",
    "SessionFactory",
    "SessionFactoryCleanupFailed",
    "SessionFactoryFailed",
    "SessionFactoryRequest",
    "VirtualSandboxProfile",
    "WorkspaceSeedFile",
]
