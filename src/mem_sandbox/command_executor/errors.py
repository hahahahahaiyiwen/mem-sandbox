"""Typed terminal failures raised by the command executor."""

from mem_sandbox.core.errors import (
    InternalSandboxError,
    InvalidRequestError,
    OperationCancelledError,
    OperationTimeoutError,
    UnsupportedOperationError,
)


class CommandEmpty(InvalidRequestError):
    """The command text contains no command."""

    code = "command_empty"


class CommandTooLong(InvalidRequestError):
    """The command text exceeds the configured UTF-8 byte limit."""

    code = "command_too_long"


class CommandSyntaxInvalid(InvalidRequestError):
    """The constrained command language input is malformed."""

    code = "command_syntax_invalid"


class CommandSyntaxUnsupported(UnsupportedOperationError):
    """The input uses shell syntax outside the constrained language."""

    code = "command_syntax_unsupported"


class CommandTimeout(OperationTimeoutError):
    """The complete command plan exceeded its timeout."""

    code = "command_timeout"


class CommandCancelled(OperationCancelledError):
    """Command execution was cooperatively or externally cancelled."""

    code = "command_cancelled"


class CommandInternalFailure(InternalSandboxError):
    """An unexpected command collaborator failure prevented completion."""

    code = "command_internal_failure"
