from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from mem_sandbox.core.identifiers import OperationId, Revision, SessionId
from mem_sandbox.core.models import OperationRequestMetadata, OperationResultMetadata

SESSION_ID = SessionId(UUID("12345678-1234-5678-1234-567812345678"))
OPERATION_ID = OperationId(UUID("87654321-4321-8765-4321-876543218765"))
STARTED_AT = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
COMPLETED_AT = STARTED_AT + timedelta(milliseconds=250)


def test_request_metadata_is_immutable() -> None:
    metadata = OperationRequestMetadata(
        session_id=SESSION_ID,
        operation_id=OPERATION_ID,
    )

    with pytest.raises(FrozenInstanceError):
        setattr(metadata, "session_id", SessionId(UUID(int=1)))  # noqa: B010


def test_result_metadata_exposes_elapsed_time() -> None:
    metadata = OperationResultMetadata(
        session_id=SESSION_ID,
        operation_id=OPERATION_ID,
        workspace_revision=Revision(3),
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )

    assert metadata.elapsed == timedelta(milliseconds=250)


@pytest.mark.parametrize(
    ("started_at", "completed_at"),
    (
        (datetime(2026, 8, 29, 12, 0), COMPLETED_AT),
        (STARTED_AT, datetime(2026, 8, 29, 12, 0)),
    ),
)
def test_result_metadata_rejects_naive_timestamps(
    started_at: datetime,
    completed_at: datetime,
) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        OperationResultMetadata(
            session_id=SESSION_ID,
            operation_id=OPERATION_ID,
            workspace_revision=Revision(0),
            started_at=started_at,
            completed_at=completed_at,
        )


def test_result_metadata_rejects_completion_before_start() -> None:
    with pytest.raises(ValueError, match="before"):
        OperationResultMetadata(
            session_id=SESSION_ID,
            operation_id=OPERATION_ID,
            workspace_revision=Revision(0),
            started_at=COMPLETED_AT,
            completed_at=STARTED_AT,
        )
