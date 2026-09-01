"""Stable session snapshot failures."""

from mem_sandbox.core import (
    ConflictError,
    InternalSandboxError,
    InvalidRequestError,
    NotFoundError,
    QuotaExceededError,
    UnsupportedOperationError,
)


class SnapshotIdentifierConflict(ConflictError):
    code = "snapshot_identifier_conflict"


class SnapshotNotFound(NotFoundError):
    code = "snapshot_not_found"


class SnapshotCorrupt(InvalidRequestError):
    code = "snapshot_corrupt"


class SnapshotTooLarge(QuotaExceededError):
    code = "snapshot_too_large"


class SnapshotIncompatible(UnsupportedOperationError):
    code = "snapshot_incompatible"


class SnapshotStoreFull(QuotaExceededError):
    code = "snapshot_store_full"


class SnapshotSaveFailed(InternalSandboxError):
    code = "snapshot_save_failed"


class SnapshotLoadFailed(InternalSandboxError):
    code = "snapshot_load_failed"


class SnapshotRestoreFailed(InternalSandboxError):
    code = "snapshot_restore_failed"


SnapshotIdentifierConflictError = SnapshotIdentifierConflict
SnapshotNotFoundError = SnapshotNotFound
SnapshotCorruptError = SnapshotCorrupt
SnapshotTooLargeError = SnapshotTooLarge
SnapshotIncompatibleError = SnapshotIncompatible
