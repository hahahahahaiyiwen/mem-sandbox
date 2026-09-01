from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest

from mem_sandbox.core import OperationId, OperationKind, OperationLimits, SessionId
from mem_sandbox.events import NoOpEventSink, SandboxEvent
from mem_sandbox.policy import AllowAllPolicyEngine, PolicyRequest
from mem_sandbox.secrets import (
    NoSecretBroker,
    SecretAccessRequest,
    SecretDenied,
    SecretRef,
)

SESSION_ID = SessionId(UUID("12345678-1234-5678-1234-567812345678"))
OPERATION_ID = OperationId(UUID("87654321-4321-8765-4321-876543218765"))


@pytest.mark.asyncio
async def test_allow_all_returns_an_explicit_unchanged_decision() -> None:
    limits = OperationLimits(timeout_seconds=5.0, terminal_event_reserve_seconds=0.5)
    request = PolicyRequest(
        session_id=SESSION_ID,
        operation_id=OPERATION_ID,
        operation_kind=OperationKind.EXECUTE,
        path=None,
        command_name=None,
        requested_limits=limits,
    )

    decision = await AllowAllPolicyEngine().evaluate(request)

    assert decision.allowed is True
    assert decision.reason_code == "allow_all"
    assert decision.effective_limits is limits


@pytest.mark.asyncio
async def test_noop_event_sink_accepts_and_discards_a_valid_event() -> None:
    await NoOpEventSink().emit(
        SandboxEvent(
            event_type="operation.started",
            occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
            session_id=SESSION_ID,
            sequence=1,
            operation_id=OPERATION_ID,
            parent_operation_id=None,
            operation_kind=OperationKind.READ_FILE,
            data={"path": "/workspace/file.txt"},
        )
    )


@pytest.mark.asyncio
async def test_no_secret_broker_rejects_every_request_explicitly() -> None:
    request = SecretAccessRequest(
        session_id=SESSION_ID,
        operation_id=OPERATION_ID,
        secret_ref=SecretRef("service-token"),
        command_name=None,
        max_lease_seconds=1.0,
    )

    with pytest.raises(SecretDenied, match="unsupported"):
        await NoSecretBroker().lease(request)
