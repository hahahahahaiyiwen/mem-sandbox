from __future__ import annotations

import io
from collections.abc import Callable
from dataclasses import MISSING, fields
from importlib.metadata import version
from inspect import iscoroutinefunction, signature
from pathlib import Path
from typing import cast
from uuid import UUID, uuid4

import pytest
from agents.sandbox import (
    Capability,
    ExecResult,
    Manifest,
    SandboxConcurrencyLimits,
    SandboxRunConfig,
    SnapshotSpec,
    User,
)
from agents.sandbox.session import (
    BaseSandboxClient,
    BaseSandboxClientOptions,
    BaseSandboxSession,
    Dependencies,
    SandboxSession,
    SandboxSessionState,
)
from agents.sandbox.snapshot import NoopSnapshot, SnapshotBase
from pydantic import Field, PrivateAttr

PINNED_OPENAI_AGENTS_VERSION = "0.22.0"


class MutableContractCapability(Capability):
    type: str = "mutable-contract-test"
    mutable_state: dict[str, list[str]] = Field(default_factory=lambda: {"events": ["original"]})


class ContractClientOptions(BaseSandboxClientOptions):
    type: str = "contract-client"
    label: str = "contract"


class ContractClient(BaseSandboxClient[ContractClientOptions]):
    backend_id = "contract-client"

    async def create(
        self,
        *,
        snapshot: SnapshotSpec | SnapshotBase | None = None,
        manifest: Manifest | None = None,
        options: ContractClientOptions,
    ) -> SandboxSession:
        _ = (snapshot, manifest, options)
        raise NotImplementedError

    async def delete(self, session: SandboxSession) -> SandboxSession:
        _ = session
        raise NotImplementedError

    async def resume(self, state: SandboxSessionState) -> SandboxSession:
        _ = state
        raise NotImplementedError

    def deserialize_session_state(self, payload: dict[str, object]) -> SandboxSessionState:
        return self._deserialize_session_state_payload(payload, SandboxSessionState)


def _empty_bytes_list() -> list[bytes]:
    return []


def _empty_stream_list() -> list[io.IOBase]:
    return []


class RecordingSnapshot(SnapshotBase):
    type: str = "contract-recording"
    events: list[str]
    persisted_payloads: list[bytes] = Field(default_factory=_empty_bytes_list)
    _persisted_streams: list[io.IOBase] = PrivateAttr(default_factory=_empty_stream_list)

    @property
    def persisted_streams(self) -> tuple[io.IOBase, ...]:
        return tuple(self._persisted_streams)

    async def persist(
        self,
        data: io.IOBase,
        *,
        dependencies: Dependencies | None = None,
    ) -> None:
        _ = dependencies
        self.events.append("snapshot.persist")
        self._persisted_streams.append(data)
        payload = data.read()
        if not isinstance(payload, bytes):
            raise TypeError("expected binary workspace payload")
        self.persisted_payloads.append(payload)

    async def restore(self, *, dependencies: Dependencies | None = None) -> io.IOBase:
        _ = dependencies
        self.events.append("snapshot.restore")
        return io.BytesIO(b"restored")

    async def restorable(self, *, dependencies: Dependencies | None = None) -> bool:
        _ = dependencies
        self.events.append("snapshot.restorable")
        return False


class RecordingSession(BaseSandboxSession):
    events: list[str]

    def __init__(self, snapshot: RecordingSnapshot) -> None:
        self.events = snapshot.events
        self.state = SandboxSessionState(
            type="contract-session",
            snapshot=snapshot,
            manifest=Manifest(),
        )

    async def _ensure_backend_started(self) -> None:
        self.events.append("ensure_backend_started")

    async def _probe_workspace_root_for_preserved_resume(self) -> bool:
        self.events.append("probe_workspace_root")
        return False

    async def _prepare_backend_workspace(self) -> None:
        self.events.append("prepare_backend_workspace")

    async def _ensure_runtime_helpers(self) -> None:
        self.events.append("ensure_runtime_helpers")

    async def _start_workspace(self) -> None:
        self.events.append("start_workspace")

    async def _after_start(self) -> None:
        self.events.append("after_start")

    async def _before_stop(self) -> None:
        self.events.append("before_stop")

    async def _after_stop(self) -> None:
        self.events.append("after_stop")

    async def _before_shutdown(self) -> None:
        self.events.append("before_shutdown")

    async def _shutdown_backend(self) -> None:
        self.events.append("shutdown_backend")

    async def _after_shutdown(self) -> None:
        self.events.append("after_shutdown")

    async def _exec_internal(
        self,
        *command: str | Path,
        timeout: float | None = None,
    ) -> ExecResult:
        _ = (command, timeout)
        raise NotImplementedError

    async def read(self, path: Path, *, user: str | User | None = None) -> io.IOBase:
        _ = (path, user)
        raise NotImplementedError

    async def write(
        self,
        path: Path,
        data: io.IOBase,
        *,
        user: str | User | None = None,
    ) -> None:
        _ = (path, data, user)
        raise NotImplementedError

    async def running(self) -> bool:
        return True

    async def persist_workspace(self) -> io.IOBase:
        self.events.append("persist_workspace")
        return io.BytesIO(b"workspace")

    async def hydrate_workspace(self, data: io.IOBase) -> None:
        _ = data
        self.events.append("hydrate_workspace")


def _method_contract(callable_: Callable[..., object]) -> tuple[str, bool]:
    return str(signature(callable_)), iscoroutinefunction(callable_)


def _method(owner: object, name: str) -> Callable[..., object]:
    return cast(Callable[..., object], getattr(owner, name))


def _define_options_without_discriminator() -> type[BaseSandboxClientOptions]:
    class MissingTypeDiscriminator(BaseSandboxClientOptions):
        pass

    return MissingTypeDiscriminator


def test_openai_agents_contract_uses_the_exact_tested_version() -> None:
    assert version("openai-agents") == PINNED_OPENAI_AGENTS_VERSION


def test_sandbox_client_options_contract_is_frozen_and_discriminated() -> None:
    assert tuple(BaseSandboxClientOptions.model_fields) == ("type",)
    assert BaseSandboxClientOptions.model_config.get("frozen") is True
    assert BaseSandboxClientOptions.model_config.get("arbitrary_types_allowed") is True

    with pytest.raises(TypeError, match="must define a non-empty string default for `type`"):
        _define_options_without_discriminator()


def test_sandbox_session_state_contract_fields_are_stable() -> None:
    state_fields = SandboxSessionState.model_fields

    assert tuple(state_fields) == (
        "type",
        "session_id",
        "snapshot",
        "manifest",
        "exposed_ports",
        "snapshot_fingerprint",
        "snapshot_fingerprint_version",
        "workspace_root_ready",
    )
    assert {name: field.annotation for name, field in state_fields.items()} == {
        "type": str,
        "session_id": UUID,
        "snapshot": SnapshotBase,
        "manifest": Manifest,
        "exposed_ports": tuple[int, ...],
        "snapshot_fingerprint": str | None,
        "snapshot_fingerprint_version": str | None,
        "workspace_root_ready": bool,
    }
    assert {name: field.is_required() for name, field in state_fields.items()} == {
        "type": True,
        "session_id": False,
        "snapshot": True,
        "manifest": True,
        "exposed_ports": False,
        "snapshot_fingerprint": False,
        "snapshot_fingerprint_version": False,
        "workspace_root_ready": False,
    }
    assert {name: field.default_factory for name, field in state_fields.items()} == {
        "type": None,
        "session_id": uuid4,
        "snapshot": None,
        "manifest": None,
        "exposed_ports": tuple,
        "snapshot_fingerprint": None,
        "snapshot_fingerprint_version": None,
        "workspace_root_ready": None,
    }
    assert state_fields["snapshot_fingerprint"].default is None
    assert state_fields["snapshot_fingerprint_version"].default is None
    assert state_fields["workspace_root_ready"].default is False
    assert SandboxSessionState.model_config.get("arbitrary_types_allowed") is True
    assert SandboxSessionState.model_config.get("hide_input_in_errors") is True


def test_sandbox_client_abstract_contract_is_stable() -> None:
    assert BaseSandboxClient.__abstractmethods__ == frozenset(
        {
            "create",
            "delete",
            "resume",
            "deserialize_session_state",
        }
    )
    assert _method_contract(_method(BaseSandboxClient, "create")) == (
        "(self, *, snapshot: 'SnapshotSpec | SnapshotBase | None' = None, "
        "manifest: 'Manifest | None' = None, options: 'ClientOptionsT') -> 'SandboxSession'",
        True,
    )
    assert _method_contract(_method(BaseSandboxClient, "delete")) == (
        "(self, session: 'SandboxSession') -> 'SandboxSession'",
        True,
    )
    assert _method_contract(_method(BaseSandboxClient, "resume")) == (
        "(self, state: 'SandboxSessionState') -> 'SandboxSession'",
        True,
    )
    assert _method_contract(_method(BaseSandboxClient, "deserialize_session_state")) == (
        "(self, payload: 'dict[str, object]') -> 'SandboxSessionState'",
        False,
    )


def test_sandbox_session_abstract_contract_is_stable() -> None:
    assert BaseSandboxSession.__abstractmethods__ == frozenset(
        {
            "_exec_internal",
            "read",
            "write",
            "running",
            "persist_workspace",
            "hydrate_workspace",
        }
    )
    assert _method_contract(_method(BaseSandboxSession, "_exec_internal")) == (
        "(self, *command: str | pathlib.Path, timeout: float | None = None) "
        "-> agents.sandbox.types.ExecResult",
        True,
    )
    assert _method_contract(_method(BaseSandboxSession, "read")) == (
        "(self, path: pathlib.Path, *, user: str | agents.sandbox.types.User | None = None) "
        "-> io.IOBase",
        True,
    )
    assert _method_contract(_method(BaseSandboxSession, "write")) == (
        "(self, path: pathlib.Path, data: io.IOBase, *, "
        "user: str | agents.sandbox.types.User | None = None) -> None",
        True,
    )
    assert _method_contract(_method(BaseSandboxSession, "running")) == (
        "(self) -> bool",
        True,
    )
    assert _method_contract(_method(BaseSandboxSession, "persist_workspace")) == (
        "(self) -> io.IOBase",
        True,
    )
    assert _method_contract(_method(BaseSandboxSession, "hydrate_workspace")) == (
        "(self, data: io.IOBase) -> None",
        True,
    )


def test_sandbox_session_concrete_override_hooks_are_stable() -> None:
    expected = {
        "exec": (
            "(self, *command: str | pathlib.Path, timeout: float | None = None, "
            "shell: bool | list[str] = True, "
            "user: str | agents.sandbox.types.User | None = None) "
            "-> agents.sandbox.types.ExecResult",
            True,
        ),
        "_probe_workspace_root_for_preserved_resume": ("(self) -> bool", True),
        "_start_workspace": ("(self) -> None", True),
        "_clear_workspace_root_on_resume": ("(self) -> None", True),
        "_should_compute_snapshot_fingerprint_on_persist": ("(self) -> bool", False),
        "_can_skip_snapshot_restore_on_resume": (
            "(self, *, is_running: bool) -> bool",
            True,
        ),
        "_set_start_state_preserved": (
            "(self, workspace: bool, *, system: bool | None = None) -> None",
            False,
        ),
        "_validate_path_access": (
            "(self, path: pathlib.Path | str, *, for_write: bool = False) -> pathlib.Path",
            True,
        ),
        "ls": (
            "(self, path: pathlib.Path | str, *, "
            "user: str | agents.sandbox.types.User | None = None) "
            "-> list[agents.sandbox.files.FileEntry]",
            True,
        ),
        "mkdir": (
            "(self, path: pathlib.Path | str, *, parents: bool = False, "
            "user: str | agents.sandbox.types.User | None = None) -> None",
            True,
        ),
        "rm": (
            "(self, path: pathlib.Path | str, *, recursive: bool = False, "
            "user: str | agents.sandbox.types.User | None = None) -> None",
            True,
        ),
        "extract": (
            "(self, path: pathlib.Path | str, data: io.IOBase, *, "
            "compression_scheme: Optional[Literal['tar', 'zip']] = None, "
            "archive_limits: agents.run_config.SandboxArchiveLimits | None = None) -> None",
            True,
        ),
        "_validate_manifest_application": (
            "(self, *, only_ephemeral: bool = False, "
            "manifest: agents.sandbox.manifest.Manifest | None = None, "
            "session_running: bool | None = None) -> None",
            True,
        ),
        "_apply_manifest": (
            "(self, *, only_ephemeral: bool = False, provision_accounts: bool = True) "
            "-> agents.sandbox.materialization.MaterializationResult",
            True,
        ),
        "_apply_entry_batch": (
            "(self, entries: collections.abc.Sequence[tuple[pathlib.Path, "
            "agents.sandbox.entries.base.BaseEntry]], *, base_dir: pathlib.Path) "
            "-> list[agents.sandbox.materialization.MaterializedFile]",
            True,
        ),
        "provision_manifest_accounts": ("(self) -> None", True),
    }

    assert {
        name: _method_contract(_method(BaseSandboxSession, name)) for name in expected
    } == expected


def test_capability_clone_and_bind_contract_is_stable() -> None:
    assert tuple(Capability.model_fields) == (
        "type",
        "session",
        "run_as",
        "workspace_scope",
    )
    assert _method_contract(_method(Capability, "clone")) == (
        "(self) -> 'Capability'",
        False,
    )
    assert _method_contract(_method(Capability, "bind")) == (
        "(self, session: agents.sandbox.session.base_sandbox_session.BaseSandboxSession) -> None",
        False,
    )

    capability = MutableContractCapability()
    bound_session = cast(BaseSandboxSession, object())
    cloned = cast(MutableContractCapability, capability.clone())
    cloned.mutable_state["events"].append("clone")
    cloned.bind(bound_session)

    assert cloned is not capability
    assert cloned.mutable_state is not capability.mutable_state
    assert cloned.mutable_state["events"] is not capability.mutable_state["events"]
    assert capability.mutable_state == {"events": ["original"]}
    assert cloned.mutable_state == {"events": ["original", "clone"]}
    assert capability.session is None
    assert cloned.session is bound_session


def test_sandbox_run_config_contract_fields_are_stable() -> None:
    config_fields = {field.name: field for field in fields(SandboxRunConfig)}

    assert tuple(config_fields) == (
        "client",
        "options",
        "session",
        "session_state",
        "manifest",
        "snapshot",
        "concurrency_limits",
        "archive_limits",
        "cwd",
    )
    assert {name: field.type for name, field in config_fields.items()} == {
        "client": "BaseSandboxClient[Any] | None",
        "options": "Any | None",
        "session": "BaseSandboxSession | None",
        "session_state": "SandboxSessionState | None",
        "manifest": "Manifest | None",
        "snapshot": "SnapshotSpec | SnapshotBase | None",
        "concurrency_limits": "SandboxConcurrencyLimits",
        "archive_limits": "SandboxArchiveLimits | None",
        "cwd": "str | PurePath | None",
    }
    assert {
        name: field.default for name, field in config_fields.items() if name != "concurrency_limits"
    } == {
        "client": None,
        "options": None,
        "session": None,
        "session_state": None,
        "manifest": None,
        "snapshot": None,
        "archive_limits": None,
        "cwd": None,
    }
    assert config_fields["concurrency_limits"].default is MISSING
    assert config_fields["concurrency_limits"].default_factory is SandboxConcurrencyLimits
    assert all(
        field.default_factory is MISSING
        for name, field in config_fields.items()
        if name != "concurrency_limits"
    )


def test_snapshot_contract_is_frozen_and_stable() -> None:
    assert tuple(SnapshotBase.model_fields) == ("type", "id")
    assert SnapshotBase.model_config.get("frozen") is True
    assert SnapshotBase.__abstractmethods__ == frozenset(
        {
            "persist",
            "restore",
            "restorable",
        }
    )
    assert _method_contract(_method(SnapshotBase, "persist")) == (
        "(self, data: io.IOBase, *, "
        "dependencies: agents.sandbox.session.dependencies.Dependencies | None = None) -> None",
        True,
    )
    assert _method_contract(_method(SnapshotBase, "restore")) == (
        "(self, *, dependencies: agents.sandbox.session.dependencies.Dependencies | None = None) "
        "-> io.IOBase",
        True,
    )
    assert _method_contract(_method(SnapshotBase, "restorable")) == (
        "(self, *, dependencies: agents.sandbox.session.dependencies.Dependencies | None = None) "
        "-> bool",
        True,
    )


def test_options_state_and_snapshot_serialization_round_trip() -> None:
    options = ContractClientOptions(label="configured")
    options_payload = options.model_dump(mode="json")

    assert options_payload == {"type": "contract-client", "label": "configured"}
    assert BaseSandboxClientOptions.parse(options_payload) == options

    snapshot = NoopSnapshot(id="snapshot-1")
    snapshot_payload = snapshot.model_dump(mode="json")

    assert snapshot_payload == {"type": "noop", "id": "snapshot-1"}
    assert SnapshotBase.parse(snapshot_payload) == snapshot

    client = ContractClient()
    state = SandboxSessionState(
        type=client.backend_id,
        snapshot=snapshot,
        manifest=Manifest(),
        exposed_ports=(8080,),
        workspace_root_ready=True,
    )
    state_payload = client.serialize_session_state(state)
    restored = client.deserialize_session_state(state_payload.copy())

    assert state_payload == state.model_dump(mode="json")
    assert restored == state


@pytest.mark.asyncio
async def test_session_lifecycle_orders_start_persist_shutdown_and_stream_cleanup() -> None:
    snapshot = RecordingSnapshot(id="snapshot-1", events=[])
    events = snapshot.events
    session = RecordingSession(snapshot)

    await session.start()

    assert events == [
        "ensure_backend_started",
        "probe_workspace_root",
        "prepare_backend_workspace",
        "ensure_runtime_helpers",
        "start_workspace",
        "after_start",
    ]
    assert session.state.workspace_root_ready is True

    events.clear()
    await session.aclose()

    assert events == [
        "before_stop",
        "persist_workspace",
        "snapshot.persist",
        "after_stop",
        "before_shutdown",
        "shutdown_backend",
        "after_shutdown",
    ]
    assert snapshot.persisted_payloads == [b"workspace"]
    assert len(snapshot.persisted_streams) == 1
    assert snapshot.persisted_streams[0].closed
