from __future__ import annotations

from datetime import timedelta

import pytest

from mem_sandbox.command_executor import (
    CommandContext,
    CommandDescriptor,
    CommandFailureCode,
    CommandRegistry,
    CommandRequest,
    CommandResult,
    VirtualCommandExecutor,
    create_command_profile,
)
from mem_sandbox.core import SessionId, SystemClock, SystemUuidGenerator
from mem_sandbox.events import InMemoryEventSink, canonical_event_bytes
from mem_sandbox.policy import AllowAllPolicyEngine
from mem_sandbox.secrets import (
    BoundedSecretBroker,
    MappingSecretSource,
    SecretBrokerLimits,
    SecretRef,
    SecretValue,
)
from mem_sandbox.session import (
    CreateSnapshotRequest,
    NoOpSessionResourceScope,
    ReadFileRequest,
    SandboxSession,
    SessionExecuteRequest,
    SessionSecretEnvironmentBinding,
)
from mem_sandbox.snapshots import (
    InMemorySnapshotStore,
    JsonSessionSnapshotCodec,
    SnapshotStoreLimits,
)
from mem_sandbox.workspace import MemoryWorkspace, SandboxPath

CANARY = "lease-\N{LOCK}-\N{SNOWMAN}-canary"
SECRET_REF = SecretRef("deployment-token")


class SecretConsumerCommand:
    descriptor = CommandDescriptor(
        "secret-consumer",
        (),
        "secret-consumer",
        "secret-consumer",
        True,
        True,
        True,
    )

    async def execute(
        self,
        request: CommandRequest,
        context: CommandContext,
    ) -> CommandResult:
        return CommandResult.success(stdout=f"{context.environment.get('DEPLOY_TOKEN')}\n")


@pytest.mark.asyncio
async def test_secret_canary_stays_out_of_persistent_and_observable_boundaries() -> None:
    clock = SystemClock()
    uuids = SystemUuidGenerator()
    workspace = MemoryWorkspace()
    events = InMemoryEventSink(max_events=100, max_payload_bytes=1024 * 1024)
    snapshots = InMemorySnapshotStore(
        default_ttl=timedelta(days=1),
        limits=SnapshotStoreLimits(
            max_snapshots=10,
            max_total_payload_bytes=64 * 1024 * 1024,
        ),
        clock=clock,
    )
    session = SandboxSession(
        session_id=SessionId(uuids.new_uuid()),
        workspace_reader=workspace,
        workspace_mutator=workspace,
        workspace_snapshots=workspace,
        command_executor=VirtualCommandExecutor(
            CommandRegistry(
                (
                    *create_command_profile(workspace, workspace),
                    SecretConsumerCommand(),
                )
            ),
            workspace,
        ),
        policy_engine=AllowAllPolicyEngine(),
        secret_broker=BoundedSecretBroker(
            MappingSecretSource({SECRET_REF: SecretValue(CANARY)}),
            limits=SecretBrokerLimits(
                max_active_leases=4,
                max_lease_seconds=30,
                max_value_bytes=256,
            ),
            clock=clock,
        ),
        event_sink=events,
        snapshot_store=snapshots,
        snapshot_codec=JsonSessionSnapshotCodec(),
        resource_scope=NoOpSessionResourceScope(),
        clock=clock,
        uuid_generator=uuids,
    )
    binding = (SessionSecretEnvironmentBinding("DEPLOY_TOKEN", SECRET_REF),)
    await session.start()

    visible = await session.execute(
        SessionExecuteRequest(
            command="secret-consumer; env",
            secret_environment=binding,
        )
    )
    redirected = await session.execute(
        SessionExecuteRequest(
            command="secret-consumer > captured.txt",
            secret_environment=binding,
        )
    )
    rejected_path = await session.execute(
        SessionExecuteRequest(
            command='touch "$DEPLOY_TOKEN"',
            secret_environment=binding,
        )
    )
    rejected_environment = await session.execute(
        SessionExecuteRequest(
            command='export SAVED="$DEPLOY_TOKEN"',
            secret_environment=binding,
        )
    )
    captured = await session.read_file(ReadFileRequest(path="captured.txt"))
    persisted_content = (
        await workspace.read_text(SandboxPath.resolve("/workspace/captured.txt"))
    ).content
    snapshot_result = await session.create_snapshot(CreateSnapshotRequest())
    snapshot = await snapshots.load(snapshot_result.snapshot_ref)
    collected_events = await events.query()
    workspace_entries = await workspace.list(SandboxPath.root())

    assert visible.stdout == ("[REDACTED]\nDEPLOY_TOKEN=[REDACTED]\nPWD=/workspace\n")
    assert redirected.stdout == ""
    assert captured.content == "[REDACTED]"
    assert persisted_content == "[REDACTED]\n"
    assert rejected_path.failure_code is CommandFailureCode.PROTECTED_VALUE_REJECTED
    assert rejected_environment.failure_code is CommandFailureCode.PROTECTED_VALUE_REJECTED
    assert session.environment.get("DEPLOY_TOKEN") == ""
    assert session.environment.get("SAVED") == ""
    assert [entry.path.name for entry in workspace_entries] == ["captured.txt"]

    observed_text = "\n".join(
        (
            repr(visible),
            str(visible),
            repr(redirected),
            str(redirected),
            repr(rejected_path),
            rejected_path.stderr,
            repr(rejected_environment),
            rejected_environment.stderr,
            captured.content,
            persisted_content,
            *(repr(event) for event in collected_events),
        )
    )
    observed_bytes = b"".join(
        (
            snapshot.payload,
            *(canonical_event_bytes(event) for event in collected_events),
        )
    )
    assert CANARY not in observed_text
    assert CANARY.encode("utf-8") not in observed_bytes
