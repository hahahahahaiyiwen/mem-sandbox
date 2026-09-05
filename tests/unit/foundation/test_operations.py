from dataclasses import FrozenInstanceError

import pytest

from mem_sandbox.core.operations import OperationKind, OperationLimits


def test_supported_operation_kinds_are_exact() -> None:
    assert tuple(OperationKind) == (
        OperationKind.EXECUTE,
        OperationKind.READ_FILE,
        OperationKind.WRITE_FILE,
        OperationKind.APPLY_PATCH,
        OperationKind.READ_BYTES,
        OperationKind.WRITE_BYTES,
        OperationKind.STAT,
        OperationKind.LIST_ENTRIES,
        OperationKind.CREATE_DIRECTORY,
        OperationKind.REMOVE_PATH,
        OperationKind.CREATE_SNAPSHOT,
        OperationKind.RESTORE_SNAPSHOT,
    )
    assert tuple(item.value for item in OperationKind) == (
        "execute",
        "read_file",
        "write_file",
        "apply_patch",
        "read_bytes",
        "write_bytes",
        "stat",
        "list_entries",
        "create_directory",
        "remove_path",
        "create_snapshot",
        "restore_snapshot",
    )


def test_operation_limits_are_immutable_and_validate_the_terminal_reserve() -> None:
    limits = OperationLimits()

    assert limits.timeout_seconds == 30.0
    assert limits.terminal_event_reserve_seconds == 1.0
    with pytest.raises(FrozenInstanceError):
        limits.timeout_seconds = 1.0  # type: ignore[misc]

    for value in (0.0, -1.0, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            OperationLimits(timeout_seconds=value)
        with pytest.raises(ValueError):
            OperationLimits(terminal_event_reserve_seconds=value)
    with pytest.raises(ValueError, match="strictly less"):
        OperationLimits(timeout_seconds=1.0, terminal_event_reserve_seconds=1.0)
