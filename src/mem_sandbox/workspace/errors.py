"""Stable workspace-specific domain failures."""

from mem_sandbox.core.errors import (
    ConflictError,
    InvalidRequestError,
    NotFoundError,
    QuotaExceededError,
    UnsupportedOperationError,
)


class InvalidPathError(InvalidRequestError):
    """A path is malformed for the virtual workspace."""

    code = "invalid_path"


class PathOutsideWorkspaceError(InvalidRequestError):
    """A path is outside the configured virtual root."""

    code = "path_outside_workspace"


class PathNotFoundError(NotFoundError):
    """A workspace path does not exist."""

    code = "path_not_found"


class PathAlreadyExistsError(ConflictError):
    """A workspace path already exists."""

    code = "path_already_exists"


class NotAFileError(InvalidRequestError):
    """A requested path is not a file."""

    code = "not_a_file"


class NotADirectoryError(InvalidRequestError):
    """A requested path is not a directory."""

    code = "not_a_directory"


class DirectoryNotEmptyError(ConflictError):
    """A non-empty directory cannot be removed non-recursively."""

    code = "directory_not_empty"


class RootModificationError(InvalidRequestError):
    """The workspace root cannot be removed, copied, or moved."""

    code = "root_modification"


class SamePathError(InvalidRequestError):
    """Source and destination paths must differ."""

    code = "same_path"


class DestinationWithinSourceError(InvalidRequestError):
    """A directory cannot be copied or moved into itself."""

    code = "destination_within_source"


class UnsupportedNodeTypeError(UnsupportedOperationError):
    """The requested filesystem node type is unsupported."""

    code = "unsupported_node_type"


class FileEncodingError(InvalidRequestError):
    """File content is not valid for the requested text encoding."""

    code = "file_encoding_error"


class InvalidRangeError(InvalidRequestError):
    """A requested line range is malformed or outside the file."""

    code = "invalid_range"


class StaleContentError(ConflictError):
    """The current file content does not match the caller's precondition."""

    code = "stale_content"


class InvalidPatchError(InvalidRequestError):
    """A patch is malformed or unsupported."""

    code = "invalid_patch"


class PatchContextMismatchError(ConflictError):
    """A patch hunk does not match current file content."""

    code = "patch_context_mismatch"


class FileSizeLimitExceededError(QuotaExceededError):
    """A file would exceed the configured byte limit."""

    code = "file_size_limit_exceeded"


class WorkspaceSizeLimitExceededError(QuotaExceededError):
    """A mutation would exceed the workspace byte limit."""

    code = "workspace_size_limit_exceeded"


class NodeLimitExceededError(QuotaExceededError):
    """A mutation would exceed the workspace node limit."""

    code = "node_limit_exceeded"


class ReadLimitExceededError(QuotaExceededError):
    """A model-facing read would exceed its response limit."""

    code = "read_limit_exceeded"


class SnapshotTooLargeError(QuotaExceededError):
    """Encoded or decoded snapshot data exceeds configured limits."""

    code = "snapshot_too_large"


class SnapshotCorruptError(InvalidRequestError):
    """Snapshot bytes are malformed or fail integrity validation."""

    code = "snapshot_corrupt"


class SnapshotIncompatibleError(UnsupportedOperationError):
    """The snapshot schema is not supported by this workspace."""

    code = "snapshot_incompatible"
